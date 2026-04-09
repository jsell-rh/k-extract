# Task 017: Pydantic Settings for Runtime Configuration

**Status:** `in-progress`
**Spec Reference:** specs/decisions/technology-choices.md (Configuration, Model Configuration)
**Branch:** task-017
**PR:** #17
**Review:** (none)

## Description

The technology choices spec mandates two things that are not yet implemented:

1. **Pydantic Settings** for runtime configuration with environment variable support: "Pydantic Settings provides typed config with env var support, validation, and defaults in a single source of truth."
2. **Model ID via environment variable**: "The model used by extraction agents is configured via environment variable."

Currently, the model ID is hardcoded as `DEFAULT_MODEL_ID = "default"` in `src/k_extract/pipeline/orchestrator.py`, and no Pydantic Settings class exists despite `pydantic-settings>=2.0` already being a dependency in `pyproject.toml`.

This is distinct from the YAML-based `ExtractionConfig` (which captures per-project extraction decisions). Pydantic Settings handles runtime/environment concerns: which model to use, how to format logs, etc.

### What to build

1. **Add `src/k_extract/config/settings.py` with a Pydantic Settings class:**
   - `model_id` (str): Model ID for extraction agents. Env var: `K_EXTRACT_MODEL`. Default: `"claude-sonnet-4-6@default"`.
   - `log_format` (str): Log output format (`"color"` or `"json"`). Env var: `K_EXTRACT_LOG_FORMAT`. Default: `"color"`.
   - Use `env_prefix = "K_EXTRACT_"` via `SettingsConfigDict`.
   - Provide a module-level `get_settings()` function (cached) for easy access.

2. **Wire `model_id` through the pipeline:**
   - `run_pipeline()` reads `model_id` from settings.
   - Remove `DEFAULT_MODEL_ID = "default"` constant from orchestrator.
   - Pass `model_id` to `compute_fingerprint()` and `store_fingerprint()` (currently uses hardcoded `"default"` — changing the model should change the fingerprint).
   - Pass `model_id` to worker_loop → run_agent's `model` parameter (currently not passed at all, line 130 of `worker.py`).

3. **Wire `log_format` to logging configuration:**
   - `configure_logging()` in `src/k_extract/extraction/logging.py` already supports color vs JSON mode. Wire this to settings.

### File layout

- `src/k_extract/config/settings.py` — New Pydantic Settings class
- `src/k_extract/pipeline/orchestrator.py` — Use settings for model_id, remove DEFAULT_MODEL_ID
- `src/k_extract/pipeline/worker.py` — Accept and forward model_id to run_agent
- `src/k_extract/cli/run.py` — Initialize logging with settings-based log_format
- `tests/config/test_settings.py` — Test defaults and env var overrides

### Dependencies

- None (independent of task 016)

## Acceptance Criteria

- [ ] Pydantic Settings class exists at `src/k_extract/config/settings.py`
- [ ] Model ID is configurable via `K_EXTRACT_MODEL` environment variable
- [ ] Model ID is passed through pipeline → worker → `run_agent(model=...)`
- [ ] Model ID is used in fingerprint computation (changing the model triggers fingerprint mismatch)
- [ ] Log format is configurable via `K_EXTRACT_LOG_FORMAT` environment variable
- [ ] Tests verify settings defaults and env var override behavior

## Relevant Commits

(none yet)
