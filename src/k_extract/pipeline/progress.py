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

# Maximum display width for data source names before truncation
_MAX_SOURCE_NAME_LEN = 20


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


@dataclass
class SourceProgress:
    """Tracks per-data-source job counts."""

    total: int = 0
    completed: int = 0
    failed: int = 0

    @property
    def pending(self) -> int:
        """Pending jobs for this source."""
        return self.total - self.completed - self.failed


class PipelineProgress:
    """Tracks live pipeline state for dashboard display.

    Safe for concurrent updates from multiple async worker tasks
    within a single asyncio event loop (single-threaded, no preemption
    between awaits).
    """

    def __init__(self, worker_count: int) -> None:
        self.cumulative_cost: float = 0.0
        self._start_time: float = time.monotonic()
        self._source_order: list[str] = []
        self._sources: dict[str, SourceProgress] = {}
        self.workers: dict[str, WorkerState] = {}
        for i in range(worker_count):
            wid = f"{i + 1:02d}"
            self.workers[wid] = WorkerState(worker_id=wid)

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed wall-clock time since pipeline start."""
        return time.monotonic() - self._start_time

    @property
    def total_jobs(self) -> int:
        """Total jobs across all registered sources."""
        return sum(s.total for s in self._sources.values())

    @property
    def completed_jobs(self) -> int:
        """Completed jobs across all sources."""
        return sum(s.completed for s in self._sources.values())

    @property
    def failed_jobs(self) -> int:
        """Failed jobs across all sources."""
        return sum(s.failed for s in self._sources.values())

    @property
    def pending_jobs(self) -> int:
        """Pending jobs across all sources."""
        return sum(s.pending for s in self._sources.values())

    def register_sources(
        self,
        sources: dict[str, int],
        initial_completed: dict[str, int] | None = None,
        initial_failed: dict[str, int] | None = None,
    ) -> None:
        """Register all data sources with their total job counts upfront.

        Args:
            sources: Mapping of data source name to total job count,
                ordered as in the config.
            initial_completed: Per-source completed counts for resume
                accuracy. If None, all sources start at 0.
            initial_failed: Per-source failed counts for resume
                accuracy. If None, all sources start at 0.
        """
        completed = initial_completed or {}
        failed = initial_failed or {}
        self._source_order = list(sources.keys())
        self._sources = {
            name: SourceProgress(
                total=count,
                completed=completed.get(name, 0),
                failed=failed.get(name, 0),
            )
            for name, count in sources.items()
        }

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

    def record_job_completed(
        self, worker_id: str, cost: float, data_source: str
    ) -> None:
        """Record a job completion: increment completed, add cost.

        Args:
            worker_id: The worker that completed the job.
            cost: Cost incurred for this job.
            data_source: Name of the data source this job belongs to.
        """
        if data_source in self._sources:
            self._sources[data_source].completed += 1
        self.cumulative_cost += cost
        self.mark_worker_idle(worker_id)

    def record_job_failed(self, worker_id: str, data_source: str) -> None:
        """Record a job failure: increment failed count.

        Args:
            worker_id: The worker that failed the job.
            data_source: Name of the data source this job belongs to.
        """
        if data_source in self._sources:
            self._sources[data_source].failed += 1
        self.mark_worker_idle(worker_id)

    def get_source_progress(self, name: str) -> SourceProgress | None:
        """Get progress for a specific data source."""
        return self._sources.get(name)


def _format_elapsed(seconds: float) -> str:
    """Format seconds into a human-readable elapsed string."""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _truncate_name(name: str, max_len: int = _MAX_SOURCE_NAME_LEN) -> str:
    """Truncate a data source name with '...' if too long."""
    if len(name) <= max_len:
        return name
    return name[: max_len - 3] + "..."


def render_dashboard(progress: PipelineProgress) -> Group:
    """Render the progress state into a Rich renderable.

    Produces a compact display with:
    - Header line: "k-extract" branding, elapsed time, cost
    - N+1 progress bars (Total + per source)
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
        ("k-extract", "bold cyan"),
        ("  ", ""),
        (f"elapsed: {elapsed}", "dim"),
        ("  ", ""),
        (f"cost: {cost_str}", "dim"),
    )

    # Total progress bar (visually distinct — full block chars, bold white)
    total_bar = Progress(
        TextColumn("  {task.description}", style="bold"),
        BarColumn(
            bar_width=30,
            complete_style="bold white",
            finished_style="bold green",
            pulse_style="bold white",
        ),
        MofNCompleteColumn(),
        TextColumn("jobs"),
        expand=False,
    )
    total_completed = progress.completed_jobs + progress.failed_jobs
    total_bar.add_task(
        _truncate_name("Total", _MAX_SOURCE_NAME_LEN).ljust(_MAX_SOURCE_NAME_LEN),
        total=max(progress.total_jobs, 1),
        completed=total_completed,
    )

    # Per-source progress bars
    source_bar = Progress(
        TextColumn("  {task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TextColumn("jobs"),
        expand=False,
    )
    for source_name in progress._source_order:
        sp = progress._sources[source_name]
        display_name = _truncate_name(source_name, _MAX_SOURCE_NAME_LEN).ljust(
            _MAX_SOURCE_NAME_LEN
        )
        source_done = sp.completed + sp.failed
        source_bar.add_task(
            display_name,
            total=max(sp.total, 1),
            completed=source_done,
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
            time_str = (
                f"({_format_elapsed(elapsed_job)})" if elapsed_job is not None else ""
            )
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

    return Group(header, total_bar, source_bar, worker_table, summary)
