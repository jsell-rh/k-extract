# Task 015: Add Entity Creation Mode to manage_entity Tool

**Status:** `in-progress`
**Spec Reference:** specs/agent/agent-tools.md (Section 3: Manage Entity)
**Branch:** task-015
**PR:** #15
**Review:** (none)

## Description

The `manage_entity` tool currently only supports `mode: "edit"` — it can update properties on existing entity instances but cannot create new ones. The system prompt template already tells agents that `manage_entity` can "Create or update an entity" and the workflow instructs agents to "Check for existing entities before creating new ones to avoid duplicates." However, the tool implementation rejects any mode other than "edit" and errors if the entity doesn't exist.

In the original kartograph-extraction system, entity instances were pre-populated by an ingestion step. k-extract has no such ingestion step — it is a general-purpose extraction framework where agents must create entities from scratch when processing source files. Without entity creation, the extraction pipeline produces no entities.

Reference: specs/agent/agent-tools.md section 3 — the spec describes "Edit-only for pre-populated instances" based on the original system's model. For k-extract's generalized model, agents need to create entities since there is no pre-population step.

### What to build

1. **Add `mode: "create"` to `manage_entity` in `src/k_extract/extraction/tools.py`:**
   - Accept `mode: "create"` in addition to existing `mode: "edit"`
   - In create mode:
     - Validate entity type exists in ontology and is not structural
     - Validate slug format matches `{type}:{canonical-name}` pattern
     - Validate entity DOES NOT already exist (in shared store + staging) — reject duplicates
     - Validate required properties are present
     - Validate property value types against schema
     - Validate tags if present
     - Stage the new entity via `store.stage_entity()`
   - Preserve all existing "edit" mode behavior unchanged

2. **Update `ManageEntityInput` TypedDict:**
   - Change `mode` annotation to accept `"create"` or `"edit"`

3. **Update the tool description string:**
   - Change from "Edit properties on an existing entity instance" to "Create a new entity or edit properties on an existing entity instance"

4. **Verify `validate_and_commit` handles new entities correctly:**
   - The commit logic already supports staged entities that don't exist in the shared store (the merge adds them). Just verify this path works with tests.

### File layout

- `src/k_extract/extraction/tools.py` — Add create mode to manage_entity
- `tests/extraction/test_tools.py` — Add tests for entity creation

### Dependencies

- None (Task 013 and prior tasks are complete)

## Acceptance Criteria

- [ ] `manage_entity` with `mode: "create"` creates a new entity and stages it
- [ ] Create mode rejects entities that already exist (shared or staged)
- [ ] Create mode validates slug format (`{type}:{canonical-name}`)
- [ ] Create mode validates entity type is non-structural
- [ ] Create mode validates required properties are present
- [ ] Create mode validates property types and tag values
- [ ] Edit mode behavior is unchanged
- [ ] `validate_and_commit` successfully commits newly created entities
- [ ] Tests cover create mode: success, duplicate rejection, validation errors

## Relevant Commits

(none yet)
