"""Tests for pipeline progress tracking and dashboard rendering."""

from __future__ import annotations

import asyncio

import pytest
from rich.console import Console

from k_extract.pipeline.progress import (
    PipelineProgress,
    SourceProgress,
    WorkerStatus,
    _truncate_name,
    render_dashboard,
)


class TestPipelineProgress:
    def test_initial_state(self) -> None:
        """PipelineProgress initializes with correct defaults."""
        progress = PipelineProgress(worker_count=3)

        assert progress.total_jobs == 0
        assert progress.completed_jobs == 0
        assert progress.failed_jobs == 0
        assert progress.pending_jobs == 0
        assert progress.cumulative_cost == 0.0
        assert len(progress.workers) == 3
        assert "01" in progress.workers
        assert "02" in progress.workers
        assert "03" in progress.workers

    def test_register_sources(self) -> None:
        """register_sources sets per-source totals and ordering."""
        progress = PipelineProgress(worker_count=2)
        progress.register_sources({"alpha": 10, "beta": 5, "gamma": 20})

        assert progress.total_jobs == 35
        assert progress.pending_jobs == 35
        assert progress.completed_jobs == 0
        assert progress.failed_jobs == 0

        # Order preserved
        assert progress._source_order == ["alpha", "beta", "gamma"]

    def test_register_sources_with_initial_completed(self) -> None:
        """register_sources reflects initial completed counts for resume."""
        progress = PipelineProgress(worker_count=2)
        progress.register_sources(
            {"alpha": 100, "beta": 50},
            initial_completed={"alpha": 80, "beta": 30},
        )

        assert progress.total_jobs == 150
        assert progress.completed_jobs == 110
        assert progress.pending_jobs == 40

        sp_alpha = progress.get_source_progress("alpha")
        assert sp_alpha is not None
        assert sp_alpha.completed == 80
        assert sp_alpha.pending == 20

        sp_beta = progress.get_source_progress("beta")
        assert sp_beta is not None
        assert sp_beta.completed == 30
        assert sp_beta.pending == 20

    def test_register_sources_with_initial_failed(self) -> None:
        """register_sources reflects initial failed counts for resume."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources(
            {"src": 50},
            initial_completed={"src": 30},
            initial_failed={"src": 5},
        )

        assert progress.completed_jobs == 30
        assert progress.failed_jobs == 5
        assert progress.pending_jobs == 15

    def test_register_sources_replaces_previous(self) -> None:
        """Calling register_sources again replaces prior registration."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"old": 100})
        assert progress.total_jobs == 100

        progress.register_sources({"new-a": 5, "new-b": 10})
        assert progress.total_jobs == 15
        assert progress._source_order == ["new-a", "new-b"]

    def test_mark_worker_idle(self) -> None:
        """mark_worker_idle resets worker state."""
        progress = PipelineProgress(worker_count=1)
        progress.mark_worker_processing("01", "job-1")
        progress.mark_worker_idle("01")

        ws = progress.workers["01"]
        assert ws.status == WorkerStatus.IDLE
        assert ws.current_job_id is None
        assert ws.job_start_time is None

    def test_mark_worker_processing(self) -> None:
        """mark_worker_processing sets job and start time."""
        progress = PipelineProgress(worker_count=1)
        progress.mark_worker_processing("01", "batch_0003")

        ws = progress.workers["01"]
        assert ws.status == WorkerStatus.PROCESSING
        assert ws.current_job_id == "batch_0003"
        assert ws.job_start_time is not None

    def test_mark_worker_finished(self) -> None:
        """mark_worker_finished sets terminal state."""
        progress = PipelineProgress(worker_count=1)
        progress.mark_worker_processing("01", "job-1")
        progress.mark_worker_finished("01")

        ws = progress.workers["01"]
        assert ws.status == WorkerStatus.FINISHED
        assert ws.current_job_id is None

    def test_record_job_completed_per_source(self) -> None:
        """record_job_completed increments the correct source's count."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"alpha": 5, "beta": 3})
        progress.mark_worker_processing("01", "job-1")

        progress.record_job_completed("01", cost=1.50, data_source="alpha")

        sp = progress.get_source_progress("alpha")
        assert sp is not None
        assert sp.completed == 1
        assert sp.pending == 4

        # beta is unaffected
        sp_beta = progress.get_source_progress("beta")
        assert sp_beta is not None
        assert sp_beta.completed == 0
        assert sp_beta.pending == 3

        # Global totals
        assert progress.completed_jobs == 1
        assert progress.pending_jobs == 7
        assert progress.cumulative_cost == 1.50
        assert progress.workers["01"].status == WorkerStatus.IDLE

    def test_record_job_failed_per_source(self) -> None:
        """record_job_failed increments the correct source's failed count."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"alpha": 5, "beta": 3})
        progress.mark_worker_processing("01", "job-1")

        progress.record_job_failed("01", data_source="beta")

        sp = progress.get_source_progress("beta")
        assert sp is not None
        assert sp.failed == 1
        assert sp.pending == 2

        # alpha is unaffected
        sp_alpha = progress.get_source_progress("alpha")
        assert sp_alpha is not None
        assert sp_alpha.failed == 0

        # Global totals
        assert progress.failed_jobs == 1
        assert progress.pending_jobs == 7
        assert progress.workers["01"].status == WorkerStatus.IDLE

    def test_elapsed_seconds(self) -> None:
        """elapsed_seconds returns positive wall-clock time."""
        progress = PipelineProgress(worker_count=1)
        assert progress.elapsed_seconds >= 0.0

    def test_elapsed_job_seconds(self) -> None:
        """WorkerState.elapsed_job_seconds returns time since job start."""
        progress = PipelineProgress(worker_count=1)
        progress.mark_worker_processing("01", "job-1")

        ws = progress.workers["01"]
        elapsed = ws.elapsed_job_seconds
        assert elapsed is not None
        assert elapsed >= 0.0

    def test_elapsed_job_seconds_idle(self) -> None:
        """elapsed_job_seconds returns None when idle."""
        progress = PipelineProgress(worker_count=1)
        ws = progress.workers["01"]
        assert ws.elapsed_job_seconds is None

    def test_ignores_unknown_worker_id(self) -> None:
        """Operations on unknown worker IDs are silently ignored."""
        progress = PipelineProgress(worker_count=1)
        # Should not raise
        progress.mark_worker_idle("99")
        progress.mark_worker_processing("99", "job-1")
        progress.mark_worker_finished("99")

    def test_multiple_completions_accumulate_cost(self) -> None:
        """Cost accumulates across multiple job completions."""
        progress = PipelineProgress(worker_count=2)
        progress.register_sources({"src": 10})

        progress.record_job_completed("01", cost=1.00, data_source="src")
        progress.record_job_completed("02", cost=2.50, data_source="src")
        progress.record_job_completed("01", cost=0.75, data_source="src")

        assert progress.cumulative_cost == pytest.approx(4.25)
        assert progress.completed_jobs == 3

    def test_get_source_progress_missing(self) -> None:
        """get_source_progress returns None for unknown source."""
        progress = PipelineProgress(worker_count=1)
        assert progress.get_source_progress("nonexistent") is None

    def test_source_progress_pending_computed(self) -> None:
        """SourceProgress.pending is total - completed - failed."""
        sp = SourceProgress(total=10, completed=3, failed=2)
        assert sp.pending == 5


