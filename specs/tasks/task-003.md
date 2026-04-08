# Task 003: Config Schema — extraction.yaml Parsing and Validation

**Status:** `ready-for-review`
**Spec Reference:** specs/process/config-schema.md
**Branch:** task-003
**PR:** #3
**Review:** (none)

## Description

Implement the Pydantic Settings models for parsing and validating the `extraction.yaml` config file. The config file bridges `k-extract init` and `k-extract run` — it must be human-readable, editable, and portable.

Reference: specs/process/config-schema.md.

### What to build

1. **Top-level config model** with fields:
   - `problem_statement` (string, multiline)
   - `data_sources` (list of DataSource objects: `name` + `path`)
   - `ontology` (object with `entity_types` and `relationship_types` lists)
   - `prompts` (object with `system_prompt` and `job_description_template`)
   - `output` (object with `file` and optional `database`)

2. **Ontology config sub-models:**
   - `EntityTypeConfig`: `label`, `description`, `required_properties`, `optional_properties`, `tag_definitions`
   - `RelationshipTypeConfig`: `label`, `description`, `source_entity_type`, `target_entity_type`, `required_properties`, `optional_properties`

3. **YAML loading/saving:**
   - Load from file path, validate, return typed config
   - Save config to YAML file (for `init` output)
   - Round-trip fidelity (load → save produces equivalent YAML)

4. **Validation:**
   - All required fields present
   - Entity type labels are PascalCase
   - Relationship type labels are UPPER_SNAKE_CASE
   - Relationship source/target entity types reference defined entity types
   - Data source paths are non-empty strings
   - Output file path is non-empty

### File layout

- `src/k_extract/config/schema.py` — Pydantic models for the config
- `src/k_extract/config/loader.py` — YAML load/save functions
- `tests/config/` — Unit tests

## Acceptance Criteria

- [ ] Pydantic model covering the full config schema from specs/process/config-schema.md
- [ ] YAML loading with validation errors on malformed input
- [ ] YAML saving (for `init` to produce config files)
- [ ] Cross-field validation (relationship types reference existing entity types)
- [ ] Naming convention enforcement on labels
- [ ] Unit tests for valid configs, invalid configs, and edge cases

## Relevant Commits

- `622b16d` — chore(task-003): begin implementation
- `175eac9` — chore(task-003): add PR number
