# Task 018: End-to-End Integration Test (No Mocking)

**Status:** `ready-for-review`
**Spec Reference:** specs/process/extraction-pipeline.md, specs/process/output-format.md, specs/agent/agent-architecture.md
**Branch:** task-018
**PR:** #18
**Review:** (none)

## Description

Add a true end-to-end integration test that exercises the full `k-extract run` pipeline against the real Claude API — no mocking of the agent, SDK, or model capabilities. This validates that all components (config loading, fingerprinting, job generation, agent instantiation, tool execution, ontology store, JSONL output) work together correctly against the live system.

Existing tests in `tests/pipeline/test_orchestrator.py` mock `discover_model_capabilities` and `run_agent`. This task adds a test that calls `run_pipeline()` end-to-end with real API calls to verify the full integration contract.

### What to build

1. **Add `tests/e2e/test_full_pipeline.py`:**
   - Create a small, self-contained test data source (2-3 short text files describing a tiny domain — e.g., a few software components and their relationships).
   - Create a minimal `extraction.yaml` config with:
     - A simple ontology (2-3 entity types, 1-2 relationship types)
     - A focused problem statement
     - The test data source path
     - Output file and database paths in `tmp_path`
   - Call `run_pipeline()` with `workers=1`, `max_jobs=1`, and `force=True` to keep cost and duration minimal.
   - Assert:
     - `PipelineResult.completed_jobs >= 1`
     - `PipelineResult.failed_jobs == 0`
     - `PipelineResult.total_cost > 0` (proves real API was called)
     - Output JSONL file exists and contains DEFINE operations
     - Output JSONL file contains at least one CREATE operation with valid structure (has `op`, `type`, `id`, `label`, `set_properties`)
     - Database contains job records with `completed` status
     - Every DEFINE operation has the correct fields (`op`, `type`, `label`, `description`, `required_properties`)
     - Every CREATE node operation has required system properties (`slug`, `data_source_id`, `source_path`)

2. **Mark the test with `@pytest.mark.e2e`:**
   - Register a custom `e2e` marker in `pyproject.toml` under `[tool.pytest.ini_options]` markers.
   - The test should be skipped by default in CI and normal `pytest` runs. Use `pytest.mark.skipif` conditioned on an environment variable (e.g., `K_EXTRACT_E2E=1`) or use `-m e2e` marker selection. The test must NOT run in CI (it requires an API key and costs money).
   - Add a note in the test docstring explaining how to run it: `K_EXTRACT_E2E=1 uv run pytest -m e2e`

3. **Add `tests/e2e/__init__.py`** (empty, for package structure).

4. **Config fixture helper:**
   - Create a helper function within the test file that builds a valid `extraction.yaml` in `tmp_path` with all required fields populated.
   - The config should use the real default prompts from `src/k_extract/extraction/templates/` (load them via the existing template loading mechanism or inline minimal prompts).
   - Data source path should point to a temp directory with the test files.

### File layout

- `tests/e2e/__init__.py` — Package init
- `tests/e2e/test_full_pipeline.py` — End-to-end test
- `pyproject.toml` — Add `e2e` marker registration

### Dependencies

- Requires a valid `ANTHROPIC_API_KEY` in the environment at runtime
- All 17 prior tasks must be complete (this tests the assembled system)

## Acceptance Criteria

- [ ] End-to-end test exists at `tests/e2e/test_full_pipeline.py`
- [ ] Test calls `run_pipeline()` with no mocked components
- [ ] Test uses real Claude API (verified by `total_cost > 0`)
- [ ] Test validates JSONL output contains both DEFINE and CREATE operations
- [ ] Test validates database job records show completed status
- [ ] Test is marked `@pytest.mark.e2e` and skipped by default (does not run in CI)
- [ ] `e2e` marker is registered in `pyproject.toml`
- [ ] Test runs successfully with `K_EXTRACT_E2E=1 uv run pytest -m e2e -v`

## Relevant Commits

- `37d029e` — chore(task-018): begin implementation
- `239b12e` — chore(task-018): add PR number
