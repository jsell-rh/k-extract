"""Job lifecycle operations: batching, claiming, completion, and stale recovery.

Implements job generation (context-window-based batching), atomic claiming,
completion/failure recording, and stale job detection/recovery.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

from k_extract.pipeline.database import Job, JobStatus

CHARS_PER_TOKEN = 4


@dataclass
class FileInfo:
    """File metadata for job batching."""

    path: str
    char_count: int


def compute_available_tokens(
    context_window: int,
    prompt_overhead: int,
    output_reservation: int,
    safety_margin: int,
) -> int:
    """Compute available token budget for source material.

    Formula: context_window - prompt_overhead
             - output_reservation - safety_margin
    """
    return context_window - prompt_overhead - output_reservation - safety_margin


def create_jobs(
    files: list[FileInfo],
    data_source: str,
    available_tokens: int,
    start_order: int = 0,
) -> list[Job]:
    """Create job records using context-window-based batching.

    Files are grouped by parent directory for better agent context.
    Oversized files (exceeding available_tokens) get their own job.
    Token estimation: chars / 4.
    """
    if not files:
        return []

    # Group files by parent directory
    groups: dict[str, list[FileInfo]] = defaultdict(list)
    for f in files:
        parent = str(PurePosixPath(f.path).parent)
        groups[parent].append(f)

    jobs: list[Job] = []
    current_files: list[FileInfo] = []
    current_chars = 0
    order = start_order
    now = datetime.now(UTC)

    for _dir, group_files in sorted(groups.items()):
        group_chars = sum(f.char_count for f in group_files)
        group_tokens = group_chars / CHARS_PER_TOKEN

        if group_tokens <= available_tokens:
            # Group fits in a single job — try to add to current job
            current_tokens = current_chars / CHARS_PER_TOKEN
            if current_tokens + group_tokens <= available_tokens:
                current_files.extend(group_files)
                current_chars += group_chars
            else:
                # Finalize current job, start new one with this group
                if current_files:
                    jobs.append(
                        _make_job(current_files, data_source, current_chars, order, now)
                    )
                    order += 1
                current_files = list(group_files)
                current_chars = group_chars
        else:
            # Group exceeds budget — process files individually
            if current_files:
                jobs.append(
                    _make_job(current_files, data_source, current_chars, order, now)
                )
                order += 1
                current_files = []
                current_chars = 0

            for f in group_files:
                file_tokens = f.char_count / CHARS_PER_TOKEN
                if file_tokens > available_tokens:
                    # Oversized file — gets its own job
                    jobs.append(_make_job([f], data_source, f.char_count, order, now))
                    order += 1
                else:
                    current_tokens = current_chars / CHARS_PER_TOKEN
                    if current_tokens + file_tokens <= available_tokens:
                        current_files.append(f)
                        current_chars += f.char_count
                    else:
                        if current_files:
                            jobs.append(
                                _make_job(
                                    current_files,
                                    data_source,
                                    current_chars,
                                    order,
                                    now,
                                )
                            )
                            order += 1
                        current_files = [f]
                        current_chars = f.char_count

    # Finalize remaining job
    if current_files:
        jobs.append(_make_job(current_files, data_source, current_chars, order, now))

    return jobs


def claim_next_job(
    session: Session,
    agent_instance_id: str,
) -> Job | None:
    """Atomically claim the next pending job from the global queue.

    Uses UPDATE...WHERE...RETURNING to atomically find and claim the
    next pending job ordered by the 'order' field. Claims globally
    across all data sources.

    Args:
        session: Database session.
        agent_instance_id: Worker identifier.
    """
    now = datetime.now(UTC)

    result = session.execute(
        text(
            "UPDATE jobs "
            "SET status = :status, started_at = :now, "
            "agent_instance_id = :agent_id, attempt = attempt + 1 "
            "WHERE job_id = ("
            "  SELECT job_id FROM jobs "
            "  WHERE status = :pending "
            '  ORDER BY "order" ASC LIMIT 1'
            ") "
            "RETURNING job_id"
        ),
        {
            "status": JobStatus.IN_PROGRESS.value,
            "now": now,
            "agent_id": agent_instance_id,
            "pending": JobStatus.PENDING.value,
        },
    )

    row = result.fetchone()
    if row is None:
        return None
    job_id = row[0]
    session.commit()
    return session.get(Job, job_id)


def mark_completed(session: Session, job_id: str) -> None:
    """Mark a job as completed with a timestamp.

    Only in_progress jobs can be marked completed.
    """
    job = session.get(Job, job_id)
    if job is None:
        msg = f"Job not found: {job_id!r}"
        raise ValueError(msg)
    if job.status != JobStatus.IN_PROGRESS:
        msg = f"Cannot complete job in {job.status!r} state, must be in_progress"
        raise ValueError(msg)
    job.status = JobStatus.COMPLETED
    job.completed_at = datetime.now(UTC)
    session.commit()


def mark_failed(session: Session, job_id: str, error_message: str) -> None:
    """Mark a job as failed with error message and timestamp.

    Only in_progress jobs can be marked failed.
    """
    job = session.get(Job, job_id)
    if job is None:
        msg = f"Job not found: {job_id!r}"
        raise ValueError(msg)
    if job.status != JobStatus.IN_PROGRESS:
        msg = f"Cannot fail job in {job.status!r} state, must be in_progress"
        raise ValueError(msg)
    job.status = JobStatus.FAILED
    job.completed_at = datetime.now(UTC)
    job.error_message = error_message
    session.commit()


def reset_stale_jobs(session: Session, timeout_minutes: int = 60) -> int:
    """Reset in_progress jobs older than timeout back to pending.

    Clears started_at and agent_instance_id. Preserves attempt counter.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    result: CursorResult = session.execute(  # type: ignore[assignment]
        text(
            "UPDATE jobs "
            "SET status = :pending, started_at = NULL, "
            "agent_instance_id = NULL "
            "WHERE status = :in_progress AND started_at < :cutoff"
        ),
        {
            "pending": JobStatus.PENDING.value,
            "in_progress": JobStatus.IN_PROGRESS.value,
            "cutoff": cutoff,
        },
    )
    session.commit()
    return result.rowcount


