# Task 008: Prompt Generation — Template Composition and LLM Guidance

**Status:** `ready-for-review`
**Spec Reference:** specs/agent/prompt-generation.md, specs/agent/prompt-patterns.md
**Branch:** task-008
**PR:** #8
**Review:** (none)

## Description

Implement the prompt composition system that produces the `system_prompt` and `job_description_template` fields in the config file. This includes the static template (shipped with k-extract), LLM-generated extraction guidance, and per-job variable substitution at runtime.

Reference: specs/agent/prompt-generation.md, specs/agent/prompt-patterns.md.

### What to build

1. **Static system prompt template** (shipped with k-extract as a template file):
   - Role definition (knowledge extraction agent)
   - Workflow steps (read files, extract entities/relationships, validate and commit)
   - Tool usage rules
   - Efficiency constraints (don't narrate, work autonomously)
   - Quality constraints (don't create duplicates, verify before creating, consistent slugs)
   - Access permissions (read-only built-in tools, mutations via custom tools only)
   - Completion protocol (run validate_and_commit tool)

2. **LLM-generated extraction guidance** (produced during `init`):
   - Input: confirmed ontology + problem statement
   - Output: natural language extraction instructions per entity type and relationship type
   - Describes what each type represents, when to create one, what properties to capture
   - Problem-statement-driven priorities

3. **Prompt composition flow:**
   - Merge static template + LLM-generated guidance → `system_prompt`
   - Create `job_description_template` with `{job_id}`, `{file_count}`, `{total_characters}`, `{file_list}` placeholders

4. **Per-job substitution** (at runtime):
   - Substitute template variables into `job_description_template` for each job
   - No LLM call at runtime — pure string substitution

### File layout

- `src/k_extract/extraction/prompts.py` — Prompt composition and substitution logic
- `src/k_extract/extraction/templates/` — Static template files shipped with package
- `tests/extraction/test_prompts.py` — Tests

## Acceptance Criteria

- [ ] Static system prompt template covering all universal sections from spec
- [ ] LLM-generated extraction guidance function (ontology + problem → guidance text)
- [ ] Composition: template + guidance → complete system_prompt
- [ ] Job description template with variable placeholders
- [ ] Per-job substitution function
- [ ] Templates are package data files (not hardcoded strings)
- [ ] Unit tests for composition and substitution

## Relevant Commits

- `32f373c` — chore(task-008): begin implementation
- `8e11943` — chore(task-008): add PR number
