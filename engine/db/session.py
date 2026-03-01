"""
engine.db.session — Database Session Management
==================================================
Provides SQLAlchemy engine + session factory.

Usage:
    from engine.db.session import get_engine, get_session, SessionLocal

    # As context manager
    with get_session() as session:
        session.query(...)

    # As dependency (FastAPI)
    def get_db():
        with get_session() as session:
            yield session
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from engine.db.settings import get_settings


# ── Module-level singletons ──────────────────────────────────

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def get_engine(url: Optional[str] = None) -> Engine:
    """Get or create the SQLAlchemy engine singleton."""
    global _engine
    if _engine is None:
        settings = get_settings()
        dsn = url or settings.db.dsn
        _engine = create_engine(
            dsn,
            pool_size=settings.db.pool_size,
            max_overflow=settings.db.max_overflow,
            echo=settings.db.echo_sql,
            pool_pre_ping=True,      # Reconnect on stale connections
            pool_recycle=3600,        # Recycle connections every hour
        )
    return _engine


def get_session_factory(engine: Optional[Engine] = None) -> sessionmaker:
    """Get or create the session factory singleton."""
    global _session_factory
    if _session_factory is None:
        eng = engine or get_engine()
        _session_factory = sessionmaker(bind=eng, expire_on_commit=False)
    return _session_factory


@contextmanager
def get_session(engine: Optional[Engine] = None) -> Generator[Session, None, None]:
    """Context manager that provides a transactional session.

    Commits on clean exit, rolls back on exception.
    """
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_db_connection(engine: Optional[Engine] = None) -> bool:
    """Verify database connectivity. Returns True if healthy."""
    try:
        eng = engine or get_engine()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def create_all_tables(engine: Optional[Engine] = None):
    """Create all tables (for testing / dev bootstrap).

    Production should use Alembic migrations instead.
    """
    from engine.db.models import Base
    eng = engine or get_engine()
    Base.metadata.create_all(eng)


def drop_all_tables(engine: Optional[Engine] = None):
    """Drop all tables (for testing only)."""
    from engine.db.models import Base
    eng = engine or get_engine()
    Base.metadata.drop_all(eng)


def reset_singletons():
    """Reset engine and session factory (for testing)."""
    global _engine, _session_factory
    if _engine:
        _engine.dispose()
    _engine = None
    _session_factory = None
