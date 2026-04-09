# Task 024: N+1 Progress Bars (Per-Source + Total)

**Status:** `in-progress`
**Spec Reference:** specs/process/extraction-pipeline.md
**Branch:** task-024
**PR:** #24
**Review:** (none)

## Description

Refactor the progress dashboard to show **n+1 progress bars**: one per data source plus one overall total bar, as specified in the updated extraction-pipeline spec (commit `1870089`).

### Current Behavior

`PipelineProgress` (`src/k_extract/pipeline/progress.py`) tracks a single set of counters (`total_jobs`, `completed_jobs`, `failed_jobs`) and `render_dashboard` renders a single progress bar. The `set_data_source` method resets these counters per source — a model designed for the old per-source sequential processing.

### Required Behavior (from spec)

```
  k-extract  elapsed: 1h 08m  cost: $31.35

  Total                ━━━━━━━━━━━━━╺━━━━━━━━━━━━━━━━  93/714 jobs
  hypershift           ━━━━━━━━━━━━━━━━━━━━╺━━━━━━━━━━  87/654 jobs
  ocm-api-model        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━╺━━  32/34 jobs
  cluster-api-prov...  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╺━   3/26 jobs
  rosa                 ╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   0/42 jobs

  worker-01:  processing 47bffb3abbcc474c (1793s)
  worker-02:  processing a1b2c3d4e5f67890 (423s)
  worker-03:  idle

  completed: 93  failed: 29  pending: 592
```

Since all jobs are generated upfront (Task 023) and workers claim from a global queue, the dashboard can show accurate totals from the start. Per-source bars update as their jobs complete regardless of processing order.

### Changes Required

**`src/k_extract/pipeline/progress.py`:**
- Replace `set_data_source` with an initialization method that registers all sources with their total job counts upfront (e.g., `register_sources(sources: dict[str, int])` mapping source name → total jobs)
- Track per-source completed and failed counts independently
- `record_job_completed` and `record_job_failed` must accept `data_source: str` to attribute the result to the correct source bar
- Update `render_dashboard` to render n+1 bars:
  - "Total" bar: sum of all sources' totals and completions
  - One bar per data source (ordered as in config)
  - Truncate long data source names with "..." (as shown in spec example)
- Header should show "k-extract" branding instead of a single data source name
- Summary line at the bottom: global completed, failed, pending counts

**`src/k_extract/pipeline/orchestrator.py`:**
- After upfront job generation, call the new `register_sources` method with per-source job counts
- Remove per-source `set_data_source` calls
- Launch a single Live dashboard that persists for the entire run

**`src/k_extract/pipeline/worker.py`:**
- Pass `job.data_source` to `progress.record_job_completed` and `progress.record_job_failed`

**Tests:**
- Update `tests/pipeline/test_progress.py` to test multi-source tracking, per-source attribution, and the new dashboard rendering

## Acceptance Criteria

- [ ] Dashboard shows n+1 progress bars: one "Total" bar plus one per data source
- [ ] Per-source bars update independently as their jobs complete (regardless of processing order)
- [ ] Total bar accurately reflects the sum across all sources
- [ ] Header shows "k-extract" branding with elapsed time and cost
- [ ] Long data source names are truncated with "..."
- [ ] Summary line shows global completed, failed, and pending counts
- [ ] `PipelineProgress` supports registering all sources upfront with total counts
- [ ] `record_job_completed` and `record_job_failed` accept and correctly attribute a `data_source` parameter
- [ ] All existing tests pass (`uv run pytest`)
- [ ] Lint and type checks pass (`uv run ruff check`, `uv run pyright`)

## Relevant Commits

(none yet)
