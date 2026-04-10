# Task 025: Enforce referential integrity — no orphaned edges

**Status:** `in-progress`
**Spec Reference:** specs/agent/agent-tools.md, specs/agent/prompt-generation.md, specs/agent/prompt-patterns.md
**Branch:** task-025
**PR:** (none)
**Review:** (none)

## Description

Spec revision c785dba added requirements for preventing orphaned edges in JSONL output. The tool-level referential integrity check already exists (`tools.py:539-545`), but two gaps remain:

### Gap 1: Error messages must instruct the agent to create the missing entity

Per `agent-tools.md` (Create Mode, step 1): *"If either slug is not found, return an error telling the agent to create the missing entity first."*

Current error messages in `src/k_extract/extraction/tools.py` lines 541 and 545 say:
- `"Source entity not found: {source_slug!r}."`
- `"Target entity not found: {target_slug!r}."`

These must be updated to explicitly tell the agent to create the missing entity first, e.g.:
- `"Source entity not found: {source_slug!r}. Create the entity before creating this relationship."`
- `"Target entity not found: {target_slug!r}. Create the entity before creating this relationship."`

### Gap 2: System prompt must include stub entity instructions

Per `prompt-generation.md`, the system prompt template must include a stub entity instruction block:

> "Before creating any relationship, ensure both the source and target entities exist. If a target entity is referenced in your files but its source files are not in your current job, create a minimal stub entity with the properties you can infer from the reference (e.g., module path from an import statement, repository name from a URL). The stub will be enriched when the target's source files are processed. The relationship tool will reject relationships with missing endpoints."

This should be added to the Quality Rules section of `src/k_extract/extraction/templates/system_prompt.txt`, replacing or expanding the existing generic line "Verify entity existence before creating relationships" (line 49).

## Acceptance Criteria

- [ ] `manage_relationship` error messages for missing source/target entities include guidance telling the agent to create the missing entity first
- [ ] System prompt template includes stub entity instructions per `prompt-generation.md`
- [ ] Existing tests for missing source/target still pass (error messages changed but still detected as errors)
- [ ] New or updated tests verify the error messages contain actionable guidance (e.g., assert "create" or "Create" appears in the error text)
- [ ] Prompt template tests updated if they assert on Quality Rules content

## Relevant Commits

(none yet)
