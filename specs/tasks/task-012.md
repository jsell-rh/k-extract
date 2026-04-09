# Task 012: Extraction Pipeline — `k-extract run` Orchestrator and Workers

**Status:** `ready-for-review`
**Spec Reference:** specs/process/extraction-pipeline.md, specs/concurrency/concurrency-model.md
**Branch:** task-012
**PR:** #12
**Review:** specs/reviews/task-012.md

## Description

Implement the `k-extract run` command and the extraction pipeline orchestrator that coordinates multiple concurrent worker agents processing jobs.

Reference: specs/process/extraction-pipeline.md, specs/concurrency/concurrency-model.md section 9.

### What to build

1. **CLI command: `k-extract run`**
   - Required: `--config extraction.yaml`
   - Options: `--workers N` (concurrent workers, default sensible), `--max-jobs N` (cap total jobs), `--force` (discard previous state), `--log-conversations` (enable conversation logging), `--db` (override database path)

2. **Pipeline initialization:**
   - Load and validate config (Task 003)
   - Compute environment fingerprint (Task 009)
   - Resume logic: check previous fingerprint, decide fresh/resume/hard-stop
   - Initialize database and ontology store (Tasks 005, 006)
   - Reset stale in_progress jobs to pending

3. **Job generation:**
   - Discover files from all data sources (Task 009)
   - Batch files using context-window-based algorithm (Task 005)
   - Insert jobs into database

4. **DEFINE emission:**
   - Emit DEFINE operations for all entity/relationship types from config (Task 004)
   - Write to JSONL output file before any CREATE operations

5. **Worker execution (competing-workers model):**
   - Launch N worker instances concurrently (asyncio.gather)
   - Each worker loops: claim next job → set up workspace → run agent → record result
   - Agent instantiation per job (Task 010) with job-specific tools (Task 007)
   - Per-job prompt substitution (Task 008)
   - On agent success: validate_and_commit handles ontology update, emit CREATE operations to JSONL
   - On agent failure: mark job as failed with error details
   - Track total jobs processed for --max-jobs cap

6. **Completion and reporting:**
   - Summary: N/M jobs completed, failures listed
   - Total cost
   - Output file path and line count
   - Instruction to re-run for retrying failed jobs

7. **Data source processing:**
   - Process data sources in configured order
   - Cross-source relationships: entities from earlier sources visible when processing later sources
   - JSONL output appended across sources

### File layout

- `src/k_extract/cli/run.py` — CLI command
- `src/k_extract/pipeline/orchestrator.py` — Pipeline orchestrator
- `src/k_extract/pipeline/worker.py` — Worker loop
- `tests/pipeline/test_orchestrator.py` — Orchestrator tests
- `tests/pipeline/test_worker.py` — Worker tests

## Acceptance Criteria

- [ ] `k-extract run --config extraction.yaml` executes the pipeline
- [ ] Environment fingerprint check with resume/fresh/hard-stop behavior
- [ ] Job generation from data source files using context-window batching
- [ ] DEFINE operations emitted before any CREATE
- [ ] Concurrent worker execution with asyncio.gather
- [ ] Worker loop: claim → agent → record with proper error handling
- [ ] Job failure isolation (one failure doesn't affect other workers)
- [ ] Completion summary with stats, cost, and failed job details
- [ ] JSONL output is valid and appendable across resume
- [ ] Tests for orchestration flow, worker lifecycle, and failure handling

## Relevant Commits

- `e48d3b5` — feat(task-012): implement k-extract run orchestrator and workers
- `b857cd8` — fix(task-012): address all review findings from round 1
- `de94f37` — fix(task-012): address all review findings from round 2
