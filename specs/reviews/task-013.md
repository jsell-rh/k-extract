# Review: Task 013

## Round 1

- [ ] `_show_job_detail` (src/k_extract/cli/jobs.py:132-154) omits the `agent_instance_id` field. The task requires "All fields from the job record" and the Job Data Model spec (specs/process/job-lifecycle.md, line 41) explicitly defines `agent_instance_id` ("ID of the worker that claimed this job"). The SQLAlchemy model includes this field (src/k_extract/pipeline/database.py:58) but the detail view displays 12 of 13 fields, skipping `agent_instance_id`. The corresponding test (tests/cli/test_jobs.py, `TestJobDetail`) also does not assert on this field.