class TestTruncateName:
    def test_short_name_unchanged(self) -> None:
        """Names within max_len are returned unchanged."""
        assert _truncate_name("short", 20) == "short"

    def test_exact_length_unchanged(self) -> None:
        """Names exactly at max_len are returned unchanged."""
        assert _truncate_name("a" * 20, 20) == "a" * 20

    def test_long_name_truncated(self) -> None:
        """Names exceeding max_len are truncated with '...'."""
        result = _truncate_name("cluster-api-provider-aws", 20)
        assert result == "cluster-api-provi..."
        assert len(result) == 20

    def test_custom_max_len(self) -> None:
        """Custom max_len is respected."""
        result = _truncate_name("longname", 5)
        assert result == "lo..."
        assert len(result) == 5


class TestRenderDashboard:
    def test_renders_without_error(self) -> None:
        """render_dashboard produces a valid Rich renderable."""
        progress = PipelineProgress(worker_count=2)
        progress.register_sources({"test-source": 10})
        progress.mark_worker_processing("01", "batch_0003")

        renderable = render_dashboard(progress)

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(renderable)
        output = capture.get()
        assert "k-extract" in output
        assert "batch_0003" in output

    def test_renders_total_bar(self) -> None:
        """Dashboard includes a Total bar."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"src-a": 5, "src-b": 10})

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "Total" in output

    def test_renders_per_source_bars(self) -> None:
        """Dashboard includes per-source bars."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"alpha": 5, "beta": 10})

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "alpha" in output
        assert "beta" in output

    def test_renders_long_source_name_truncated(self) -> None:
        """Long data source names are truncated with '...'."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"cluster-api-provider-aws": 5})

        console = Console(file=None, force_terminal=True, width=100)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "cluster-api-provi..." in output

    def test_renders_idle_workers(self) -> None:
        """Dashboard shows idle workers."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"src": 5})

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "idle" in output

    def test_renders_finished_workers(self) -> None:
        """Dashboard shows finished workers."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"src": 5})
        progress.mark_worker_finished("01")

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "finished" in output

    def test_renders_summary_counts(self) -> None:
        """Dashboard includes completed/failed/pending counts."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"src": 10})
        progress.record_job_completed("01", cost=0.0, data_source="src")
        progress.record_job_failed("01", data_source="src")

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "completed:" in output
        assert "failed:" in output
        assert "pending:" in output

    def test_renders_cost(self) -> None:
        """Dashboard shows cumulative cost."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"src": 5})
        progress.record_job_completed("01", cost=3.14, data_source="src")

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "$3.14" in output

    def test_renders_elapsed_time(self) -> None:
        """Dashboard shows elapsed time."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"src": 5})

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "elapsed:" in output

    def test_renders_k_extract_branding(self) -> None:
        """Header shows 'k-extract' branding."""
        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"src": 5})

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "k-extract" in output

    def test_per_source_completion_reflected_in_bars(self) -> None:
        """Per-source bars update independently."""
        progress = PipelineProgress(worker_count=2)
        progress.register_sources({"alpha": 10, "beta": 5})

        # Complete some jobs from each source
        progress.record_job_completed("01", cost=0.1, data_source="alpha")
        progress.record_job_completed("01", cost=0.1, data_source="alpha")
        progress.record_job_failed("02", data_source="beta")

        assert progress.get_source_progress("alpha") is not None
        assert progress.get_source_progress("alpha").completed == 2  # type: ignore[union-attr]
        assert progress.get_source_progress("beta") is not None
        assert progress.get_source_progress("beta").failed == 1  # type: ignore[union-attr]

        # Total bar should reflect all
        assert progress.completed_jobs == 2
        assert progress.failed_jobs == 1
        assert progress.pending_jobs == 12


