"""Tests for SHA256 hashing, fingerprint computation, and resume logic."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from k_extract.pipeline.database import Base, EnvironmentFingerprint
from k_extract.pipeline.fingerprint import (
    ResumeAction,
    compute_fingerprint,
    evaluate_resume,
    hash_file,
    hash_files_parallel,
    store_fingerprint,
)


@pytest.fixture()
def db_session() -> Session:
    """Create an in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session  # type: ignore[misc]
    session.close()


class TestHashFile:
    def test_hash_matches_expected(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        content = b"hello world"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()

        path_str, digest = hash_file(f)
        assert digest == expected
        assert path_str == str(f)

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()

        _, digest = hash_file(f)
        assert digest == expected

    def test_binary_file(self, tmp_path: Path) -> None:
        f = tmp_path / "binary.bin"
        data = bytes(range(256))
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()

        _, digest = hash_file(f)
        assert digest == expected

    def test_returns_path_as_string(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("data")

        path_str, _ = hash_file(str(f))
        assert path_str == str(f)


class TestHashFilesParallel:
    def test_hashes_multiple_files(self, tmp_path: Path) -> None:
        files = []
        for i in range(5):
            f = tmp_path / f"file_{i}.txt"
            f.write_text(f"content {i}")
            files.append(f)

        results = hash_files_parallel(files)
        assert len(results) == 5
        for _path_str, digest in results:
            assert len(digest) == 64  # SHA256 hex digest length

    def test_sorted_by_filepath(self, tmp_path: Path) -> None:
        paths = []
        for name in ["c.txt", "a.txt", "b.txt"]:
            f = tmp_path / name
            f.write_text(name)
            paths.append(f)

        results = hash_files_parallel(paths)
        result_paths = [p for p, _ in results]
        assert result_paths == sorted(result_paths)

    def test_empty_list(self) -> None:
        results = hash_files_parallel([])
        assert results == []

    def test_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("deterministic test")

        r1 = hash_files_parallel([f])
        r2 = hash_files_parallel([f])
        assert r1 == r2

    def test_max_workers_param(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("test")

        results = hash_files_parallel([f], max_workers=1)
        assert len(results) == 1


class TestComputeFingerprint:
    def test_deterministic(self) -> None:
        fp1 = compute_fingerprint("config", "prompts", "model-1", [("a.py", "abc")])
        fp2 = compute_fingerprint("config", "prompts", "model-1", [("a.py", "abc")])
        assert fp1 == fp2

    def test_different_config(self) -> None:
        fp1 = compute_fingerprint("config-a", "prompts", "model-1", [])
        fp2 = compute_fingerprint("config-b", "prompts", "model-1", [])
        assert fp1 != fp2

    def test_different_prompts(self) -> None:
        fp1 = compute_fingerprint("config", "prompts-a", "model-1", [])
        fp2 = compute_fingerprint("config", "prompts-b", "model-1", [])
        assert fp1 != fp2

    def test_different_model(self) -> None:
        fp1 = compute_fingerprint("config", "prompts", "model-1", [])
        fp2 = compute_fingerprint("config", "prompts", "model-2", [])
        assert fp1 != fp2

    def test_different_file_hashes(self) -> None:
        fp1 = compute_fingerprint("config", "prompts", "model-1", [("a.py", "hash1")])
        fp2 = compute_fingerprint("config", "prompts", "model-1", [("a.py", "hash2")])
        assert fp1 != fp2

    def test_same_hashes_different_paths_same_fingerprint(self) -> None:
        """File paths are NOT part of the fingerprint — only content hashes."""
        fp1 = compute_fingerprint("config", "prompts", "model-1", [("a.py", "hash")])
        fp2 = compute_fingerprint("config", "prompts", "model-1", [("b.py", "hash")])
        assert fp1 == fp2

    def test_returns_hex_digest(self) -> None:
        fp = compute_fingerprint("c", "p", "m", [])
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_file_order_matters(self) -> None:
        fp1 = compute_fingerprint("c", "p", "m", [("a.py", "h1"), ("b.py", "h2")])
        fp2 = compute_fingerprint("c", "p", "m", [("b.py", "h2"), ("a.py", "h1")])
        assert fp1 != fp2

    def test_empty_file_hashes(self) -> None:
        fp = compute_fingerprint("config", "prompts", "model-1", [])
        assert len(fp) == 64


class TestEvaluateResume:
    def test_no_previous_run_fresh_start(self, db_session: Session) -> None:
        decision = evaluate_resume(db_session, "abc123")
        assert decision.action == ResumeAction.FRESH_START
        assert "No previous run" in decision.message

    def test_matching_fingerprint_resume(self, db_session: Session) -> None:
        db_session.add(
            EnvironmentFingerprint(
                fingerprint="abc123",
                created_at=datetime.now(UTC),
                config_hash="ch",
                model_id="m",
            )
        )
        db_session.commit()

        decision = evaluate_resume(db_session, "abc123")
        assert decision.action == ResumeAction.RESUME
        assert "Resuming" in decision.message

    def test_mismatched_fingerprint_hard_stop(self, db_session: Session) -> None:
        db_session.add(
            EnvironmentFingerprint(
                fingerprint="old-fp",
                created_at=datetime.now(UTC),
                config_hash="ch",
                model_id="m",
            )
        )
        db_session.commit()

        decision = evaluate_resume(db_session, "new-fp")
        assert decision.action == ResumeAction.HARD_STOP
        assert "changed" in decision.message
        assert "--force" in decision.message

    def test_force_overrides_mismatch(self, db_session: Session) -> None:
        db_session.add(
            EnvironmentFingerprint(
                fingerprint="old-fp",
                created_at=datetime.now(UTC),
                config_hash="ch",
                model_id="m",
            )
        )
        db_session.commit()

        decision = evaluate_resume(db_session, "new-fp", force=True)
        assert decision.action == ResumeAction.FRESH_START
        assert "--force" in decision.message

    def test_force_with_no_previous_run(self, db_session: Session) -> None:
        decision = evaluate_resume(db_session, "abc123", force=True)
        assert decision.action == ResumeAction.FRESH_START

    def test_uses_most_recent_fingerprint(self, db_session: Session) -> None:
        db_session.add(
            EnvironmentFingerprint(
                fingerprint="old-fp",
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                config_hash="ch",
                model_id="m",
            )
        )
        db_session.add(
            EnvironmentFingerprint(
                fingerprint="new-fp",
                created_at=datetime(2024, 6, 1, tzinfo=UTC),
                config_hash="ch2",
                model_id="m",
            )
        )
        db_session.commit()

        # Should match against most recent (new-fp)
        decision = evaluate_resume(db_session, "new-fp")
        assert decision.action == ResumeAction.RESUME


class TestStoreFingerprint:
    def test_stores_and_retrieves(self, db_session: Session) -> None:
        record = store_fingerprint(db_session, "fp123", "config-hash", "model-1")

        assert record.fingerprint == "fp123"
        assert record.config_hash == "config-hash"
        assert record.model_id == "model-1"
        assert record.created_at is not None

        # Verify it's in the database
        stored = db_session.get(EnvironmentFingerprint, "fp123")
        assert stored is not None
        assert stored.config_hash == "config-hash"

    def test_multiple_fingerprints(self, db_session: Session) -> None:
        store_fingerprint(db_session, "fp1", "ch1", "m1")
        store_fingerprint(db_session, "fp2", "ch2", "m2")

        count = db_session.query(EnvironmentFingerprint).count()
        assert count == 2

    def test_duplicate_fingerprint_updates_existing(self, db_session: Session) -> None:
        """Storing the same fingerprint twice should not raise IntegrityError."""
        store_fingerprint(db_session, "fp-dup", "ch1", "m1")
        record = store_fingerprint(db_session, "fp-dup", "ch2", "m2")

        assert record.config_hash == "ch2"
        assert record.model_id == "m2"

        count = db_session.query(EnvironmentFingerprint).count()
        assert count == 1
