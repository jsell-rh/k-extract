"""Tests for pipeline progress tracking and dashboard rendering."""

from __future__ import annotations

import asyncio

import pytest
from rich.console import Console

from k_extract.pipeline.progress import (
    PipelineProgress,
    WorkerStatus,
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
        assert progress.current_data_source == ""
        assert len(progress.workers) == 3
        assert "01" in progress.workers
        assert "02" in progress.workers
        assert "03" in progress.workers

    def test_set_data_source(self) -> None:
        """set_data_source updates tracking state."""
        progress = PipelineProgress(worker_count=2)
        progress.set_data_source("my-source", total=15, pending=10)

        assert progress.current_data_source == "my-source"
        assert progress.total_jobs == 15
        assert progress.pending_jobs == 10
        assert progress.completed_jobs == 5  # total - pending

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

    def test_record_job_completed(self) -> None:
        """record_job_completed increments counts and adds cost."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("src", total=5, pending=3)
        progress.mark_worker_processing("01", "job-1")

        progress.record_job_completed("01", cost=1.50)

        assert progress.completed_jobs == 3  # 2 already + 1 new
        assert progress.pending_jobs == 2
        assert progress.cumulative_cost == 1.50
        assert progress.workers["01"].status == WorkerStatus.IDLE

    def test_record_job_failed(self) -> None:
        """record_job_failed increments failed count."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("src", total=5, pending=3)
        progress.mark_worker_processing("01", "job-1")

        progress.record_job_failed("01")

        assert progress.failed_jobs == 1
        assert progress.pending_jobs == 2
        assert progress.workers["01"].status == WorkerStatus.IDLE

    def test_elapsed_seconds(self) -> None:
        """elapsed_seconds returns positive wall-clock time."""
        progress = PipelineProgress(worker_count=1)
        # Just verify it's a positive float; exact timing is non-deterministic
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

    def test_pending_does_not_go_negative(self) -> None:
        """pending_jobs stays at 0 when decremented past zero."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("src", total=1, pending=0)
        progress.record_job_completed("01", cost=0.0)
        assert progress.pending_jobs == 0

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
        progress.set_data_source("src", total=10, pending=10)

        progress.record_job_completed("01", cost=1.00)
        progress.record_job_completed("02", cost=2.50)
        progress.record_job_completed("01", cost=0.75)

        assert progress.cumulative_cost == pytest.approx(4.25)
        assert progress.completed_jobs == 3

    def test_set_data_source_resets_worker_states(self) -> None:
        """set_data_source resets all worker states to IDLE."""
        progress = PipelineProgress(worker_count=3)
        progress.set_data_source("source-a", total=5, pending=5)

        # Simulate workers finishing data source A
        progress.mark_worker_finished("01")
        progress.mark_worker_processing("02", "job-x")
        progress.mark_worker_finished("03")

        assert progress.workers["01"].status == WorkerStatus.FINISHED
        assert progress.workers["02"].status == WorkerStatus.PROCESSING
        assert progress.workers["03"].status == WorkerStatus.FINISHED

        # Transition to data source B
        progress.set_data_source("source-b", total=3, pending=3)

        # All workers should be IDLE for the new data source
        for wid in ("01", "02", "03"):
            ws = progress.workers[wid]
            assert ws.status == WorkerStatus.IDLE
            assert ws.current_job_id is None
            assert ws.job_start_time is None

    def test_set_data_source_preserves_cumulative_cost(self) -> None:
        """set_data_source preserves cumulative cost across sources."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("source-a", total=5, pending=5)
        progress.record_job_completed("01", cost=2.50)

        progress.set_data_source("source-b", total=3, pending=3)

        assert progress.cumulative_cost == pytest.approx(2.50)


class TestRenderDashboard:
    def test_renders_without_error(self) -> None:
        """render_dashboard produces a valid Rich renderable."""
        progress = PipelineProgress(worker_count=2)
        progress.set_data_source("test-source", total=10, pending=7)
        progress.mark_worker_processing("01", "batch_0003")

        renderable = render_dashboard(progress)

        # Verify it renders without error by capturing output
        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(renderable)
        output = capture.get()
        assert "test-source" in output
        assert "batch_0003" in output

    def test_renders_idle_workers(self) -> None:
        """Dashboard shows idle workers."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("src", total=5, pending=5)

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "idle" in output

    def test_renders_finished_workers(self) -> None:
        """Dashboard shows finished workers."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("src", total=5, pending=0)
        progress.mark_worker_finished("01")

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "finished" in output

    def test_renders_summary_counts(self) -> None:
        """Dashboard includes completed/failed/pending counts."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("src", total=10, pending=5)
        progress.record_job_completed("01", cost=0.0)
        progress.record_job_failed("01")

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
        progress.set_data_source("src", total=5, pending=5)
        progress.record_job_completed("01", cost=3.14)

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "$3.14" in output

    def test_renders_elapsed_time(self) -> None:
        """Dashboard shows elapsed time."""
        progress = PipelineProgress(worker_count=1)
        progress.set_data_source("src", total=5, pending=5)

        console = Console(file=None, force_terminal=True, width=80)
        with console.capture() as capture:
            console.print(render_dashboard(progress))
        output = capture.get()
        assert "elapsed:" in output


class TestConcurrentUpdates:
    @pytest.mark.asyncio
    async def test_concurrent_async_updates(self) -> None:
        """Multiple async tasks can update PipelineProgress without corruption."""
        progress = PipelineProgress(worker_count=3)
        progress.set_data_source("src", total=90, pending=90)

        async def worker_sim(wid: str, count: int) -> None:
            for i in range(count):
                progress.mark_worker_processing(wid, f"job-{wid}-{i}")
                await asyncio.sleep(0)  # yield to event loop
                progress.record_job_completed(wid, cost=0.10)
                await asyncio.sleep(0)

        await asyncio.gather(
            worker_sim("01", 30),
            worker_sim("02", 30),
            worker_sim("03", 30),
        )

        assert progress.completed_jobs == 90
        assert progress.pending_jobs == 0
        assert progress.cumulative_cost == pytest.approx(9.0)
        assert progress.failed_jobs == 0

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self) -> None:
        """Concurrent tasks with mixed outcomes maintain correct counts."""
        progress = PipelineProgress(worker_count=2)
        progress.set_data_source("src", total=20, pending=20)

        async def worker_success(wid: str) -> None:
            for i in range(8):
                progress.mark_worker_processing(wid, f"job-{i}")
                await asyncio.sleep(0)
                progress.record_job_completed(wid, cost=0.05)

        async def worker_mixed(wid: str) -> None:
            for i in range(8):
                progress.mark_worker_processing(wid, f"job-{i}")
                await asyncio.sleep(0)
                if i % 3 == 0:
                    progress.record_job_failed(wid)
                else:
                    progress.record_job_completed(wid, cost=0.05)

        await asyncio.gather(
            worker_success("01"),
            worker_mixed("02"),
        )

        # worker_success: 8 completed
        # worker_mixed: 3 failed (i=0,3,6), 5 completed (i=1,2,4,5,7)
        assert progress.completed_jobs == 13
        assert progress.failed_jobs == 3
