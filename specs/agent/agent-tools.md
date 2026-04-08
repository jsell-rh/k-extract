# Agent Tools Spec

Distilled from `kartograph-extraction` codebase. Captures tool contracts for reimplementation with dynamic ontologies.

---

## Overview

Agents interact with the knowledge graph through **custom Python function tools** registered via the Claude Agent SDK's `@tool` decorator. All tools run in-process — no subprocess spawning, no CLI parsing. Tools are categorized as: read (search), write (stage changes), and commit (validate and apply).

Each tool function:
- Is bound to a specific agent instance's staging area at instantiation time (via closure or factory)
- Receives validated arguments as `dict[str, Any]`
- Returns `{"content": [{"type": "text", "text": "..."}]}` on success
- Returns `{"content": [...], "is_error": True}` on validation failure

### Data Flow Architecture

```
Shared Ontology Store (SQLite)
       │
       ├── Search tools (readOnlyHint=True): READ shared + merge with staged → "virtual view"
       │
       ├── Manage tools: WRITE to the agent's private staging area only
       │
       └── Commit tool: READ staged + shared, VALIDATE, WRITE merged result (under transaction)
```

### Concurrency Model

Read-only tools are annotated with `readOnlyHint=True`, allowing the SDK to invoke them in parallel. Write tools serialize access to shared state via database transactions. The commit step uses an exclusive transaction to prevent concurrent commits from corrupting shared state.

---

## 1. Search Entities

**Purpose:** Query entity instances from the virtual ontology (master + staged edits merged).

**Access:** Read-only. Reads shared ontology and the agent's staged changes.

**Side effects:** None.

### Modes

| Mode | Inputs | Returns |
|------|--------|---------|
| **Type Definition** | An entity type name | Schema: `instance_count`, `description`, `tier`, `required_properties`, `optional_properties`, `property_definitions`, `property_defaults`, `tag_definitions` |
| **Get by Slugs** | One or more slugs (entity type optional, auto-resolved) | Full instance objects. Each result includes `entity_type` when type is auto-resolved. |
| **Get by file_path** | A file path | Full instance(s) matching the `file_path` property. Searches all file-based entity types. Errors if not found. |
| **Filter by Tags** | An entity type + one or more tags | `[{slug, title}]` — entities with ANY of the specified tags (OR logic). Warns on tags not in `tag_definitions`. |
| **Search by Text** | An entity type + search terms | `[{slug, title}]` — case-insensitive AND search across all string/list properties and slug. |

### Common Modifiers (Filter/Search modes)

| Modifier | Effect |
|----------|--------|
| Result limit | Cap results at N (overrides default 10) |
| Show all | Return all matches (no cap) |
| Include specific fields | Return slug + specified fields instead of slug+title |

### Default Result Cap

Filter and search modes default to returning the first 10 results. If more exist, a warning is emitted stating the total count. This prevents overwhelming the agent with large result sets.

### Generalizable Contract

- **Type Definition mode** is how the agent learns the schema for an entity type at runtime. This is critical for dynamic ontologies: the agent discovers property definitions, required fields, and valid tags from the ontology itself, not from hardcoded knowledge.
- **File-path lookup** maps source file paths to entity slugs for relationship creation.
- The search modes enable the agent to find existing entities to avoid duplication and to resolve cross-references.

---

## 2. Search Relationships

**Purpose:** Query relationship instances from the virtual ontology.

**Access:** Read-only. Reads shared ontology and the agent's staged changes.

**Side effects:** None.

### Modes

| Mode | Inputs | Returns |
|------|--------|---------|
| **Type Definition** | A forward type or composite key | Schema per composite key: `relationship_type`, `forward_type`, `instance_count`, `description`, `source_entity_type`, `target_entity_type`, `required_parameters`, `optional_parameters`, `property_definitions` |
| **List by Slug** | A relationship type + one or two slugs | Relationship instances involving the slug(s). One slug: where it is source or target. Two slugs: the specific relationship between that pair. |
| **List All** | A relationship type (optionally filtered by slug) | All instances of the given type. |

### Key Concepts

- **Forward types:** e.g. `REFERENCES`, `HAS_ROOT_FOLDER`, `HAS_SUBFOLDER`, `CONTAINS`. Only `REFERENCES` is agent-writable.
- **Composite key:** `SourceType|REL_TYPE|TargetType` — how relationship types are identified in the ontology.
- **Slug universality:** Slugs are globally unique across all entity types.

### Default Result Cap

Same as search entities: default cap of 10 with warning if more exist.

### Generalizable Contract

- Type Definition mode lets agents discover relationship schema at runtime.
- Slug-based lookup enables checking for existing relationships before creating duplicates.

---

## 3. Manage Entity

**Purpose:** Edit properties on existing entity instances. Stages the update in the worker's private store.

**Access:** Read (master + staged for current state) then Write (staging area only). Does NOT modify master ontology.

### Contract

**Inputs:** An entity type, a slug, and a JSON object of properties to set. Must specify edit mode.

**Allowed entity types:** File-based, agent-editable types only.
**Disallowed (generalizable pattern):** Structural/ingestion-only types (e.g., `DataSource`, `Folder`) cannot be edited.

### Behavior

