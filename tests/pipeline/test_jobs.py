"""Tests for job lifecycle operations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from k_extract.pipeline.database import (
    Job,
    JobStatus,
    create_engine_with_wal,
    create_session_factory,
)
from k_extract.pipeline.jobs import (
    FileInfo,
    claim_next_job,
    compute_available_tokens,
    create_jobs,
    mark_completed,
    mark_failed,
    reset_all_in_progress,
    reset_failed_jobs,
    reset_job,
    reset_stale_jobs,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def session_factory(db_path: Path):
    engine = create_engine_with_wal(db_path)
    return create_session_factory(engine)


@pytest.fixture
def session(session_factory) -> Session:
    s = session_factory()
    yield s  # type: ignore[misc]
    s.close()


def _insert_job(
    session: Session,
    job_id: str,
    order: int = 0,
    status: str = JobStatus.PENDING,
    started_at: datetime | None = None,
    agent_instance_id: str | None = None,
    attempt: int = 0,
    completed_at: datetime | None = None,
    error_message: str | None = None,
    data_source: str = "source",
) -> Job:
    """Helper to insert a job for testing."""
    now = datetime.now(UTC)
    job = Job(
        job_id=job_id,
        order=order,
        data_source=data_source,
        files=["file.md"],
        file_count=1,
        total_characters=100,
        status=status,
        created_at=now,
        started_at=started_at,
        completed_at=completed_at,
        agent_instance_id=agent_instance_id,
        attempt=attempt,
        error_message=error_message,
    )
    session.add(job)
    session.commit()
    return job


class TestComputeAvailableTokens:
    def test_basic_computation(self) -> None:
        result = compute_available_tokens(
            context_window=200000,
            prompt_overhead=10000,
            output_reservation=32000,
            safety_margin=5000,
        )
        assert result == 153000

    def test_zero_overhead(self) -> None:
        result = compute_available_tokens(200000, 0, 0, 0)
        assert result == 200000


class TestCreateJobs:
    def test_empty_files(self) -> None:
        jobs = create_jobs([], "source", available_tokens=10000)
        assert jobs == []

    def test_single_file_fits(self) -> None:
        files = [FileInfo("dir/file.md", 1000)]
        # 1000 chars = 250 tokens, budget = 1000 tokens
        jobs = create_jobs(files, "source", available_tokens=1000)
        assert len(jobs) == 1
        assert jobs[0].files == ["dir/file.md"]
        assert jobs[0].file_count == 1
        assert jobs[0].total_characters == 1000
        assert jobs[0].data_source == "source"
        assert jobs[0].status == JobStatus.PENDING
        assert jobs[0].order == 0

    def test_multiple_files_one_job(self) -> None:
        files = [
            FileInfo("dir/a.md", 400),
            FileInfo("dir/b.md", 400),
        ]
        # 800 chars = 200 tokens, budget = 400 tokens
        jobs = create_jobs(files, "source", available_tokens=400)
        assert len(jobs) == 1
        assert jobs[0].file_count == 2

    def test_files_split_across_jobs(self) -> None:
        files = [
            FileInfo("dir1/a.md", 1000),
            FileInfo("dir2/b.md", 1000),
        ]
        # 1000 chars = 250 tokens each, budget = 300 tokens
        # dir1 (250 tokens) fits in job 1
        # dir2 (250 tokens) doesn't fit with dir1 (500 > 300) -> job 2
        jobs = create_jobs(files, "source", available_tokens=300)
        assert len(jobs) == 2

    def test_folder_aware_grouping_keeps_dir_together(self) -> None:
        files = [
            FileInfo("dir1/a.md", 800),
            FileInfo("dir1/b.md", 800),
            FileInfo("dir2/c.md", 800),
        ]
        # 800 chars = 200 tokens each
        # dir1 = 400 tokens (fits in one job, budget = 500)
        # dir2 = 200 tokens (400+200=600 > 500, starts new job)
        jobs = create_jobs(files, "source", available_tokens=500)
        assert len(jobs) == 2
        assert set(jobs[0].files) == {"dir1/a.md", "dir1/b.md"}
        assert jobs[1].files == ["dir2/c.md"]

    def test_all_files_fit_one_job(self) -> None:
        files = [
            FileInfo("dir1/a.md", 400),
            FileInfo("dir1/b.md", 400),
            FileInfo("dir2/c.md", 400),
        ]
        # Total 1200 chars = 300 tokens, budget = 10000 tokens
        jobs = create_jobs(files, "source", available_tokens=10000)
        assert len(jobs) == 1
        assert jobs[0].file_count == 3

    def test_oversized_file_own_job(self) -> None:
        files = [
            FileInfo("dir/small.md", 100),
            FileInfo("dir/huge.md", 100000),
        ]
        # small = 25 tokens, huge = 25000 tokens, budget = 1000
        # Group exceeds budget → process individually
        # huge > 1000 → own job
        # small ≤ 1000 → separate job
        jobs = create_jobs(files, "source", available_tokens=1000)
        assert len(jobs) == 2
        oversized = [j for j in jobs if "dir/huge.md" in j.files]
        assert len(oversized) == 1
        assert oversized[0].file_count == 1

    def test_order_sequential(self) -> None:
        files = [
            FileInfo("dir1/a.md", 1000),
            FileInfo("dir2/b.md", 1000),
        ]
        jobs = create_jobs(files, "source", available_tokens=300, start_order=5)
        assert jobs[0].order == 5
        assert jobs[1].order == 6

    def test_job_ids_unique(self) -> None:
        files = [
            FileInfo("dir1/a.md", 1000),
            FileInfo("dir2/b.md", 1000),
        ]
        jobs = create_jobs(files, "source", available_tokens=300)
        ids = [j.job_id for j in jobs]
        assert len(ids) == len(set(ids))

    def test_large_dir_exceeding_budget_splits(self) -> None:
        """When a directory's total exceeds budget, files are processed individually."""
        files = [
            FileInfo("dir/a.md", 2000),
            FileInfo("dir/b.md", 2000),
            FileInfo("dir/c.md", 2000),
        ]
        # Each file = 500 tokens, dir total = 1500 tokens, budget = 600
        # Group exceeds budget → files split individually
        # a (500) fits in job 1
        # b (500) → 500+500=1000 > 600 → job 2
        # c (500) → 500+500=1000 > 600 → job 3
        jobs = create_jobs(files, "source", available_tokens=600)
        assert len(jobs) == 3

    def test_created_at_set(self) -> None:
        files = [FileInfo("dir/file.md", 100)]
        jobs = create_jobs(files, "source", available_tokens=1000)
        assert jobs[0].created_at is not None

    def test_attempt_starts_at_zero(self) -> None:
        files = [FileInfo("dir/file.md", 100)]
        jobs = create_jobs(files, "source", available_tokens=1000)
        assert jobs[0].attempt == 0


