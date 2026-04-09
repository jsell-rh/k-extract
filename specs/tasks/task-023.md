# Task 023: Upfront Job Generation and Global Worker Queue

**Status:** `ready-for-review`
**Spec Reference:** specs/process/extraction-pipeline.md
**Branch:** task-023
**PR:** #23
**Review:** (none)

## Description

Restructure the pipeline orchestrator to generate all jobs upfront and use a single global worker queue, as specified in the updated extraction-pipeline spec (commit `1870089`).

### Current Behavior

The orchestrator (`src/k_extract/pipeline/orchestrator.py`) processes data sources **sequentially**:

```python
for ds in config.data_sources:
    # generate jobs for this source
    # launch workers scoped to this source
    # wait for all workers to finish
    # move to next source
```

Workers receive a `data_source` parameter and only claim jobs from that source. The per-source loop at line 369 generates jobs, launches workers, waits, then repeats for the next source.

### Required Behavior (from spec)

1. **Generate all jobs upfront** — Before any worker launches, iterate all configured data sources, enumerate their files, batch into jobs, and write everything to the database. This gives the system a complete picture of total work scope before extraction begins.

2. **Launch workers once** — After all jobs exist, launch N worker instances via `asyncio.gather`. Workers run until no pending jobs remain.

3. **Global queue** — `claim_next_job` no longer filters by `data_source`. Workers claim the next pending job by global order number, regardless of source. A worker finishing a job from source A may next pick up a job from source B.

4. **Source path from job** — Since workers are no longer scoped to a data source, the `cwd` for the agent must be determined from the job's `data_source` field, mapped to the corresponding path from the config. The worker needs access to the data source name→path mapping.

### Changes Required

**`src/k_extract/pipeline/orchestrator.py`:**
- Move job generation into a dedicated upfront phase (iterate all data sources, generate and insert all jobs before any worker launches)
- Remove the per-data-source worker launch loop
- Launch workers once with a global queue (no `data_source` parameter)
- Build and pass a `source_paths: dict[str, Path]` mapping (data source name → filesystem path) to each worker
- Adapt max_jobs and shared_counter to work with the single global launch
- Update setup spinner to show job generation progress across all sources

**`src/k_extract/pipeline/worker.py`:**
- Remove `data_source` and `source_path` parameters from `worker_loop`
- Add `source_paths: dict[str, Path]` parameter (name→path mapping)
- Call `claim_next_job(session, worker_id)` without `data_source` filter (global claiming)
- Resolve `source_path = source_paths[job.data_source]` after claiming each job
- Pass `job.data_source` to `run_agent` and `generate_creates` instead of a fixed `data_source`

**`src/k_extract/pipeline/jobs.py`:**
- Remove the `data_source` parameter from `claim_next_job` (it should always claim globally now). The existing `data_source=None` code path is already correct — just remove the parameter and the per-source branch.

**Tests:**
- Update `tests/pipeline/test_orchestrator.py` to verify upfront job generation (all sources jobbed before workers launch)
- Update `tests/pipeline/test_worker.py` to verify global claiming and source path resolution
- Update `tests/pipeline/test_jobs.py` to remove data_source parameter from claim tests

## Acceptance Criteria

- [ ] All jobs for all data sources are generated and persisted to the database before any worker launches
- [ ] Workers claim from a single global queue (no data_source filter in `claim_next_job`)
- [ ] Worker resolves source_path from the job's `data_source` field via a name→path mapping
- [ ] The `claim_next_job` function no longer accepts a `data_source` parameter
- [ ] max_jobs cap works correctly with the global queue (shared counter across all workers)
- [ ] Resume logic still works: on resume, existing jobs from all sources are preserved and stale/failed jobs are reset
- [ ] Setup spinner reflects upfront job generation progress (e.g., "Generating jobs for source-1 (1/3)...")
- [ ] All existing tests pass (`uv run pytest`)
- [ ] Lint and type checks pass (`uv run ruff check`, `uv run pyright`)

## Relevant Commits

- `d6d4ee4` — chore(task-023): begin implementation
- `1fe446b` — chore(task-023): add PR number
- `64412ab` — feat(task-023): upfront job generation and global worker queue
