# Task 013: CLI Jobs Command — Job Inspection and Error Reporting

**Status:** `needs-revision`
**Spec Reference:** specs/process/extraction-pipeline.md (Job Inspection, User-Facing Error Reporting)
**Branch:** task-013
**PR:** #13
**Review:** specs/reviews/task-013.md

## Description

Implement the `k-extract jobs` command for inspecting job state from the database. This is the diagnostic tool users need to understand extraction progress and investigate failures.

Reference: specs/process/extraction-pipeline.md sections on Job Inspection and User-Facing Error Reporting.

### What to build

1. **CLI command: `k-extract jobs`**
   - Required: `--config extraction.yaml` (to locate the database)
   - Options:
     - `--status <status>` — filter by status (pending, in_progress, completed, failed)
     - `--job <job_id>` — show details for a specific job
     - `--data-source <name>` — filter by data source
   - Default (no filters): show summary counts by status

2. **Summary display:**
   ```
   Jobs: 107 completed, 3 failed, 0 pending, 0 in_progress (110 total)
   ```

3. **Filtered listing:**
   - Show job_id, data_source, status, file_count, total_characters, attempt
   - For failed jobs: include error_message

4. **Job detail view:**
   - All fields from the job record
   - File list
   - Timestamps (created, started, completed)
   - Error message if failed

### File layout

- `src/k_extract/cli/jobs.py` — CLI command
- `tests/cli/test_jobs.py` — Tests

## Acceptance Criteria

- [ ] `k-extract jobs --config extraction.yaml` shows summary by default
- [ ] `--status failed` filters to failed jobs with error messages
- [ ] `--job <id>` shows full detail for a specific job
- [ ] `--data-source <name>` filters by data source
- [ ] Clean, readable terminal output
- [ ] Tests for all display modes

## Relevant Commits

- `05f9dd4` — feat(task-013): implement CLI jobs command
