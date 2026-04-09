# Task 020: Live Progress Dashboard for `k-extract run`

**Status:** `in-progress`
**Spec Reference:** specs/process/extraction-pipeline.md, specs/agent/agent-architecture.md
**Branch:** task-020
**PR:** #20
**Review:** (none)

## Description

The `k-extract run` command currently produces zero terminal output during extraction — users stare at a blank terminal for minutes to hours until a final summary prints at the very end. This task adds a Rich Live dashboard that shows real-time progress: which workers are active, what jobs they're processing, how many jobs are complete, cumulative cost, and elapsed time. The dashboard updates in-place without scrolling, giving the user full visibility into the pipeline without cluttering the terminal.

### What to build

1. **Create `src/k_extract/pipeline/progress.py` — Progress tracking and display:**
   - A `PipelineProgress` class that tracks live pipeline state:
     - Total jobs generated (per data source and overall)
     - Jobs completed, failed, pending (updated as workers report results)
     - Per-worker status: idle, processing (with current job ID), finished
     - Cumulative cost (updated as workers finish jobs)
     - Elapsed wall-clock time (from pipeline start)
     - Current data source being processed
   - A `render_dashboard(progress: PipelineProgress) -> rich.table.Table` function (or equivalent Rich renderable) that formats the progress state into a compact, information-dense display. The layout should include:
     - A header line: data source name, elapsed time, cumulative cost
     - A progress bar or fraction: `[####------] 7/15 jobs` 
     - Per-worker status rows: `worker-01: processing batch_0003 (42s)` / `worker-02: idle` / `worker-03: processing batch_0007 (18s)`
     - A compact summary line: `completed: 5  failed: 1  pending: 9`
   - Thread-safety: `PipelineProgress` must be safe for concurrent updates from multiple async worker tasks. Use simple attribute assignments (which are atomic in CPython for single values) or an `asyncio.Lock` if compound updates are needed.

2. **Integrate progress tracking into the orchestrator (`orchestrator.py`):**
   - Create a `PipelineProgress` instance at pipeline start.
   - Wrap the worker execution phase in a `rich.live.Live` context that renders the dashboard and refreshes periodically (e.g., every 0.5s via `Live(refresh_per_second=2)`).
   - Pass the `PipelineProgress` instance to each worker so they can report status changes.
   - Update progress when: a data source starts processing, jobs are generated, a worker claims a job, a worker completes/fails a job.
   - When the Live display exits (all workers done for a data source), the final state should be printed as static output so it remains visible in the scrollback.

3. **Integrate progress reporting into the worker loop (`worker.py`):**
   - Accept a `PipelineProgress` (or a callback protocol) parameter in `worker_loop()`.
   - Report status transitions:
     - Worker starts: mark as idle
     - Job claimed: mark as processing with job ID and start time
     - Job completed: mark as idle, increment completed count, add cost
     - Job failed: mark as idle, increment failed count
   - Progress reporting must not interfere with worker correctness — if the progress object is `None` (e.g., in tests), skip all reporting silently. Use an `if progress is not None:` guard or a no-op default.

4. **Enhance the `run` CLI command (`run.py`):**
   - Replace the bare `asyncio.run(run_pipeline(...))` with Rich console output.
   - Show a spinner during the setup phase (config loading, fingerprinting, resume evaluation, job generation) before workers start.
   - The live dashboard runs during worker execution.
   - The final summary (already implemented) should use Rich formatting consistent with Task 019's display layer.
   - Use the `Console` from `display.py` (Task 019) for all output.

5. **Tests in `tests/pipeline/test_progress.py`:**
   - Test `PipelineProgress` state tracking: incrementing completed/failed counts, updating worker status, computing elapsed time.
   - Test `render_dashboard()` produces a valid Rich renderable (instantiate and call `rich.console.Console.capture()` to verify it renders without error).
   - Test that worker loop still functions correctly when `progress=None` (existing test compatibility).
   - Test concurrent updates to `PipelineProgress` from multiple async tasks don't corrupt state.

### File layout

- `src/k_extract/pipeline/progress.py` — Progress state tracker and Rich dashboard renderer
- `src/k_extract/pipeline/orchestrator.py` — Updated to create progress tracker and wrap workers in Live display
- `src/k_extract/pipeline/worker.py` — Updated to report status transitions to progress tracker
- `src/k_extract/cli/run.py` — Updated to use Rich console and show setup spinner
- `tests/pipeline/test_progress.py` — Tests for progress tracking and rendering

### Dependencies

- Task 019 must be complete (depends on `display.py` console and spinner utilities)

## Acceptance Criteria

- [ ] `src/k_extract/pipeline/progress.py` exists with `PipelineProgress` class and `render_dashboard()` function
- [ ] During `k-extract run`, a live-updating dashboard shows: job progress, worker status, elapsed time, cost
- [ ] Dashboard refreshes in-place (no terminal scroll) using `rich.live.Live`
- [ ] Per-worker status shows current job ID and elapsed time for active jobs
- [ ] Workers report status transitions (idle/processing/complete/failed) to the progress tracker
- [ ] Setup phase (before workers start) shows a spinner
- [ ] Final summary uses Rich formatting
- [ ] Existing worker tests pass without modification (progress parameter is optional/None-safe)
- [ ] New tests cover progress state tracking, dashboard rendering, and concurrent update safety
- [ ] `uv run pytest` passes, `uv run pyright` clean, `uv run ruff check` clean

## Relevant Commits

(none yet)
