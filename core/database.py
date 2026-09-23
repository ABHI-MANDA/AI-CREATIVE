"""Production persistence adapter.

The application keeps a JSON-file fallback for local development/tests, but when
DATABASE_URL is configured it stores durable records in PostgreSQL (Supabase is
recommended). SQLAlchemy keeps the application portable between local SQLite
and hosted PostgreSQL.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, Text, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.types import JSON

from . import config

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_ENGINE = None


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "creative_records"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload: Mapped[Any] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or config.DATABASE_URL or "").strip()


def enabled() -> bool:
    return bool(database_url())


def engine():
    global _ENGINE
    if not enabled():
        return None
    with _LOCK:
        if _ENGINE is None:
            url = database_url()
            # Supabase sometimes exposes postgres:// URLs; SQLAlchemy expects
            # postgresql:// or a psycopg dialect.
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url[len("postgres://") :]
            elif url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url[len("postgresql://") :]
            connect_args = {}
            if url.startswith("postgresql+psycopg://"):
                connect_args["prepare_threshold"] = 0
            _ENGINE = create_engine(
                url,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
                max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
                connect_args=connect_args,
            )
        return _ENGINE


def init_db() -> bool:
    eng = engine()
    if eng is None:
        return False
    Base.metadata.create_all(eng)
    return True


def ping() -> bool:
    eng = engine()
    if eng is None:
        return False
    try:
        with eng.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False


def load_collection(name: str, default: dict) -> dict:
    """Load a collection by logical name, falling back to the JSON store."""
    if not enabled():
        return default
    try:
        init_db()
        with Session(engine()) as session:
            rows = session.scalars(select(Record).where(Record.key == name)).all()
            if not rows:
                return default
            # A collection is represented by a single record_id='root'.
            row = next((r for r in rows if r.record_id == "root"), rows[0])
            return row.payload if isinstance(row.payload, dict) else default
    except Exception:
        logger.exception("Database read failed for collection %s", name)
        # Do not silently destroy production data. Returning default allows the
        # app to start, while health exposes the DB problem.
        return default


def save_collection(name: str, data: dict) -> bool:
    if not enabled():
        return False
    try:
        init_db()
        now = datetime.now(timezone.utc)
        with Session(engine()) as session:
            row = session.get(Record, {"key": name, "record_id": "root"})
            if row is None:
                row = Record(key=name, record_id="root", payload=data, updated_at=now)
                session.add(row)
            else:
                row.payload = data
                row.updated_at = now
            session.commit()
        return True
    except Exception:
        logger.exception("Database write failed for collection %s", name)
        return False


def migrate_json_collection(name: str, data: dict) -> bool:
    return save_collection(name, data)