class TestClaimNextJob:
    def test_claim_pending_job(self, session: Session) -> None:
        _insert_job(session, "job-1", order=0)

        claimed = claim_next_job(session, "worker-1")
        assert claimed is not None
        assert claimed.job_id == "job-1"
        assert claimed.status == JobStatus.IN_PROGRESS
        assert claimed.agent_instance_id == "worker-1"
        assert claimed.started_at is not None
        assert claimed.attempt == 1

    def test_claim_no_pending_jobs(self, session: Session) -> None:
        claimed = claim_next_job(session, "worker-1")
        assert claimed is None

    def test_claim_respects_order(self, session: Session) -> None:
        for i in [3, 1, 2]:
            _insert_job(session, f"job-{i}", order=i)

        claimed = claim_next_job(session, "worker-1")
        assert claimed is not None
        assert claimed.job_id == "job-1"

    def test_claim_skips_non_pending(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-done",
            order=0,
            status=JobStatus.COMPLETED,
            completed_at=now,
            attempt=1,
        )
        _insert_job(session, "job-pending", order=1)

        claimed = claim_next_job(session, "worker-1")
        assert claimed is not None
        assert claimed.job_id == "job-pending"

    def test_claim_increments_attempt(self, session: Session) -> None:
        _insert_job(session, "job-1", order=0, attempt=2)

        claimed = claim_next_job(session, "worker-1")
        assert claimed is not None
        assert claimed.attempt == 3

    def test_concurrent_claims_no_duplicates(self, session_factory) -> None:
        """Multiple threads claiming concurrently never claim the same job."""
        session = session_factory()
        now = datetime.now(UTC)
        for i in range(10):
            session.add(
                Job(
                    job_id=f"job-{i}",
                    order=i,
                    data_source="source",
                    files=["file.md"],
                    file_count=1,
                    total_characters=100,
                    status=JobStatus.PENDING,
                    created_at=now,
                    attempt=0,
                )
            )
        session.commit()
        session.close()

        claimed_ids: list[str] = []

        def claim_job(worker_id: str) -> str | None:
            s = session_factory()
            job = claim_next_job(s, worker_id)
            s.close()
            return job.job_id if job else None

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(claim_job, f"worker-{i}") for i in range(10)]
            for f in futures:
                result = f.result()
                if result is not None:
                    claimed_ids.append(result)

        # Each job claimed at most once
        assert len(claimed_ids) == len(set(claimed_ids))
        # All 10 jobs should be claimed
        assert len(claimed_ids) == 10

    def test_global_claim_across_data_sources(self, session: Session) -> None:
        """claim_next_job claims from all data sources by global order."""
        _insert_job(session, "source-a-job", order=0, data_source="source-a")
        _insert_job(session, "source-b-job", order=1, data_source="source-b")
        _insert_job(session, "source-c-job", order=2, data_source="source-c")

        first = claim_next_job(session, "worker-1")
        assert first is not None
        assert first.job_id == "source-a-job"
        assert first.data_source == "source-a"

        second = claim_next_job(session, "worker-1")
        assert second is not None
        assert second.job_id == "source-b-job"
        assert second.data_source == "source-b"

        third = claim_next_job(session, "worker-1")
        assert third is not None
        assert third.job_id == "source-c-job"
        assert third.data_source == "source-c"

        # No more jobs
        assert claim_next_job(session, "worker-1") is None


