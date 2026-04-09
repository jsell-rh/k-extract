# Task 022: Fix Deduplication Return Behavior in manage_entity and manage_relationship

**Status:** `ready-for-review`
**Spec Reference:** specs/agent/agent-tools.md, specs/process/output-format.md
**Branch:** task-022
**PR:** #22
**Review:** (none)

## Description

The `manage_entity` and `manage_relationship` tools currently return `is_error=True` when a duplicate entity or relationship is detected during create mode. The spec requires these tools to return the **existing entity/relationship as a success response** instead.

### Current Behavior (tools.py)

**manage_entity** (line ~451): When an entity with the given slug already exists in create mode, returns:
```python
return _err(f"Entity already exists: {slug!r}. Use mode='edit' to modify.")
```

**manage_relationship** (line ~577): When a relationship with the same source+target+type already exists in create mode, returns:
```python
return _err(f"Relationship already exists: {composite_key!r} from {source_slug!r} to {target_slug!r}. Use mode='edit' to modify.")
```

### Required Behavior (from spec)

**agent-tools.md Section 3 (Manage Entity):**
> "If it exists: return the existing entity to the agent. Do not emit a duplicate CREATE. The agent can then decide to UPDATE it if properties need changing."

> "If a match is found, the tool returns the existing entity with a message indicating it already exists. The agent can then use the existing entity's slug/ID for relationships without creating a duplicate."

**agent-tools.md Section 4 (Manage Relationship):**
> "If it already exists, return the existing relationship — do not emit a duplicate CREATE."

**output-format.md (line 94):**
> "the tool returns the existing entity instead of emitting a duplicate CREATE"

### Why This Matters

Returning `is_error=True` causes agents to treat duplicate detection as a failure, potentially wasting tokens on error recovery. The spec's design is intentional: returning the existing entity as a success lets the agent smoothly use the data (e.g., for creating relationships) without unnecessary retries.

### Changes Required

1. In `manage_entity` create mode: when the entity already exists, return `_ok(...)` with the existing entity data and a `status: "already_exists"` indicator
2. In `manage_relationship` create mode: when the relationship already exists, return `_ok(...)` with the existing relationship data and a `status: "already_exists"` indicator
3. Update tests in `tests/extraction/test_tools.py` to expect success responses instead of errors for duplicate detection

## Acceptance Criteria

- [ ] `manage_entity` in create mode returns a success response (not `is_error`) containing the existing entity data when a duplicate slug is found
- [ ] `manage_relationship` in create mode returns a success response (not `is_error`) containing the existing relationship data when a duplicate is found
- [ ] Response includes a clear indicator (e.g., `"status": "already_exists"`) so the agent knows this was not a fresh creation
- [ ] All existing tests updated to match new behavior
- [ ] All tests pass (`uv run pytest`)
- [ ] Lint and type checks pass (`uv run ruff check`, `uv run pyright`)

## Relevant Commits

- `52d8373` — chore(task-022): begin implementation
- `2e471a6` — chore(task-022): add PR number
