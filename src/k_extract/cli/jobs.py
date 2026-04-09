"""CLI command for `k-extract jobs`.

Inspects job state from the database for diagnostics and progress tracking.
"""

from __future__ import annotations

from pathlib import Path

import click
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from k_extract.config.loader import load_config
from k_extract.pipeline.database import (
    Job,
    JobStatus,
    create_engine_with_wal,
    create_session_factory,
)
from k_extract.pipeline.jobs import reset_failed_jobs, reset_job


@click.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to extraction.yaml config file.",
)
@click.option(
    "--status",
    "status_filter",
    type=click.Choice(
        [s.value for s in JobStatus],
        case_sensitive=False,
    ),
    default=None,
    help="Filter by job status.",
)
@click.option(
    "--job",
    "job_id",
    type=str,
    default=None,
    help="Show details for a specific job.",
)
@click.option(
    "--data-source",
    "data_source",
    type=str,
    default=None,
    help="Filter by data source name.",
)
@click.option(
    "--reset",
    "reset_id",
    type=str,
    default=None,
    help="Reset a specific job to pending by job ID.",
)
@click.option(
    "--reset-failed",
    "reset_failed",
    is_flag=True,
    default=False,
    help="Reset all failed jobs to pending.",
)
def jobs(
    config_path: Path,
    status_filter: str | None,
    job_id: str | None,
    data_source: str | None,
    reset_id: str | None,
    reset_failed: bool,
) -> None:
    """Inspect extraction job state."""
    config = load_config(config_path)
    db_path = Path(config.output.database)

    if not db_path.exists():
        raise click.ClickException(
            f"Database not found: {db_path}. Run `k-extract run` first."
        )

    engine = create_engine_with_wal(db_path)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        if reset_id is not None:
            _reset_single_job(session, reset_id)
        elif reset_failed:
            _reset_all_failed(session)
        elif job_id is not None:
            _show_job_detail(session, job_id)
        elif status_filter is not None or data_source is not None:
            _show_filtered_listing(session, status_filter, data_source)
        else:
            _show_summary(session)


def _reset_single_job(session: Session, job_id: str) -> None:
    """Reset a specific job to pending."""
    try:
        previous_status = reset_job(session, job_id)
    except ValueError:
        raise click.ClickException(f"Job not found: {job_id}") from None
    click.echo(f"Reset job {job_id}: {previous_status} -> pending")


def _reset_all_failed(session: Session) -> None:
    """Reset all failed jobs to pending."""
    count = reset_failed_jobs(session)
    click.echo(f"Reset {count} failed job(s) to pending.")


def _show_summary(session: Session) -> None:
    """Display summary counts by status."""
    stmt = select(Job.status, func.count()).group_by(Job.status)
    rows = session.execute(stmt).all()

    counts: dict[str, int] = {s.value: 0 for s in JobStatus}
    for status, count in rows:
        counts[status] = count

    total = sum(counts.values())

    parts = [
        f"{counts[JobStatus.COMPLETED]} completed",
        f"{counts[JobStatus.FAILED]} failed",
        f"{counts[JobStatus.PENDING]} pending",
        f"{counts[JobStatus.IN_PROGRESS]} in_progress",
    ]
    click.echo(f"Jobs: {', '.join(parts)} ({total} total)")


def _show_filtered_listing(
    session: Session,
    status_filter: str | None,
    data_source: str | None,
) -> None:
    """Display filtered job listing."""
    stmt = select(Job).order_by(Job.order)

    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    if data_source is not None:
        stmt = stmt.where(Job.data_source == data_source)

    jobs_list = session.execute(stmt).scalars().all()

    if not jobs_list:
        click.echo("No jobs found.")
        return

    for job in jobs_list:
        line = (
            f"{job.job_id}  {job.data_source}  {job.status}  "
            f"files={job.file_count}  chars={job.total_characters}  "
            f"attempt={job.attempt}"
        )
        if job.status == JobStatus.FAILED and job.error_message:
            line += f"  error={job.error_message}"
        click.echo(line)


def _show_job_detail(session: Session, job_id: str) -> None:
    """Display full detail for a specific job."""
    job = session.get(Job, job_id)
    if job is None:
        raise click.ClickException(f"Job not found: {job_id}")

    click.echo(f"Job: {job.job_id}")
    click.echo(f"Data source: {job.data_source}")
    click.echo(f"Status: {job.status}")
    click.echo(f"Order: {job.order}")
    click.echo(f"Files: {job.file_count}")
    click.echo(f"Total characters: {job.total_characters}")
    click.echo(f"Attempt: {job.attempt}")
    click.echo(f"Agent instance: {job.agent_instance_id}")
    click.echo(f"Created: {job.created_at}")
    click.echo(f"Started: {job.started_at}")
    click.echo(f"Completed: {job.completed_at}")
    if job.error_message:
        click.echo(f"Error: {job.error_message}")

    click.echo("")
    click.echo("File list:")
    for f in job.files:
        click.echo(f"  {f}")