class TestMarkCompleted:
    def test_mark_completed(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-1",
            status=JobStatus.IN_PROGRESS,
            started_at=now,
            agent_instance_id="worker-1",
            attempt=1,
        )

        mark_completed(session, "job-1")
        job = session.get(Job, "job-1")
        assert job is not None
        assert job.status == JobStatus.COMPLETED
        assert job.completed_at is not None

    def test_cannot_complete_pending_job(self, session: Session) -> None:
        _insert_job(session, "job-1")

        with pytest.raises(ValueError, match="must be in_progress"):
            mark_completed(session, "job-1")

    def test_cannot_complete_failed_job(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-1",
            status=JobStatus.FAILED,
            started_at=now,
            completed_at=now,
            agent_instance_id="worker-1",
            attempt=1,
            error_message="Error",
        )

        with pytest.raises(ValueError, match="must be in_progress"):
            mark_completed(session, "job-1")

    def test_complete_nonexistent_job(self, session: Session) -> None:
        with pytest.raises(ValueError, match="Job not found"):
            mark_completed(session, "nonexistent")


class TestMarkFailed:
    def test_mark_failed(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-1",
            status=JobStatus.IN_PROGRESS,
            started_at=now,
            agent_instance_id="worker-1",
            attempt=1,
        )

        mark_failed(session, "job-1", "Something went wrong")
        job = session.get(Job, "job-1")
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.completed_at is not None
        assert job.error_message == "Something went wrong"

    def test_cannot_fail_pending_job(self, session: Session) -> None:
        _insert_job(session, "job-1")

        with pytest.raises(ValueError, match="must be in_progress"):
            mark_failed(session, "job-1", "Error")

    def test_fail_nonexistent_job(self, session: Session) -> None:
        with pytest.raises(ValueError, match="Job not found"):
            mark_failed(session, "nonexistent", "Error")


class TestResetStaleJobs:
    def test_reset_stale(self, session: Session) -> None:
        old_time = datetime.now(UTC) - timedelta(minutes=90)
        _insert_job(
            session,
            "job-stale",
            status=JobStatus.IN_PROGRESS,
            started_at=old_time,
            agent_instance_id="worker-1",
            attempt=1,
        )

        count = reset_stale_jobs(session, timeout_minutes=60)
        assert count == 1

        session.expire_all()
        job = session.get(Job, "job-stale")
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.started_at is None
        assert job.agent_instance_id is None
        assert job.attempt == 1  # Preserved

    def test_does_not_reset_recent(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-recent",
            status=JobStatus.IN_PROGRESS,
            started_at=now,
            agent_instance_id="worker-1",
            attempt=1,
        )

        count = reset_stale_jobs(session, timeout_minutes=60)
        assert count == 0

    def test_does_not_reset_completed(self, session: Session) -> None:
        old_time = datetime.now(UTC) - timedelta(minutes=90)
        _insert_job(
            session,
            "job-done",
            status=JobStatus.COMPLETED,
            started_at=old_time,
            completed_at=old_time,
            agent_instance_id="worker-1",
            attempt=1,
        )

        count = reset_stale_jobs(session, timeout_minutes=60)
        assert count == 0

    def test_does_not_reset_failed(self, session: Session) -> None:
        old_time = datetime.now(UTC) - timedelta(minutes=90)
        _insert_job(
            session,
            "job-failed",
            status=JobStatus.FAILED,
            started_at=old_time,
            completed_at=old_time,
            agent_instance_id="worker-1",
            attempt=1,
            error_message="Error",
        )

        count = reset_stale_jobs(session, timeout_minutes=60)
        assert count == 0

    def test_configurable_timeout(self, session: Session) -> None:
        old_time = datetime.now(UTC) - timedelta(minutes=45)
        _insert_job(
            session,
            "job-1",
            status=JobStatus.IN_PROGRESS,
            started_at=old_time,
            agent_instance_id="worker-1",
            attempt=1,
        )

        # 60-minute timeout: job is only 45 min old, not stale
        count = reset_stale_jobs(session, timeout_minutes=60)
        assert count == 0

        # 30-minute timeout: job is 45 min old, stale
        count = reset_stale_jobs(session, timeout_minutes=30)
        assert count == 1


