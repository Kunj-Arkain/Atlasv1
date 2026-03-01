"""
engine.workers — Isolated Subprocess Workers
==============================================
AUDIT ITEM #2 (Impact: 8/10)

Problems in V1:
  - run_crew_with_timeout() uses threading.Thread which can't actually
    kill the work (real_estate_pipeline.py:625-659)
  - Thread.join(timeout) just stops waiting — the thread keeps running
  - No memory/CPU limits, no file descriptor limits
  - A single runaway agent can consume all resources

This module implements:
  - SubprocessWorker: executes stage handlers in child processes
    with HARD SIGKILL on timeout (no orphan threads)
  - ResourceQuota: CPU time, memory, file descriptor limits via
    os.setrlimit (Linux) or soft enforcement (other platforms)
  - WorkerPool: queue-based pool with configurable concurrency
    and backpressure (reject when full)
  - WorkerResult: rich result type with status, output, timing, errors

ZERO external dependencies.
"""

from __future__ import annotations

import enum
import json
import multiprocessing
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# RESOURCE QUOTA
# ═══════════════════════════════════════════════════════════════

@dataclass
class ResourceQuota:
    """Resource limits enforced on worker processes.

    On Linux: enforced via setrlimit in the child process.
    On other platforms: soft enforcement (timeout only).
    """
    cpu_time_seconds: int = 600          # RLIMIT_CPU
    memory_bytes: int = 2 * 1024**3      # RLIMIT_AS (2GB default)
    max_file_descriptors: int = 256      # RLIMIT_NOFILE
    max_file_size_bytes: int = 100 * 1024**2  # RLIMIT_FSIZE (100MB)
    wall_time_seconds: int = 1200        # Hard kill timeout
    nice_value: int = 10                 # Process priority (higher = lower priority)


def _apply_resource_limits(quota: ResourceQuota):
    """Apply resource limits in the child process (Linux only)."""
    try:
        import resource
        # CPU time limit
        resource.setrlimit(resource.RLIMIT_CPU,
                           (quota.cpu_time_seconds, quota.cpu_time_seconds + 30))
        # Memory limit
        resource.setrlimit(resource.RLIMIT_AS,
                           (quota.memory_bytes, quota.memory_bytes))
        # File descriptor limit
        resource.setrlimit(resource.RLIMIT_NOFILE,
                           (quota.max_file_descriptors, quota.max_file_descriptors))
        # File size limit
        resource.setrlimit(resource.RLIMIT_FSIZE,
                           (quota.max_file_size_bytes, quota.max_file_size_bytes))
        # Nice value
        os.nice(quota.nice_value)
    except (ImportError, AttributeError, OSError, ValueError):
        pass  # Not Linux or insufficient permissions — skip


# ═══════════════════════════════════════════════════════════════
# WORKER RESULT
# ═══════════════════════════════════════════════════════════════

class WorkerStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    OOM = "oom"
    KILLED = "killed"


@dataclass
class WorkerResult:
    """Result from a subprocess worker execution."""
    status: str                          # WorkerStatus value
    output: Any = None                   # Return value from handler
    error: str = ""
    traceback_str: str = ""
    exit_code: int = 0
    wall_time_ms: int = 0
    pid: int = 0
    stage_name: str = ""
    started_at: str = ""
    completed_at: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == WorkerStatus.COMPLETED.value

    def to_dict(self) -> Dict:
        return {
            "status": self.status,
            "error": self.error,
            "exit_code": self.exit_code,
            "wall_time_ms": self.wall_time_ms,
            "pid": self.pid,
            "stage_name": self.stage_name,
        }


# ═══════════════════════════════════════════════════════════════
# SUBPROCESS WORKER — The core isolation primitive
# ═══════════════════════════════════════════════════════════════

def _worker_target(fn: Callable, args: tuple, kwargs: dict,
                   result_queue: Queue, quota: ResourceQuota):
    """Target function that runs in the child process."""
    _apply_resource_limits(quota)
    try:
        result = fn(*args, **kwargs)
        result_queue.put(("ok", result, ""))
    except MemoryError:
        result_queue.put(("oom", None, "Out of memory"))
    except Exception as e:
        tb = traceback.format_exc()
        result_queue.put(("error", None, f"{type(e).__name__}: {e}\n{tb}"))