class TestConcurrentUpdates:
    @pytest.mark.asyncio
    async def test_concurrent_async_updates(self) -> None:
        """Multiple async tasks can update PipelineProgress without corruption."""
        progress = PipelineProgress(worker_count=3)
        progress.register_sources({"src-a": 30, "src-b": 30, "src-c": 30})

        async def worker_sim(wid: str, source: str, count: int) -> None:
            for i in range(count):
                progress.mark_worker_processing(wid, f"job-{wid}-{i}")
                await asyncio.sleep(0)
                progress.record_job_completed(wid, cost=0.10, data_source=source)
                await asyncio.sleep(0)

        await asyncio.gather(
            worker_sim("01", "src-a", 30),
            worker_sim("02", "src-b", 30),
            worker_sim("03", "src-c", 30),
        )

        assert progress.completed_jobs == 90
        assert progress.pending_jobs == 0
        assert progress.cumulative_cost == pytest.approx(9.0)
        assert progress.failed_jobs == 0

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self) -> None:
        """Concurrent tasks with mixed outcomes maintain correct counts."""
        progress = PipelineProgress(worker_count=2)
        progress.register_sources({"src": 16})

        async def worker_success(wid: str) -> None:
            for i in range(8):
                progress.mark_worker_processing(wid, f"job-{i}")
                await asyncio.sleep(0)
                progress.record_job_completed(wid, cost=0.05, data_source="src")

        async def worker_mixed(wid: str) -> None:
            for i in range(8):
                progress.mark_worker_processing(wid, f"job-{i}")
                await asyncio.sleep(0)
                if i % 3 == 0:
                    progress.record_job_failed(wid, data_source="src")
                else:
                    progress.record_job_completed(wid, cost=0.05, data_source="src")

        await asyncio.gather(
            worker_success("01"),
            worker_mixed("02"),
        )

        # worker_success: 8 completed
        # worker_mixed: 3 failed (i=0,3,6), 5 completed (i=1,2,4,5,7)
        assert progress.completed_jobs == 13
        assert progress.failed_jobs == 3

    @pytest.mark.asyncio
    async def test_cross_source_concurrent_updates(self) -> None:
        """Workers processing jobs from different sources update correctly."""
        progress = PipelineProgress(worker_count=2)
        progress.register_sources({"alpha": 10, "beta": 10})

        async def worker_alpha(wid: str) -> None:
            for i in range(10):
                progress.mark_worker_processing(wid, f"alpha-{i}")
                await asyncio.sleep(0)
                progress.record_job_completed(wid, cost=0.1, data_source="alpha")

        async def worker_beta(wid: str) -> None:
            for i in range(10):
                progress.mark_worker_processing(wid, f"beta-{i}")
                await asyncio.sleep(0)
                progress.record_job_completed(wid, cost=0.2, data_source="beta")

        await asyncio.gather(
            worker_alpha("01"),
            worker_beta("02"),
        )

        sp_alpha = progress.get_source_progress("alpha")
        sp_beta = progress.get_source_progress("beta")
        assert sp_alpha is not None
        assert sp_alpha.completed == 10
        assert sp_alpha.pending == 0
        assert sp_beta is not None
        assert sp_beta.completed == 10
        assert sp_beta.pending == 0
        assert progress.completed_jobs == 20
        assert progress.cumulative_cost == pytest.approx(3.0)
