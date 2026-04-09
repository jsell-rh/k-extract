"""Pipeline progress tracking and Rich dashboard display.

Tracks real-time pipeline state and renders a compact, information-dense
dashboard using Rich. PipelineProgress is safe for concurrent updates
from multiple async worker tasks via asyncio (single-threaded event loop).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from rich.console import Group
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text


class WorkerStatus(Enum):
    """Status of a worker."""

    IDLE = "idle"
    PROCESSING = "processing"
    FINISHED = "finished"


@dataclass
class WorkerState:
    """Tracks the current state of a single worker."""

    worker_id: str
    status: WorkerStatus = WorkerStatus.IDLE
    current_job_id: str | None = None
    job_start_time: float | None = None

    @property
    def elapsed_job_seconds(self) -> float | None:
        """Elapsed seconds on the current job, or None if idle."""
        if self.status == WorkerStatus.PROCESSING and self.job_start_time is not None:
            return time.monotonic() - self.job_start_time
        return None


class PipelineProgress:
    """Tracks live pipeline state for dashboard display.

    Safe for concurrent updates from multiple async worker tasks
    within a single asyncio event loop (single-threaded, no preemption
    between awaits).
    """

    def __init__(self, worker_count: int) -> None:
        self.total_jobs: int = 0
        self.completed_jobs: int = 0
        self.failed_jobs: int = 0
        self.pending_jobs: int = 0
        self.cumulative_cost: float = 0.0
        self.current_data_source: str = ""
        self._start_time: float = time.monotonic()
        self.workers: dict[str, WorkerState] = {}
        for i in range(worker_count):
            wid = f"{i + 1:02d}"
            self.workers[wid] = WorkerState(worker_id=wid)

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed wall-clock time since pipeline start."""
        return time.monotonic() - self._start_time

    def set_data_source(self, name: str, total: int, pending: int) -> None:
        """Update progress for a new data source phase."""
        self.current_data_source = name
        self.total_jobs = total
        self.pending_jobs = pending
        # Reset completed/failed for this data source display
        self.completed_jobs = total - pending
        self.failed_jobs = 0
        # Reset all worker states to IDLE for the new data source
        for ws in self.workers.values():
            ws.status = WorkerStatus.IDLE
            ws.current_job_id = None
            ws.job_start_time = None

    def mark_worker_idle(self, worker_id: str) -> None:
        """Mark a worker as idle (no current job)."""
        if worker_id in self.workers:
            ws = self.workers[worker_id]
            ws.status = WorkerStatus.IDLE
            ws.current_job_id = None
            ws.job_start_time = None

    def mark_worker_processing(self, worker_id: str, job_id: str) -> None:
        """Mark a worker as processing a specific job."""
        if worker_id in self.workers:
            ws = self.workers[worker_id]
            ws.status = WorkerStatus.PROCESSING
            ws.current_job_id = job_id
            ws.job_start_time = time.monotonic()

    def mark_worker_finished(self, worker_id: str) -> None:
        """Mark a worker as finished (done processing all jobs)."""
        if worker_id in self.workers:
            ws = self.workers[worker_id]
            ws.status = WorkerStatus.FINISHED
            ws.current_job_id = None
            ws.job_start_time = None

    def record_job_completed(self, worker_id: str, cost: float) -> None:
        """Record a job completion: increment completed, add cost."""
        self.completed_jobs += 1
        self.pending_jobs = max(0, self.pending_jobs - 1)
        self.cumulative_cost += cost
        self.mark_worker_idle(worker_id)

    def record_job_failed(self, worker_id: str) -> None:
        """Record a job failure: increment failed count."""
        self.failed_jobs += 1
        self.pending_jobs = max(0, self.pending_jobs - 1)
        self.mark_worker_idle(worker_id)


def _format_elapsed(seconds: float) -> str:
    """Format seconds into a human-readable elapsed string."""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def render_dashboard(progress: PipelineProgress) -> Group:
    """Render the progress state into a Rich renderable.

    Produces a compact display with:
    - Header line: data source, elapsed time, cost
    - Progress bar with job fraction
    - Per-worker status rows
    - Summary line: completed, failed, pending counts

    Args:
        progress: Current pipeline progress state.

    Returns:
        A Rich Group renderable for use with rich.live.Live.
    """
    elapsed = _format_elapsed(progress.elapsed_seconds)
    cost_str = f"${progress.cumulative_cost:.2f}"

    # Header
    header = Text.assemble(
        ("  ", ""),
        (progress.current_data_source, "bold cyan"),
        ("  ", ""),
        (f"elapsed: {elapsed}", "dim"),
        ("  ", ""),
        (f"cost: {cost_str}", "dim"),
    )

    # Progress bar
    bar = Progress(
        TextColumn("  "),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TextColumn("jobs"),
        expand=False,
    )
    bar.add_task(
        "jobs",
        total=progress.total_jobs,
        completed=progress.completed_jobs + progress.failed_jobs,
    )

    # Worker status table
    worker_table = Table(show_header=False, box=None, padding=(0, 1))
    worker_table.add_column("worker", style="bold")
    worker_table.add_column("status")

    for wid in sorted(progress.workers):
        ws = progress.workers[wid]
        label = f"  worker-{wid}:"
        if ws.status == WorkerStatus.PROCESSING:
            elapsed_job = ws.elapsed_job_seconds
            time_str = f"({int(elapsed_job)}s)" if elapsed_job is not None else ""
            status_text = Text.assemble(
                ("processing ", "yellow"),
                (ws.current_job_id or "", ""),
                (f" {time_str}", "dim"),
            )
        elif ws.status == WorkerStatus.FINISHED:
            status_text = Text("finished", style="green")
        else:
            status_text = Text("idle", style="dim")
        worker_table.add_row(label, status_text)

    # Summary line
    summary = Text.assemble(
        ("  completed: ", ""),
        (str(progress.completed_jobs), "green"),
        ("  failed: ", ""),
        (str(progress.failed_jobs), "red" if progress.failed_jobs > 0 else "dim"),
        ("  pending: ", ""),
        (str(progress.pending_jobs), "dim"),
    )

    return Group(header, bar, worker_table, summary)
