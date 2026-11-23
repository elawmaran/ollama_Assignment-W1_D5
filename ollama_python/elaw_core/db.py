"""Database engine and sessions (PostgreSQL in production, SQLite for dev/tests)."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from elaw_core import config


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value):
    """SQLite returns naive datetimes; treat them as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = config.settings.database_url
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            path = url.split("sqlite:///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            kwargs = {"connect_args": {"check_same_thread": False}}
        else:
            kwargs.update(pool_size=5, max_overflow=5)
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _fk_on(dbapi_conn, _):  # SQLite ignores foreign keys unless asked
                dbapi_conn.execute("PRAGMA foreign_keys=ON")
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine():
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = _Session = None


@contextmanager
def session_scope():
    get_engine()
    db = _Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    """FastAPI dependency."""
    with session_scope() as db:
        yield db
