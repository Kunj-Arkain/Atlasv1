"""
engine.db.settings — Environment Configuration
=================================================
Pydantic-based settings with .env support.

Reads from environment variables with sensible defaults for local dev.
Production values come from Railway/Docker environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DatabaseSettings:
    """PostgreSQL connection settings."""
    host: str = ""
    port: int = 5432
    user: str = ""
    password: str = ""
    name: str = ""
    url: str = ""           # Full URL override (takes precedence)
    pool_size: int = 10
    max_overflow: int = 20
    echo_sql: bool = False  # Log SQL statements

    def __post_init__(self):
        self.host = self.host or os.getenv("DB_HOST", "localhost")
        self.port = int(os.getenv("DB_PORT", str(self.port)))
        self.user = self.user or os.getenv("DB_USER", "agentic")
        self.password = self.password or os.getenv("DB_PASSWORD", "agentic")
        self.name = self.name or os.getenv("DB_NAME", "agentic_engine")
        self.url = self.url or os.getenv("DATABASE_URL", "")
        self.pool_size = int(os.getenv("DB_POOL_SIZE", str(self.pool_size)))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", str(self.max_overflow)))
        self.echo_sql = os.getenv("DB_ECHO_SQL", "").lower() in ("1", "true", "yes")

    @property
    def dsn(self) -> str:
        """Return the connection URL."""
        if self.url:
            # Railway uses DATABASE_URL; normalize driver
            url = self.url
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)
            return url
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def dsn_async(self) -> str:
        """Async driver URL (for future use)."""
        return self.dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


@dataclass
class RedisSettings:
    """Redis connection settings."""
    host: str = ""
    port: int = 6379
    password: str = ""
    db: int = 0
    url: str = ""           # Full URL override

    def __post_init__(self):
        self.host = self.host or os.getenv("REDIS_HOST", "localhost")
        self.port = int(os.getenv("REDIS_PORT", str(self.port)))
        self.password = self.password or os.getenv("REDIS_PASSWORD", "")
        self.db = int(os.getenv("REDIS_DB", str(self.db)))
        self.url = self.url or os.getenv("REDIS_URL", "")

    @property
    def dsn(self) -> str:
        if self.url:
            return self.url
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


@dataclass
class AuthSettings:
    """Authentication settings."""
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agentic-engine"
    jwt_audience: str = "agentic-engine-api"
    jwt_access_ttl_sec: int = 3600       # 1 hour
    jwt_refresh_ttl_sec: int = 604800    # 7 days
    allow_header_auth: bool = False       # Dev mode only

    def __post_init__(self):
        self.jwt_secret = self.jwt_secret or os.getenv("JWT_SECRET", "dev-secret-change-me")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", self.jwt_algorithm)
        self.jwt_issuer = os.getenv("JWT_ISSUER", self.jwt_issuer)
        self.jwt_audience = os.getenv("JWT_AUDIENCE", self.jwt_audience)
        self.jwt_access_ttl_sec = int(os.getenv("JWT_ACCESS_TTL", str(self.jwt_access_ttl_sec)))
        self.jwt_refresh_ttl_sec = int(os.getenv("JWT_REFRESH_TTL", str(self.jwt_refresh_ttl_sec)))
        self.allow_header_auth = os.getenv("ALLOW_HEADER_AUTH", "").lower() in ("1", "true", "yes")


@dataclass
class Settings:
    """Top-level application settings."""
    env: str = ""               # "development", "staging", "production"
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    secrets_master_key: str = ""
    cors_origins: str = ""  # Comma-separated allowed origins

    db: DatabaseSettings = field(default_factory=DatabaseSettings)
    redis: RedisSettings = field(default_factory=RedisSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)

    def __post_init__(self):
        self.env = self.env or os.getenv("APP_ENV", "development")
        self.debug = self.env == "development" or os.getenv("DEBUG", "").lower() in ("1", "true")
        self.api_host = os.getenv("API_HOST", self.api_host)
        self.api_port = int(os.getenv("PORT", os.getenv("API_PORT", str(self.api_port))))
        self.log_level = os.getenv("LOG_LEVEL", self.log_level)
        self.secrets_master_key = os.getenv("SECRETS_MASTER_KEY", "dev-master-key-change-me")
        self.cors_origins = os.getenv("CORS_ORIGINS", self.cors_origins)

        # Initialize sub-settings if they're defaults
        if isinstance(self.db, DatabaseSettings) and not self.db.host:
            self.db = DatabaseSettings()
        if isinstance(self.redis, RedisSettings) and not self.redis.host:
            self.redis = RedisSettings()
        if isinstance(self.auth, AuthSettings) and not self.auth.jwt_secret:
            self.auth = AuthSettings()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"


# ── Singleton ────────────────────────────────────────────────

_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create the global settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings():
    """Reset settings (for testing)."""
    global _settings
    _settings = None
