# Task 014: CLI Job Reset Command

**Status:** `complete`
**Spec Reference:** specs/concurrency/concurrency-model.md (Section 6: Stale Lock / Stale Job Detection and Recovery)
**Branch:** task-014
**PR:** #14
**Review:** (none)

## Description

Implement a CLI command to reset a specific job back to pending status. This fulfills the concurrency spec requirement: "A CLI command to reset a specific job."

The system already implements automatic stale job recovery at startup (`reset_stale_jobs` in `pipeline/jobs.py`) and bulk failed job reset (`reset_failed_jobs`). What's missing is a user-facing CLI command to reset an individual job by ID.

Reference: specs/concurrency/concurrency-model.md section 6 — "The new system needs: ... A CLI command to reset a specific job"

### What to build

1. **Add a `--reset <job_id>` option to `k-extract jobs`:**
   - Requires `--config extraction.yaml` (to locate the database)
   - Resets the specified job's status to `pending`, clears `started_at` and `worker_id`
   - Preserves the `attempt` counter (it was already incremented when claimed)
   - Prints confirmation: job ID, previous status, new status
   - Errors if job ID not found

2. **Add a `--reset-failed` flag to `k-extract jobs`:**
   - Resets ALL failed jobs back to pending
   - Uses the existing `reset_failed_jobs` function from `pipeline/jobs.py`
   - Prints count of reset jobs

### File layout

- `src/k_extract/cli/jobs.py` — Add reset options (extends task 013's jobs command)
- `tests/cli/test_jobs.py` — Add reset tests (extends task 013's test file)

### Dependencies

- Task 013 must be completed first (this extends the `jobs` CLI command created in task 013)

## Acceptance Criteria

- [ ] `k-extract jobs --config extraction.yaml --reset <job_id>` resets a specific job to pending
- [ ] `k-extract jobs --config extraction.yaml --reset-failed` resets all failed jobs to pending
- [ ] Attempt counter is preserved on reset
- [ ] Error message if job ID not found
- [ ] Confirmation output showing what was reset
- [ ] Tests for reset operations

## Relevant Commits

- `f61929a` — feat(task-014): add --reset and --reset-failed CLI options
- Merged via PR #14
