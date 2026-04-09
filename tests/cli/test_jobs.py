"""Tests for `k-extract jobs` CLI command."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from click.testing import CliRunner

from k_extract.cli import main
from k_extract.pipeline.database import (
    Job,
    JobStatus,
    create_engine_with_wal,
    create_session_factory,
)


def _minimal_config() -> dict:
    """Return a minimal valid extraction config dict."""
    return {
        "problem_statement": "Test extraction",
        "data_sources": [{"name": "test-source", "path": "/tmp/test"}],
        "ontology": {"entity_types": [], "relationship_types": []},
        "prompts": {
            "system_prompt": "You are an extractor.",
            "job_description_template": "Extract from: ${{files}}",
        },
        "output": {"file": "output.jsonl", "database": "test.db"},
    }


def _write_config(tmp_path: Path, db_name: str = "test.db") -> Path:
    """Write a minimal config file and return its path."""
    config_data = _minimal_config()
    config_data["output"]["database"] = str(tmp_path / db_name)
    config_path = tmp_path / "extraction.yaml"
    with config_path.open("w") as f:
        yaml.dump(config_data, f)
    return config_path


def _create_db_with_jobs(
    db_path: Path,
    jobs: list[Job],
) -> None:
    """Create a database and insert jobs."""
    engine = create_engine_with_wal(db_path)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        for job in jobs:
            session.add(job)
        session.commit()


def _make_job(
    job_id: str = "job-001",
    order: int = 0,
    data_source: str = "test-source",
    files: list[str] | None = None,
    file_count: int = 2,
    total_characters: int = 5000,
    status: str = JobStatus.PENDING,
    attempt: int = 0,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    agent_instance_id: str | None = None,
) -> Job:
    """Create a Job instance for testing."""
    return Job(
        job_id=job_id,
        order=order,
        data_source=data_source,
        files=files if files is not None else ["file1.py", "file2.py"],
        file_count=file_count,
        total_characters=total_characters,
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=started_at,
        completed_at=completed_at,
        agent_instance_id=agent_instance_id,
        attempt=attempt,
        error_message=error_message,
    )


class TestJobsSummary:
    """Tests for default summary display."""

    def test_summary_with_mixed_statuses(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job("j1", order=0, status=JobStatus.COMPLETED),
                _make_job("j2", order=1, status=JobStatus.COMPLETED),
                _make_job("j3", order=2, status=JobStatus.FAILED, error_message="err"),
                _make_job("j4", order=3, status=JobStatus.PENDING),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(main, ["jobs", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "2 completed" in result.output
        assert "1 failed" in result.output
        assert "1 pending" in result.output
        assert "0 in_progress" in result.output
        assert "4 total" in result.output

    def test_summary_empty_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)
        _create_db_with_jobs(db_path, [])

        runner = CliRunner()
        result = runner.invoke(main, ["jobs", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "0 total" in result.output

    def test_summary_all_completed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job("j1", order=0, status=JobStatus.COMPLETED),
                _make_job("j2", order=1, status=JobStatus.COMPLETED),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(main, ["jobs", "--config", str(config_path)])

        assert result.exit_code == 0
        assert "2 completed" in result.output
        assert "0 failed" in result.output
        assert "2 total" in result.output


class TestJobsFilteredListing:
    """Tests for filtered job listing."""

    def test_filter_by_status_failed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job("j1", order=0, status=JobStatus.COMPLETED),
                _make_job(
                    "j2",
                    order=1,
                    status=JobStatus.FAILED,
                    error_message="Validation failed",
                ),
                _make_job(
                    "j3",
                    order=2,
                    status=JobStatus.FAILED,
                    error_message="Timeout",
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--status", "failed"]
        )

        assert result.exit_code == 0
        assert "j2" in result.output
        assert "j3" in result.output
        assert "j1" not in result.output
        assert "Validation failed" in result.output
        assert "Timeout" in result.output

    def test_filter_by_status_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job("j1", order=0, status=JobStatus.PENDING),
                _make_job("j2", order=1, status=JobStatus.COMPLETED),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--status", "pending"]
        )

        assert result.exit_code == 0
        assert "j1" in result.output
        assert "j2" not in result.output

    def test_filter_by_data_source(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job("j1", order=0, data_source="source-a"),
                _make_job("j2", order=1, data_source="source-b"),
                _make_job("j3", order=2, data_source="source-a"),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["jobs", "--config", str(config_path), "--data-source", "source-a"],
        )

        assert result.exit_code == 0
        assert "j1" in result.output
        assert "j3" in result.output
        assert "j2" not in result.output

    def test_filter_combined_status_and_data_source(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "j1",
                    order=0,
                    data_source="src-a",
                    status=JobStatus.FAILED,
                    error_message="err",
                ),
                _make_job(
                    "j2",
                    order=1,
                    data_source="src-b",
                    status=JobStatus.FAILED,
                    error_message="err",
                ),
                _make_job(
                    "j3",
                    order=2,
                    data_source="src-a",
                    status=JobStatus.COMPLETED,
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "jobs",
                "--config",
                str(config_path),
                "--status",
                "failed",
                "--data-source",
                "src-a",
            ],
        )

        assert result.exit_code == 0
        assert "j1" in result.output
        assert "j2" not in result.output
        assert "j3" not in result.output

    def test_filter_no_results(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [_make_job("j1", order=0, status=JobStatus.COMPLETED)],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--status", "failed"]
        )

        assert result.exit_code == 0
        assert "No jobs found" in result.output

    def test_filtered_listing_shows_fields(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "j1",
                    order=0,
                    data_source="my-source",
                    status=JobStatus.COMPLETED,
                    file_count=5,
                    total_characters=12000,
                    attempt=2,
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--status", "completed"]
        )

        assert result.exit_code == 0
        assert "j1" in result.output
        assert "my-source" in result.output
        assert "completed" in result.output
        assert "files=5" in result.output
        assert "chars=12000" in result.output
        assert "attempt=2" in result.output


class TestJobDetail:
    """Tests for single job detail view."""

    def test_job_detail_completed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "job-abc",
                    order=0,
                    data_source="my-source",
                    files=["a.py", "b.py", "c.py"],
                    file_count=3,
                    total_characters=8000,
                    status=JobStatus.COMPLETED,
                    attempt=1,
                    started_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC),
                    completed_at=datetime(2026, 1, 1, 1, 5, 0, tzinfo=UTC),
                    agent_instance_id="worker-01",
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--job", "job-abc"]
        )

        assert result.exit_code == 0
        assert "Job: job-abc" in result.output
        assert "Data source: my-source" in result.output
        assert "Status: completed" in result.output
        assert "Files: 3" in result.output
        assert "Total characters: 8000" in result.output
        assert "Attempt: 1" in result.output
        assert "Agent instance: worker-01" in result.output
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.py" in result.output

    def test_job_detail_failed_with_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "job-err",
                    order=0,
                    status=JobStatus.FAILED,
                    error_message="Validation failed: duplicate slug",
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--job", "job-err"]
        )

        assert result.exit_code == 0
        assert "Error: Validation failed: duplicate slug" in result.output

    def test_job_detail_not_found(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)
        _create_db_with_jobs(db_path, [])

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--job", "nonexistent"]
        )

        assert result.exit_code != 0
        assert "Job not found: nonexistent" in result.output

    def test_job_detail_shows_timestamps(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        started = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
        completed = datetime(2026, 1, 1, 1, 5, 0, tzinfo=UTC)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "job-ts",
                    order=0,
                    status=JobStatus.COMPLETED,
                    started_at=started,
                    completed_at=completed,
                ),
            ],
        )
        # Overwrite created_at since _make_job uses a fixed value
        # The test just verifies the labels are present
        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--job", "job-ts"]
        )

        assert result.exit_code == 0
        assert "Created:" in result.output
        assert "Started:" in result.output
        assert "Completed:" in result.output

    def test_job_detail_pending_null_timestamps(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [_make_job("job-pend", order=0, status=JobStatus.PENDING)],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--job", "job-pend"]
        )

        assert result.exit_code == 0
        assert "Agent instance: None" in result.output
        assert "Started: None" in result.output
        assert "Completed: None" in result.output


class TestJobReset:
    """Tests for --reset (single job) and --reset-failed options."""

    def test_reset_failed_job(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "job-fail",
                    order=0,
                    status=JobStatus.FAILED,
                    error_message="Some error",
                    started_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC),
                    completed_at=datetime(2026, 1, 1, 1, 5, 0, tzinfo=UTC),
                    agent_instance_id="worker-01",
                    attempt=2,
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--reset", "job-fail"]
        )

        assert result.exit_code == 0
        assert "Reset job job-fail: failed -> pending" in result.output

        # Verify the job was actually reset in the database
        engine = create_engine_with_wal(db_path)
        sf = create_session_factory(engine)
        with sf() as session:
            job = session.get(Job, "job-fail")
            assert job is not None
            assert job.status == JobStatus.PENDING
            assert job.started_at is None
            assert job.completed_at is None
            assert job.error_message is None
            assert job.agent_instance_id is None
            assert job.attempt == 2  # preserved

    def test_reset_in_progress_job(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "job-ip",
                    order=0,
                    status=JobStatus.IN_PROGRESS,
                    started_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC),
                    agent_instance_id="worker-02",
                    attempt=1,
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--reset", "job-ip"]
        )

        assert result.exit_code == 0
        assert "Reset job job-ip: in_progress -> pending" in result.output

    def test_reset_job_not_found(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)
        _create_db_with_jobs(db_path, [])

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--reset", "nonexistent"]
        )

        assert result.exit_code != 0
        assert "Job not found: nonexistent" in result.output

    def test_reset_failed_flag(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "j1",
                    order=0,
                    status=JobStatus.FAILED,
                    error_message="err1",
                    attempt=1,
                ),
                _make_job(
                    "j2",
                    order=1,
                    status=JobStatus.FAILED,
                    error_message="err2",
                    attempt=3,
                ),
                _make_job("j3", order=2, status=JobStatus.COMPLETED),
                _make_job("j4", order=3, status=JobStatus.PENDING),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--reset-failed"]
        )

        assert result.exit_code == 0
        assert "Reset 2 failed job(s) to pending." in result.output

        # Verify only failed jobs were reset
        engine = create_engine_with_wal(db_path)
        sf = create_session_factory(engine)
        with sf() as session:
            j1 = session.get(Job, "j1")
            j2 = session.get(Job, "j2")
            j3 = session.get(Job, "j3")
            assert j1 is not None and j1.status == JobStatus.PENDING
            assert j2 is not None and j2.status == JobStatus.PENDING
            assert j3 is not None and j3.status == JobStatus.COMPLETED

    def test_reset_failed_no_failed_jobs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [_make_job("j1", order=0, status=JobStatus.COMPLETED)],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--reset-failed"]
        )

        assert result.exit_code == 0
        assert "Reset 0 failed job(s) to pending." in result.output

    def test_reset_preserves_attempt_counter(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        config_path = _write_config(tmp_path)

        _create_db_with_jobs(
            db_path,
            [
                _make_job(
                    "job-retry",
                    order=0,
                    status=JobStatus.FAILED,
                    error_message="timeout",
                    attempt=5,
                ),
            ],
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["jobs", "--config", str(config_path), "--reset", "job-retry"]
        )

        assert result.exit_code == 0

        engine = create_engine_with_wal(db_path)
        sf = create_session_factory(engine)
        with sf() as session:
            job = session.get(Job, "job-retry")
            assert job is not None
            assert job.attempt == 5
            assert job.status == JobStatus.PENDING


class TestJobsErrors:
    """Tests for error handling."""

    def test_missing_database(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, db_name="nonexistent.db")

        runner = CliRunner()
        result = runner.invoke(main, ["jobs", "--config", str(config_path)])

        assert result.exit_code != 0
        assert "Database not found" in result.output
