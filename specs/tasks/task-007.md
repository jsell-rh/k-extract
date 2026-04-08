# Task 007: Agent Tools — All Five Extraction Tools

**Status:** `ready-for-review`
**Spec Reference:** specs/agent/agent-tools.md
**Branch:** task-007
**PR:** #7
**Review:** specs/reviews/task-007.md

## Description

Implement the five agent tools as Python functions using the Claude Agent SDK's `@tool` decorator. Tools are registered via `create_sdk_mcp_server` and run in-process. Each tool is bound to a specific worker's staging area at instantiation time.

Reference: specs/agent/agent-tools.md.

### What to build

1. **`search_entities`** (readOnlyHint=True):
   - Type Definition mode: return schema for an entity type
   - Get by Slugs: return full instances (auto-resolve entity type)
   - Get by file_path: return instances matching file_path property
   - Filter by Tags: entity type + tags → [{slug, title}] (OR logic)
   - Search by Text: entity type + terms → [{slug, title}] (case-insensitive AND across all properties)
   - Default result cap: 10 with warning if more exist

2. **`search_relationships`** (readOnlyHint=True):
   - Type Definition mode: return schema for relationship type(s) matching forward type or composite key
   - List by Slug: instances involving one or two slugs
   - List All: all instances of a type
   - Default result cap: 10

3. **`manage_entity`**:
   - Load instance from virtual ontology, error if not found
   - Validate entity type is editable (not structural)
   - Validate property types against schema
   - Validate tags against tag_definitions
   - Deep-copy + merge properties (partial update)
   - Stage to worker's private store

4. **`manage_relationship`**:
   - Create mode: auto-detect entity types from slugs, construct composite key, validate exists in schema, reject duplicates, stage
   - Edit mode: load existing, merge properties, stage
   - Structural relationships are read-only

5. **`validate_and_commit`**:
   - Load staged edits for current worker
   - Structural type guard
   - Schema validation (slug, properties, tags)
   - Relationship structure validation (composite key, rel_type)
   - Post-merge validations: slug uniqueness, referential integrity, required properties
   - Job completeness check (all files processed_by_agent=true)
   - Atomic commit under exclusive transaction
   - Return structured errors on failure (agent can fix and retry)

6. **Tool factory pattern:**
   - Factory function that creates tool instances bound to a specific worker_id and staging area
   - MCP server creation via `create_sdk_mcp_server`

### File layout

- `src/k_extract/extraction/tools.py` — All 5 tool functions + factory
- `tests/extraction/test_tools.py` — Tests for each tool and mode

## Acceptance Criteria

- [ ] search_entities with all 5 modes, result capping, and readOnlyHint
- [ ] search_relationships with all 3 modes, result capping, and readOnlyHint
- [ ] manage_entity with validation, merge, and staging
- [ ] manage_relationship with create/edit modes, auto-detect, and duplicate rejection
- [ ] validate_and_commit with full validation suite and atomic commit
- [ ] Tool factory binding tools to a specific worker's staging area
- [ ] All tools return `{"content": [...]}` on success and `{"content": [...], "is_error": True}` on failure
- [ ] Unit tests for every tool mode and validation path

## Relevant Commits

- `15e410e` — feat(task-007): implement all five agent extraction tools
- `2d92abe` — fix(task-007): address review round 1 findings
