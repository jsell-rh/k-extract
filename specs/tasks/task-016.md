# Task 016: Runtime Context Window Discovery from Claude Agent SDK

**Status:** `in-progress`
**Spec Reference:** specs/process/job-lifecycle.md (Section: Batching Strategy)
**Branch:** task-016
**PR:** #16
**Review:** (none)

## Description

The job lifecycle spec requires that context window parameters be obtained at runtime from the Claude Agent SDK rather than hardcoded. Currently, `src/k_extract/pipeline/orchestrator.py` uses magic numbers:

```python
CONTEXT_WINDOW = 200_000
OUTPUT_RESERVATION = 50_000
SAFETY_MARGIN = 5_000
```

The spec explicitly states: "context_window — obtained at runtime from the Claude Agent SDK's `ResultMessage.model_usage[model_name]['contextWindow']`" and "No magic numbers: The batching adapts automatically to whatever model the user configures and whatever data they point it at."

The spec also says: "The `contextWindow` and `maxOutputTokens` values can be captured from a lightweight initial agent query (e.g., during the data inventory step) and reused for all subsequent batching calculations."

Reference: specs/process/job-lifecycle.md section "Batching Strategy (k-extract)"

### What to build

1. **Add a lightweight model discovery function in `src/k_extract/extraction/agent.py`:**
   - Make a minimal agent query (e.g., "Respond with OK") to obtain `ResultMessage.model_usage`
   - Extract `contextWindow` and `maxOutputTokens` from `model_usage[model_name]`
   - Return a dataclass with these values
   - Cache the result for reuse

2. **Update `src/k_extract/pipeline/orchestrator.py`:**
   - Call the discovery function at pipeline start (before job generation)
   - Use discovered `contextWindow` instead of `CONTEXT_WINDOW`
   - Use discovered `maxOutputTokens` instead of `OUTPUT_RESERVATION`
   - Keep `SAFETY_MARGIN` as a sensible default (this is a k-extract constant, not a model parameter)
   - Fall back to current hardcoded defaults if discovery fails (e.g., SDK unavailable)

3. **Update `compute_available_tokens` callers:**
   - Pass discovered values instead of module-level constants

### File layout

- `src/k_extract/extraction/agent.py` — Add model discovery function
- `src/k_extract/pipeline/orchestrator.py` — Use discovered values for batching
- `tests/pipeline/test_orchestrator.py` — Test with mocked discovery
- `tests/extraction/test_agent.py` — Test discovery function

### Dependencies

- None (independent of tasks 014 and 015)

## Acceptance Criteria

- [ ] Lightweight agent query discovers `contextWindow` and `maxOutputTokens` at runtime
- [ ] Discovered values are used for job batching instead of hardcoded constants
- [ ] Graceful fallback to defaults if discovery fails
- [ ] No additional cost impact (single minimal query per pipeline run)
- [ ] Tests verify discovery and fallback behavior

## Relevant Commits

(none yet)
