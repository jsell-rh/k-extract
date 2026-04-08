"""Tests for SQLAlchemy models and database setup."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from k_extract.pipeline.database import (
    Base,
    EnvironmentFingerprint,
    Job,
    JobStatus,
    create_engine_with_wal,
    create_session_factory,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def session_factory(db_path: Path):
    engine = create_engine_with_wal(db_path)
    return create_session_factory(engine)


class TestEngine:
    def test_wal_mode_enabled(self, db_path: Path) -> None:
        engine = create_engine_with_wal(db_path)
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            assert mode == "wal"

    def test_tables_created(self, session_factory) -> None:
        session = session_factory()
        # Both tables should exist and be queryable
        session.execute(text("SELECT count(*) FROM jobs"))
        session.execute(text("SELECT count(*) FROM environment_fingerprints"))
        session.close()


class TestJobModel:
    def test_create_and_load(self, session_factory) -> None:
        session = session_factory()
        now = datetime.now(UTC)
        job = Job(
            job_id="test-001",
            order=0,
            data_source="test-source",
            files=["file1.md", "file2.md"],
            file_count=2,
            total_characters=1000,
            status=JobStatus.PENDING,
            created_at=now,
            attempt=0,
        )
        session.add(job)
        session.commit()

        loaded = session.get(Job, "test-001")
        assert loaded is not None
        assert loaded.job_id == "test-001"
        assert loaded.order == 0
        assert loaded.data_source == "test-source"
        assert loaded.files == ["file1.md", "file2.md"]
        assert loaded.file_count == 2
        assert loaded.total_characters == 1000
        assert loaded.status == JobStatus.PENDING
        assert loaded.started_at is None
        assert loaded.completed_at is None
        assert loaded.agent_instance_id is None
        assert loaded.attempt == 0
        assert loaded.error_message is None
        session.close()

    def test_nullable_fields_populated(self, session_factory) -> None:
        session = session_factory()
        now = datetime.now(UTC)
        job = Job(
            job_id="test-002",
            order=1,
            data_source="test-source",
            files=["file.md"],
            file_count=1,
            total_characters=500,
            status=JobStatus.IN_PROGRESS,
            created_at=now,
            started_at=now,
            agent_instance_id="worker-1",
            attempt=1,
        )
        session.add(job)
        session.commit()

        loaded = session.get(Job, "test-002")
        assert loaded is not None
        assert loaded.started_at is not None
        assert loaded.agent_instance_id == "worker-1"
        assert loaded.attempt == 1
        session.close()

    def test_json_files_roundtrip(self, session_factory) -> None:
        session = session_factory()
        now = datetime.now(UTC)
        files = ["source/dir1/a.md", "source/dir2/b.md", "source/c.md"]
        job = Job(
            job_id="test-003",
            order=0,
            data_source="source",
            files=files,
            file_count=3,
            total_characters=3000,
            status=JobStatus.PENDING,
            created_at=now,
            attempt=0,
        )
        session.add(job)
        session.commit()

        loaded = session.get(Job, "test-003")
        assert loaded is not None
        assert loaded.files == files
        session.close()

    def test_status_values(self) -> None:
        assert JobStatus.PENDING == "pending"
        assert JobStatus.IN_PROGRESS == "in_progress"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.FAILED == "failed"


class TestEnvironmentFingerprintModel:
    def test_create_and_load(self, session_factory) -> None:
        session = session_factory()
        now = datetime.now(UTC)
        fp = EnvironmentFingerprint(
            fingerprint="abc123def456",
            created_at=now,
            config_hash="hash789",
            model_id="claude-sonnet-4-6",
        )
        session.add(fp)
        session.commit()

        loaded = session.get(EnvironmentFingerprint, "abc123def456")
        assert loaded is not None
        assert loaded.fingerprint == "abc123def456"
        assert loaded.config_hash == "hash789"
        assert loaded.model_id == "claude-sonnet-4-6"
        session.close()
