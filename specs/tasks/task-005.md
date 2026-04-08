# Task 005: Database Layer — SQLAlchemy Models and Job Lifecycle

**Status:** `in-progress`
**Spec Reference:** specs/process/job-lifecycle.md, specs/concurrency/concurrency-model.md
**Branch:** task-005
**PR:** #5
**Review:** (none)

## Description

Implement the SQLAlchemy models for the SQLite database and the job lifecycle state machine. This covers job storage, state transitions, batching, atomic claiming, and stale job recovery.

Reference: specs/process/job-lifecycle.md, specs/concurrency/concurrency-model.md sections 2–3, 6.

### What to build

1. **SQLAlchemy models:**
   - `Job` table: `job_id`, `order`, `data_source`, `files` (JSON), `file_count`, `total_characters`, `status`, `created_at`, `started_at`, `completed_at`, `agent_instance_id`, `attempt`, `error_message`
   - `EnvironmentFingerprint` table: `fingerprint` (hash), `created_at`, `config_hash`, `model_id`
   - Engine/session factory for SQLite (WAL mode for concurrent access)
   - Table creation on first use

2. **Job state machine:**
   - States: `pending`, `in_progress`, `completed`, `failed`
   - Transitions: pending → in_progress, in_progress → completed, in_progress → failed
   - No automatic retry from failed

3. **Job batching algorithm** (context-window-based):
   - Input: list of files with character counts, available token budget
   - `available_tokens = context_window - prompt_overhead - output_reservation - safety_margin`
   - Token estimation: chars / ~4
   - Folder-aware grouping (files in same directory stay together)
   - Oversized files get their own job
   - Output: list of Job records ready to insert

4. **Atomic job claiming:**
   - Single UPDATE...WHERE...RETURNING SQL pattern
   - Claim next pending job by order, assign worker ID, set started_at, increment attempt

5. **Job completion/failure recording:**
   - Mark completed with timestamp
   - Mark failed with error message and timestamp

6. **Stale job detection and recovery:**
   - Timeout-based: reset in_progress jobs older than configurable timeout (default 60 min)
   - Startup reset: all in_progress jobs → pending at run start

### File layout

- `src/k_extract/pipeline/database.py` — Engine, session factory, SQLAlchemy models
- `src/k_extract/pipeline/jobs.py` — Job lifecycle (batching, claiming, completion, stale recovery)
- `tests/pipeline/test_database.py` — Model tests
- `tests/pipeline/test_jobs.py` — Lifecycle tests

## Acceptance Criteria

- [ ] SQLAlchemy models for Job and EnvironmentFingerprint
- [ ] SQLite engine with WAL mode, session factory, auto table creation
- [ ] Job batching: context-window-based with folder-aware grouping and oversized file handling
- [ ] Atomic job claiming via single SQL statement
- [ ] Job completion and failure recording with timestamps
- [ ] Stale job recovery (timeout-based and startup reset)
- [ ] Unit tests for batching algorithm, state transitions, and claiming

## Relevant Commits

(none yet)