def reset_failed_jobs(session: Session) -> int:
    """Reset all failed jobs to pending for retry.

    Clears error_message, started_at, completed_at, and agent_instance_id.
    Preserves attempt counter.
    """
    result: CursorResult = session.execute(  # type: ignore[assignment]
        text(
            "UPDATE jobs "
            "SET status = :pending, started_at = NULL, "
            "completed_at = NULL, error_message = NULL, "
            "agent_instance_id = NULL "
            "WHERE status = :failed"
        ),
        {
            "pending": JobStatus.PENDING.value,
            "failed": JobStatus.FAILED.value,
        },
    )
    session.commit()
    return result.rowcount


def reset_job(session: Session, job_id: str) -> str:
    """Reset a specific job to pending by ID.

    Clears started_at, completed_at, error_message, and agent_instance_id.
    Preserves attempt counter.

    Returns the previous status of the job.
    Raises ValueError if job_id not found.
    """
    job = session.get(Job, job_id)
    if job is None:
        msg = f"Job not found: {job_id!r}"
        raise ValueError(msg)
    previous_status = job.status
    job.status = JobStatus.PENDING
    job.started_at = None
    job.completed_at = None
    job.error_message = None
    job.agent_instance_id = None
    session.commit()
    return previous_status


def reset_all_in_progress(session: Session) -> int:
    """Reset all in_progress jobs to pending (startup reset).

    Clears started_at and agent_instance_id. Preserves attempt counter.
    """
    result: CursorResult = session.execute(  # type: ignore[assignment]
        text(
            "UPDATE jobs "
            "SET status = :pending, started_at = NULL, "
            "agent_instance_id = NULL "
            "WHERE status = :in_progress"
        ),
        {
            "pending": JobStatus.PENDING.value,
            "in_progress": JobStatus.IN_PROGRESS.value,
        },
    )
    session.commit()
    return result.rowcount


def _make_job(
    files: list[FileInfo],
    data_source: str,
    total_chars: int,
    order: int,
    created_at: datetime,
) -> Job:
    """Create a Job record from a list of files."""
    return Job(
        job_id=uuid.uuid4().hex,
        order=order,
        data_source=data_source,
        files=[f.path for f in files],
        file_count=len(files),
        total_characters=total_chars,
        status=JobStatus.PENDING,
        created_at=created_at,
        attempt=0,
    )