1. Load current instance from virtual ontology. Error if slug not found.
2. Parse the properties object. Must be a non-empty dict.
3. **Validate property types** against entity type schema.
4. **Validate tags** if `tags` key is present in changes (against `tag_definitions`).
5. Deep-copy current instance, merge changes into `properties`.
6. Stage the update in the worker's private store.

### Validation

- Entity type must be in the editable set (not structural).
- Slug must exist in the virtual view.
- Property values must pass type validation against the schema.
- Tags must be from the entity type's `tag_definitions`.

### Generalizable Requirements

- **Edit-only for pre-populated instances.** Agents do not create entity instances; instances are pre-populated by ingestion. Agents enrich them with metadata.
- **Partial updates.** Only included keys are changed; omitted properties are preserved.
- **Schema-driven validation.** Property types and tag values are validated against the ontology schema, not hardcoded rules.
- **Structural types are read-only.** The system must distinguish between agent-editable and ingestion-only entity types.

---

## 4. Manage Relationship

**Purpose:** Create or edit relationship instances. Stages the update in the worker's private store.

**Access:** Read (master + staged for entity resolution and duplicate detection) then Write (staging area only). Does NOT modify master ontology.

### Contract

**Inputs:** A relationship type, source slug, target slug, and a mode (create or edit).

**Allowed relationship types:** `REFERENCES` only.
**Disallowed (structural):** `HAS_ROOT_FOLDER`, `HAS_SUBFOLDER`, `CONTAINS` — ingestion-only.

### Create Mode

1. Resolve source and target entity types by scanning all entity types for the given slugs. Error if either slug not found.
2. Construct composite key: `SourceType|REFERENCES|TargetType`.
3. Validate composite key exists in the relationship ontology schema.
4. Check for existing relationship with same source+target+type. **Reject duplicates** (must use edit mode instead).
5. Build instance with source/target metadata and optional properties (e.g., `context`).
6. Stage the update.

### Edit Mode

1. Same entity resolution and composite key construction.
2. Load existing relationship from virtual view. Error if not found.
3. Parse property changes.
4. Deep-copy existing, merge changes into `properties`.
5. Stage the update.

### Generalizable Requirements

- **Entity type auto-detection.** Source and target types are resolved from slugs, not specified by the agent. This simplifies the agent's job.
- **Composite key validation.** The relationship type (source_type, rel_type, target_type triple) must exist in the ontology schema. No new relationship types can be created at runtime.
- **Duplicate prevention.** Creating a relationship that already exists is an error.
- **Structural relationships are read-only.** Same pattern as entities: distinguish agent-writable from ingestion-only.

---

## 5. Validate and Commit

**Purpose:** Validate all staged edits, merge into master ontology, and signal job completion.

**Access:** Read+Write on master ontology under exclusive lock. Also reads the agent's staged edits and job metadata.

**Side effects:**
- Modifies the shared ontology store.
- Logs the commit result.
- Signals completion to the orchestrator on success.

### Validation Steps

1. **Load staged edits** for the current instance.
2. **Structural type guard:** Staged edits must not include structural entity types or structural relationship types.
3. **Schema validation:** Each entity instance must have a slug and properties. Tags validated against per-type `tag_definitions`.
4. **Relationship structure:** Each relationship must have valid composite key format, valid `rel_type`, and both source and target slugs.
5. **Slug uniqueness:** After merging staged edits with master, no slug may appear in multiple entity types.
6. **Cross-reference integrity (post-merge):** Every relationship's source and target slugs must exist in the merged entity ontology.
7. **Required properties (post-merge):** Every entity instance must satisfy `required_properties` from its type definition.
8. **Job completeness (workers only, not aggregator):** Every file in the agent's job must have its corresponding entity with `processed_by_agent=true` in the merged view.
9. **Scenario consistency (domain-specific):** `must_inspect`/`may_inspect` properties on Scenario entities must match corresponding relationship instances.

### Commit Process

1. Begin exclusive transaction on the shared ontology store.
2. Load current shared state.
3. Apply staged edits: for entities, upsert by slug; for relationships, upsert by (source_slug, target_slug) pair.
4. Run all validations on the merged result.
5. If valid: commit the transaction. Log the result.
6. On success: signal completion to the orchestrator.
7. On failure: rollback. Return validation errors via `is_error=True`. Agent sees errors and can fix+retry.

### Generalizable Requirements

- **Atomic commit.** All-or-nothing: staged edits are either fully applied or not at all.
- **Exclusive transaction.** Database-level isolation prevents concurrent commits from corrupting shared state.
- **Post-merge validation.** Validation runs on the merged state, not just the staged edits in isolation. This catches cross-instance conflicts.
- **Completion signal.** Successful commit triggers a completion callback to the orchestrator.
- **Retry-friendly.** Validation errors are descriptive; agents can fix and re-run.

---

## Read/Write Summary

| Tool | Reads | Writes | Concurrency |
|------|-------|--------|-------------|
| Search entities | Shared store + staged edits | (none) | `readOnlyHint=True` — parallelizable |
| Search relationships | Shared store + staged edits | (none) | `readOnlyHint=True` — parallelizable |
| Manage entity | Shared store + staged edits | Staging area | Serialized |
| Manage relationship | Shared store + staged edits | Staging area | Serialized |
| Validate and commit | Shared store + staged edits + job metadata | Shared store | Exclusive transaction |