class TestResetAllInProgress:
    def test_reset_all(self, session: Session) -> None:
        now = datetime.now(UTC)
        for i in range(3):
            _insert_job(
                session,
                f"job-{i}",
                order=i,
                status=JobStatus.IN_PROGRESS,
                started_at=now,
                agent_instance_id=f"worker-{i}",
                attempt=1,
            )

        count = reset_all_in_progress(session)
        assert count == 3

        session.expire_all()
        for i in range(3):
            job = session.get(Job, f"job-{i}")
            assert job is not None
            assert job.status == JobStatus.PENDING
            assert job.started_at is None
            assert job.agent_instance_id is None
            assert job.attempt == 1  # Preserved

    def test_does_not_affect_other_statuses(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(session, "pending-job", order=0)
        _insert_job(
            session,
            "completed-job",
            order=1,
            status=JobStatus.COMPLETED,
            completed_at=now,
            attempt=1,
        )
        _insert_job(
            session,
            "failed-job",
            order=2,
            status=JobStatus.FAILED,
            completed_at=now,
            attempt=1,
            error_message="Error",
        )

        count = reset_all_in_progress(session)
        assert count == 0


class TestResetJob:
    def test_reset_failed_job(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-1",
            status=JobStatus.FAILED,
            started_at=now,
            completed_at=now,
            agent_instance_id="worker-1",
            attempt=3,
            error_message="Something broke",
        )

        previous = reset_job(session, "job-1")
        assert previous == JobStatus.FAILED

        session.expire_all()
        job = session.get(Job, "job-1")
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.started_at is None
        assert job.completed_at is None
        assert job.error_message is None
        assert job.agent_instance_id is None
        assert job.attempt == 3  # Preserved

    def test_reset_in_progress_job(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-1",
            status=JobStatus.IN_PROGRESS,
            started_at=now,
            agent_instance_id="worker-1",
            attempt=1,
        )

        previous = reset_job(session, "job-1")
        assert previous == JobStatus.IN_PROGRESS

        session.expire_all()
        job = session.get(Job, "job-1")
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.started_at is None
        assert job.agent_instance_id is None

    def test_reset_completed_job(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(
            session,
            "job-1",
            status=JobStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            agent_instance_id="worker-1",
            attempt=1,
        )

        previous = reset_job(session, "job-1")
        assert previous == JobStatus.COMPLETED

    def test_reset_nonexistent_job(self, session: Session) -> None:
        with pytest.raises(ValueError, match="Job not found"):
            reset_job(session, "nonexistent")

    def test_reset_preserves_attempt(self, session: Session) -> None:
        _insert_job(
            session,
            "job-1",
            status=JobStatus.FAILED,
            attempt=5,
            error_message="err",
        )

        reset_job(session, "job-1")

        session.expire_all()
        job = session.get(Job, "job-1")
        assert job is not None
        assert job.attempt == 5


class TestResetFailedJobs:
    def test_reset_failed(self, session: Session) -> None:
        now = datetime.now(UTC)
        for i in range(2):
            _insert_job(
                session,
                f"failed-{i}",
                order=i,
                status=JobStatus.FAILED,
                completed_at=now,
                attempt=2,
                error_message=f"Error {i}",
            )

        count = reset_failed_jobs(session)
        assert count == 2

        session.expire_all()
        for i in range(2):
            job = session.get(Job, f"failed-{i}")
            assert job is not None
            assert job.status == JobStatus.PENDING
            assert job.started_at is None
            assert job.completed_at is None
            assert job.error_message is None
            assert job.agent_instance_id is None
            assert job.attempt == 2  # Preserved

    def test_does_not_affect_other_statuses(self, session: Session) -> None:
        now = datetime.now(UTC)
        _insert_job(session, "pending-job", order=0)
        _insert_job(
            session,
            "completed-job",
            order=1,
            status=JobStatus.COMPLETED,
            completed_at=now,
            attempt=1,
        )
        _insert_job(
            session,
            "ip-job",
            order=2,
            status=JobStatus.IN_PROGRESS,
            started_at=now,
            agent_instance_id="w1",
            attempt=1,
        )

        count = reset_failed_jobs(session)
        assert count == 0
