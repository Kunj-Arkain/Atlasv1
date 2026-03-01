"""
engine.db — Database Layer
============================
Phase 0A: Persistence for the in-memory engine.

Usage:
    from engine.db.settings import get_settings
    from engine.db.session import get_session
    from engine.db.repositories import UserRepo, JobRepo

    with get_session() as session:
        user_repo = UserRepo(session)
        user = user_repo.get("user_123")

Note: SQLAlchemy is required for session/models/repos.
      Settings module works standalone (stdlib only).
"""

# Settings always available (no external deps)
from engine.db.settings import Settings, get_settings, reset_settings

__all__ = ["Settings", "get_settings", "reset_settings"]

# SQLAlchemy-dependent modules loaded on demand
try:
    from engine.db.session import (
        get_engine, get_session, get_session_factory,
        check_db_connection, create_all_tables, drop_all_tables,
        reset_singletons,
    )
    from engine.db.models import Base

    __all__ += [
        "Base", "get_engine", "get_session", "get_session_factory",
        "check_db_connection", "create_all_tables", "drop_all_tables",
        "reset_singletons",
    ]
except ImportError:
    pass  # SQLAlchemy not installed — settings still work