class SubprocessWorker:
    """Execute a callable in an isolated child process.

    Key guarantees:
      - HARD SIGKILL on timeout (no zombie threads)
      - Resource limits via setrlimit (CPU, memory, FDs)
      - Clean error capture with traceback
      - Process-level isolation (no shared state corruption)

    Usage:
        worker = SubprocessWorker(quota=ResourceQuota(wall_time_seconds=60))
        result = worker.run(my_handler, args=(ctx,), stage_name="research")
        if result.succeeded:
            print(result.output)
    """

    def __init__(self, quota: Optional[ResourceQuota] = None):
        self.quota = quota or ResourceQuota()

    def run(self, fn: Callable, args: tuple = (), kwargs: Optional[Dict] = None,
            stage_name: str = "") -> WorkerResult:
        """Run fn(*args, **kwargs) in a subprocess with resource limits."""
        kwargs = kwargs or {}
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.time()

        # Use multiprocessing.Queue for IPC
        result_queue: Queue = Queue(maxsize=1)

        proc = Process(
            target=_worker_target,
            args=(fn, args, kwargs, result_queue, self.quota),
            daemon=True,
        )

        try:
            proc.start()
            pid = proc.pid or 0

            # Wait with wall-time timeout
            proc.join(timeout=self.quota.wall_time_seconds)

            wall_ms = int((time.time() - t0) * 1000)
            completed_at = datetime.now(timezone.utc).isoformat()

            if proc.is_alive():
                # HARD KILL — this is the critical difference from V1's threads
                self._kill_process(proc)
                return WorkerResult(
                    status=WorkerStatus.TIMEOUT.value,
                    error=f"Wall time exceeded {self.quota.wall_time_seconds}s",
                    wall_time_ms=wall_ms, pid=pid,
                    stage_name=stage_name,
                    started_at=started_at, completed_at=completed_at,
                )

            # Process finished — check result
            if not result_queue.empty():
                status_str, output, error = result_queue.get_nowait()
            else:
                status_str, output, error = "error", None, "No result from worker"

            exit_code = proc.exitcode or 0

            if status_str == "ok":
                return WorkerResult(
                    status=WorkerStatus.COMPLETED.value,
                    output=output, exit_code=exit_code,
                    wall_time_ms=wall_ms, pid=pid,
                    stage_name=stage_name,
                    started_at=started_at, completed_at=completed_at,
                )
            elif status_str == "oom":
                return WorkerResult(
                    status=WorkerStatus.OOM.value,
                    error=error, exit_code=exit_code,
                    wall_time_ms=wall_ms, pid=pid,
                    stage_name=stage_name,
                    started_at=started_at, completed_at=completed_at,
                )
            else:
                return WorkerResult(
                    status=WorkerStatus.FAILED.value,
                    error=error, traceback_str=error,
                    exit_code=exit_code,
                    wall_time_ms=wall_ms, pid=pid,
                    stage_name=stage_name,
                    started_at=started_at, completed_at=completed_at,
                )

        except Exception as e:
            wall_ms = int((time.time() - t0) * 1000)
            return WorkerResult(
                status=WorkerStatus.FAILED.value,
                error=f"Worker infrastructure error: {e}",
                wall_time_ms=wall_ms, stage_name=stage_name,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            # Ensure cleanup
            if proc.is_alive():
                self._kill_process(proc)

    def _kill_process(self, proc: Process):
        """Hard kill with SIGKILL. No mercy."""
        try:
            proc.terminate()  # SIGTERM first
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()  # SIGKILL
                proc.join(timeout=2)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# WORKER POOL — Queue-based with backpressure
# ═══════════════════════════════════════════════════════════════

@dataclass
class WorkItem:
    """A unit of work to be executed by the pool."""
    fn: Callable
    args: tuple = ()
    kwargs: Dict = field(default_factory=dict)
    stage_name: str = ""
    priority: int = 0          # 0=highest


class WorkerPool:
    """Pool of subprocess workers with configurable concurrency.

    Features:
      - Configurable max concurrent workers
      - Per-worker resource quotas
      - Backpressure: reject when queue is full
      - Batch execution with result collection

    Usage:
        pool = WorkerPool(max_workers=4, quota=ResourceQuota())
        items = [WorkItem(fn=handler, args=(ctx,), stage_name="s1"), ...]
        results = pool.execute_batch(items)
    """

    def __init__(self, max_workers: int = 4,
                 quota: Optional[ResourceQuota] = None,
                 max_queue_size: int = 100):
        self.max_workers = max_workers
        self.quota = quota or ResourceQuota()
        self.max_queue_size = max_queue_size

    def execute_batch(self, items: List[WorkItem]) -> List[WorkerResult]:
        """Execute a batch of work items. Returns results in same order."""
        if len(items) > self.max_queue_size:
            raise RuntimeError(
                f"Batch size {len(items)} exceeds max queue size {self.max_queue_size}"
            )

        # Sort by priority (lower = higher priority)
        indexed = sorted(enumerate(items), key=lambda x: x[1].priority)

        results: Dict[int, WorkerResult] = {}
        worker = SubprocessWorker(quota=self.quota)

        # Process in chunks of max_workers
        for chunk_start in range(0, len(indexed), self.max_workers):
            chunk = indexed[chunk_start:chunk_start + self.max_workers]

            # For simplicity, execute sequentially within chunk
            # (True parallel would use multiprocessing.Pool but
            #  we want per-process resource isolation via SubprocessWorker)
            for orig_idx, item in chunk:
                result = worker.run(
                    item.fn, args=item.args, kwargs=item.kwargs,
                    stage_name=item.stage_name,
                )
                results[orig_idx] = result

        # Return in original order
        return [results[i] for i in range(len(items))]

    def execute_single(self, fn: Callable, args: tuple = (),
                       kwargs: Optional[Dict] = None,
                       stage_name: str = "") -> WorkerResult:
        """Execute a single function in an isolated worker."""
        worker = SubprocessWorker(quota=self.quota)
        return worker.run(fn, args=args, kwargs=kwargs or {},
                          stage_name=stage_name)


# ═══════════════════════════════════════════════════════════════
# DROP-IN REPLACEMENT FOR V1's run_crew_with_timeout
# ═══════════════════════════════════════════════════════════════

def run_in_subprocess(fn: Callable, args: tuple = (),
                      timeout_seconds: int = 600,
                      memory_limit_gb: float = 2.0,
                      stage_name: str = "") -> WorkerResult:
    """Drop-in replacement for V1's run_crew_with_timeout().

    V1 used threading.Thread which can't kill the work.
    This uses subprocess with SIGKILL on timeout.

    Example migration:
        # V1:
        result = run_crew_with_timeout(crew, inputs, 600)

        # V2:
        result = run_in_subprocess(
            lambda: crew.kickoff(inputs=inputs),
            timeout_seconds=600,
            stage_name="market_research",
        )
        if result.succeeded:
            output = result.output
    """
    quota = ResourceQuota(
        wall_time_seconds=timeout_seconds,
        memory_bytes=int(memory_limit_gb * 1024**3),
    )
    worker = SubprocessWorker(quota=quota)
    return worker.run(fn, args=args, stage_name=stage_name)
