"""Database engine and session management for Aegis's own state."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from .models import Base

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def init_engine(settings: Settings | None = None, *, create: bool = True) -> Engine:
    """Create the engine and, by default, the schema. Safe to call repeatedly."""
    global _engine, _factory
    settings = settings or get_settings()
    connect_args = (
        {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    )
    _engine = create_engine(settings.database_url, future=True, connect_args=connect_args)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    if create:
        Base.metadata.create_all(_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on error."""
    if _factory is None:
        init_engine()
    assert _factory is not None
    session = _factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_for_tests(settings: Settings) -> Engine:
    """Point the module at a throwaway database."""
    global _engine, _factory
    _engine = None
    _factory = None
    return init_engine(settings)
