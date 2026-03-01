"""
engine.worker_main — RQ Worker Entry Point
=============================================
Phase 0B: Redis-backed job queue worker.

Connects to Redis, listens for jobs on configured queues,
and executes them using the existing engine runtime.

Run:
    python -m engine.worker_main                # Default queues
    python -m engine.worker_main --queues high default low  # Custom
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

logger = logging.getLogger("engine.worker")


def create_worker(queue_names: list[str] | None = None):
    """Create and configure an RQ worker."""
    from redis import Redis
    from rq import Worker, Queue

    from engine.db.settings import get_settings

    settings = get_settings()
    redis_conn = Redis.from_url(settings.redis.dsn)

    queues = [Queue(name, connection=redis_conn) for name in (queue_names or ["default", "high", "low"])]

    logger.info(
        f"Worker starting [queues={[q.name for q in queues]}, "
        f"redis={settings.redis.host}:{settings.redis.port}]"
    )

    worker = Worker(queues, connection=redis_conn)
    return worker


def run_worker(queue_names: list[str] | None = None):
    """Start the worker loop."""
    worker = create_worker(queue_names)
    worker.work(with_scheduler=True)


# ═══════════════════════════════════════════════════════════════
# JOB FUNCTIONS — these are what get enqueued
# ═══════════════════════════════════════════════════════════════

def execute_pipeline_job(job_id: str):
    """Execute a pipeline job. Called by the RQ worker.

    This is the bridge between the queue and the engine runtime.
    """
    from engine.db.session import get_session
    from engine.db.repositories import JobRepo, AuditLogRepo
    from engine.tenants import JobStatus

    logger.info(f"Executing job {job_id}")

    with get_session() as session:
        job_repo = JobRepo(session)
        audit_repo = AuditLogRepo(session)

        job = job_repo.get(job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        if job.status != JobStatus.QUEUED.value:
            logger.warning(f"Job {job_id} is {job.status}, skipping")
            return

        # Mark as running
        job_repo.update_status(job_id, JobStatus.RUNNING.value)
        audit_repo.append(
            workspace_id=job.workspace_id,
            action="job.started",
            resource=f"job:{job_id}",
            outcome="success",
            user_id=job.user_id,
            details={"pipeline_type": job.pipeline_type},
        )

    # ── Execute the pipeline ─────────────────────────────────
    # TODO: Phase 2+ will wire actual pipeline runners here.
    # For now, this is a placeholder that demonstrates the
    # job lifecycle works end-to-end.
    try:
        result = _run_pipeline(job_id, job.pipeline_type, job.config)
        status = JobStatus.COMPLETED.value
        error = ""
    except Exception as e:
        logger.exception(f"Job {job_id} failed: {e}")
        result = None
        status = JobStatus.FAILED.value
        error = str(e)

    # ── Update final status ──────────────────────────────────
    with get_session() as session:
        job_repo = JobRepo(session)
        audit_repo = AuditLogRepo(session)

        job_repo.update_status(job_id, status, error=error, result=result)
        audit_repo.append(
            workspace_id=job.workspace_id,
            action=f"job.{status}",
            resource=f"job:{job_id}",
            outcome="success" if status == "completed" else "error",
            user_id=job.user_id,
            details={"result_summary": str(result)[:200] if result else error[:200]},
        )

    logger.info(f"Job {job_id} → {status}")


def _run_pipeline(job_id: str, pipeline_type: str, config: dict) -> dict:
    """Dispatch to the appropriate pipeline runner.

    This will be expanded as phases are built:
      - Phase 2: financial tool runs
      - Phase 3: EGM data ingestion
      - Phase 4: EGM prediction
      - Phase 5: contract simulation
      - Phase 6: RE capital filter
    """
    runners = {
        "health_check": _pipeline_health_check,
        # Future pipelines registered here
    }

    runner = runners.get(pipeline_type)
    if not runner:
        raise ValueError(f"Unknown pipeline type: {pipeline_type}")

    return runner(job_id, config)


def _pipeline_health_check(job_id: str, config: dict) -> dict:
    """Trivial pipeline for testing the worker end-to-end."""
    return {
        "status": "ok",
        "job_id": job_id,
        "message": "Worker pipeline execution verified",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from engine.db.settings import get_settings

    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="AgenticEngine Worker")
    parser.add_argument(
        "--queues", nargs="+", default=["high", "default", "low"],
        help="Queue names to listen on (default: high default low)",
    )
    args = parser.parse_args()

    run_worker(args.queues)
