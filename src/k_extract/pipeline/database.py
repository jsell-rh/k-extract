"""Engine, session factory, and SQLAlchemy models for the extraction database.

Provides Job and EnvironmentFingerprint models, a SQLite engine with
WAL mode for concurrent access, and auto table creation.
"""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


class JobStatus(enum.StrEnum):
    """Job state machine states (spec: job-lifecycle.md)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""


class Job(Base):
    """Extraction job record (spec: job-lifecycle.md, Job Data Model).

    States: pending -> in_progress -> completed | failed
    """

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    data_source: Mapped[str] = mapped_column(String, nullable=False)
    files: Mapped[list] = mapped_column(JSON, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_characters: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=JobStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    agent_instance_id: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)


class EnvironmentFingerprint(Base):
    """Environment fingerprint for run resumability.

    Tracks config hash and model ID to determine if a previous run
    can be resumed.
    """

    __tablename__ = "environment_fingerprints"

    fingerprint: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    config_hash: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False)


def create_engine_with_wal(db_path: str | Path) -> Engine:
    """Create a SQLite engine with WAL mode for concurrent access."""
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(
        dbapi_connection: Any,
        connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory, creating all tables on first use."""
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
