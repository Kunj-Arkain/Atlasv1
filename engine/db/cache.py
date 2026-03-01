"""
engine.db.cache — Redis Config Cache
=======================================
Phase 1: All ACP config reads go through Redis cache.

Pattern:
  1. Check Redis for key (TTL 30s)
  2. Cache miss → query Postgres → write to Redis
  3. On config update → invalidate cache key

This ensures pipeline runs see config changes within 30 seconds
without hitting Postgres on every LLM call.

Works without Redis (graceful fallback to direct DB reads).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger("engine.cache")

T = TypeVar("T")

# Default TTL for config cache entries
CONFIG_CACHE_TTL = 30  # seconds


class ConfigCache:
    """Redis-backed read-through cache for ACP configuration.

    Thread-safe. Gracefully degrades if Redis is unavailable.

    Usage:
        cache = ConfigCache(redis_url="redis://localhost:6379/0")

        # Read-through: cache miss calls loader, stores result
        config = cache.get_or_load(
            key="agent_config:ws1:analyzer",
            loader=lambda: repo.get("ws1", "analyzer"),
        )

        # Invalidate on update
        cache.invalidate("agent_config:ws1:analyzer")

        # Invalidate all keys for a workspace
        cache.invalidate_pattern("agent_config:ws1:*")
    """

    def __init__(self, redis_url: str = "", ttl: int = CONFIG_CACHE_TTL):
        self._ttl = ttl
        self._redis = None
        self._available = False

        if redis_url:
            try:
                import redis
                self._redis = redis.Redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                self._redis.ping()
                self._available = True
                logger.info(f"Config cache connected to Redis")
            except Exception as e:
                logger.warning(f"Redis not available, cache disabled: {e}")
                self._redis = None
                self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def get_or_load(
        self, key: str, loader: Callable[[], Optional[T]],
        ttl: Optional[int] = None,
    ) -> Optional[T]:
        """Read-through cache. Returns cached value or calls loader."""
        # Try cache first
        cached = self._get(key)
        if cached is not None:
            return cached

        # Cache miss — load from DB
        value = loader()
        if value is not None:
            self._set(key, value, ttl or self._ttl)
        return value

    def get_or_load_list(
        self, key: str, loader: Callable[[], List[T]],
        ttl: Optional[int] = None,
    ) -> List[T]:
        """Read-through cache for list results."""
        cached = self._get(key)
        if cached is not None:
            return cached

        value = loader()
        self._set(key, value, ttl or self._ttl)
        return value

    def invalidate(self, key: str):
        """Delete a specific cache key."""
        if not self._available:
            return
        try:
            self._redis.delete(key)
        except Exception as e:
            logger.warning(f"Cache invalidate failed for {key}: {e}")

    def invalidate_pattern(self, pattern: str):
        """Delete all keys matching a pattern (e.g., 'agent_config:ws1:*')."""
        if not self._available:
            return
        try:
            keys = list(self._redis.scan_iter(match=pattern, count=100))
            if keys:
                self._redis.delete(*keys)
                logger.debug(f"Cache invalidated {len(keys)} keys for pattern {pattern}")
        except Exception as e:
            logger.warning(f"Cache invalidate_pattern failed for {pattern}: {e}")

    def invalidate_workspace(self, workspace_id: str):
        """Invalidate all config cache for a workspace."""
        prefixes = [
            f"agent_config:{workspace_id}:*",
            f"model_route:{workspace_id}:*",
            f"tool_policy:{workspace_id}:*",
            f"pipeline_def:{workspace_id}:*",
            f"strategy_weights:{workspace_id}:*",
        ]
        for p in prefixes:
            self.invalidate_pattern(p)

    def _get(self, key: str) -> Optional[Any]:
        if not self._available:
            return None
        try:
            raw = self._redis.get(key)
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Cache read failed for {key}: {e}")
        return None

    def _set(self, key: str, value: Any, ttl: int):
        if not self._available:
            return
        try:
            self._redis.setex(key, ttl, json.dumps(value, default=str))
        except Exception as e:
            logger.warning(f"Cache write failed for {key}: {e}")


# ═══════════════════════════════════════════════════════════════
# CACHE KEY BUILDERS
# ═══════════════════════════════════════════════════════════════

def agent_config_key(workspace_id: str, agent_name: str) -> str:
    return f"agent_config:{workspace_id}:{agent_name}"


def agent_configs_list_key(workspace_id: str) -> str:
    return f"agent_configs:{workspace_id}:_all"


def model_route_key(workspace_id: str, tier: str) -> str:
    return f"model_route:{workspace_id}:{tier}"


def model_routes_list_key(workspace_id: str) -> str:
    return f"model_routes:{workspace_id}:_all"


def tool_policy_key(workspace_id: str, tool_name: str, agent_name: str = "*") -> str:
    return f"tool_policy:{workspace_id}:{agent_name}:{tool_name}"


def tool_policies_list_key(workspace_id: str) -> str:
    return f"tool_policies:{workspace_id}:_all"


def pipeline_def_key(workspace_id: str, name: str) -> str:
    return f"pipeline_def:{workspace_id}:{name}"


def pipeline_defs_list_key(workspace_id: str) -> str:
    return f"pipeline_defs:{workspace_id}:_all"


def strategy_weights_key(workspace_id: str) -> str:
    return f"strategy_weights:{workspace_id}:current"


# ═══════════════════════════════════════════════════════════════
# NO-OP CACHE (for testing without Redis)
# ═══════════════════════════════════════════════════════════════

class NoOpCache(ConfigCache):
    """Cache that never caches. For testing."""

    def __init__(self):
        self._ttl = 0
        self._redis = None
        self._available = False

    def get_or_load(self, key, loader, ttl=None):
        return loader()

    def get_or_load_list(self, key, loader, ttl=None):
        return loader()
