"""SQLite database engine and session factory connection manager."""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.database.schema import Base

logger = logging.getLogger(__name__)

_global_engine: Engine | None = None
_global_session_factory: sessionmaker[Session] | None = None


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    """Enforces SQLite foreign key constraints and WAL journal mode where supported."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception as err:
        logger.debug("Failed to set PRAGMA foreign_keys=ON: %s", err)


def get_engine(
    url: str | None = None,
    echo: bool | None = None,
    is_testing: bool = False,
) -> Engine:
    """Creates or returns the configured SQLAlchemy database engine.

    Args:
        url: Database connection URL (defaults to settings.DB_URL).
        echo: Enable query SQL echoing (defaults to settings.DB_ECHO).
        is_testing: If True, uses StaticPool for in-memory SQLite isolation.

    Returns:
        SQLAlchemy Engine instance.
    """
    global _global_engine, _global_session_factory

    db_url = url or settings.DB_URL
    echo_sql = settings.DB_ECHO if echo is None else echo

    if _global_engine is not None and url is None:
        return _global_engine

    connect_args: dict[str, Any] = {}
    engine_kwargs: dict[str, Any] = {"echo": echo_sql}

    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if is_testing or db_url in ("sqlite://", "sqlite:///:memory:"):
            engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(db_url, connect_args=connect_args, **engine_kwargs)

    if url is None:
        _global_engine = engine
        _global_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    return engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Returns a sessionmaker instance bound to the provided or default engine."""
    global _global_session_factory
    if engine is not None:
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)
    if _global_session_factory is None:
        get_engine()
    assert _global_session_factory is not None
    return _global_session_factory


def init_db(engine: Engine | None = None) -> None:
    """Creates all database tables defined in the metadata schema.

    Args:
        engine: Target database engine (defaults to configured global engine).
    """
    target_engine = engine or get_engine()
    Base.metadata.create_all(bind=target_engine)
    logger.info("Initialized database schema on engine %s", target_engine.url)


def reset_engine() -> None:
    """Disposes active database engine and resets singleton references (for test suites)."""
    global _global_engine, _global_session_factory
    if _global_engine is not None:
        _global_engine.dispose()
        _global_engine = None
        _global_session_factory = None


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency providing a transactional SQLAlchemy session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "get_db",
    "get_engine",
    "get_session_factory",
    "init_db",
    "reset_engine",
]
