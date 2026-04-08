# Domain Model Specification

Extracted from the kartograph-extraction codebase. This spec captures the patterns and structure of the ontology system, not the domain-specific entity/relationship definitions or implementation details.

## 1. Ontology Structure

The system maintains an **ontology** consisting of entity type definitions, relationship type definitions, and their instances.

### 1.1 Dual-Type Architecture

The ontology has two top-level categories:

- **Entities** (nodes) — things that exist (files, components, people, etc.)
- **Relationships** (edges) — connections between entities

Both categories combine schema (type definitions) and data (instances). Type definitions describe what kinds of entities/relationships exist; instances are the actual extracted data.

### 1.2 Entity Type Definition Schema

Each entity type definition contains:

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | The entity type name (PascalCase). |
| `description` | string | yes | Human-readable description of what this entity type represents. |
| `tier` | string | yes | Classification tier (see Section 1.5). |
| `required_properties` | string[] | yes | Properties every instance of this type must have. |
| `optional_properties` | string[] | yes | Properties instances may optionally have (can be empty). |
| `property_definitions` | object | yes | Map of property name to description of that property's purpose and expected value. |
| `property_defaults` | object | no | Map of property name to default value for pre-populating instances. |
| `tag_definitions` | object | no | Map of allowed tag name to description. Acts as an enum constraint — tags on instances must be drawn from this set. |

**Naming convention:** Entity type names must be PascalCase.

### 1.3 Relationship Type Definition Schema

Relationship types are keyed by a **composite key**: `"SourceEntityType|RELATIONSHIP_NAME|TargetEntityType"` (e.g., `"TestSuite|CONTAINS|TestCase"`).

| Field | Type | Required | Description |
|---|---|---|---|
| `source_entity_type` | string | yes | Entity type for the source end. |
| `target_entity_type` | string | yes | Entity type for the target end. |
| `forward_relationship` | object | yes | Contains `type` (UPPER_SNAKE_CASE name) and `description`. |
| `reverse_relationship` | object | no | Contains `type` (the inverse relationship name). |
| `required_parameters` | string[] | yes | Properties required on every instance. |
| `optional_parameters` | string[] | yes | Properties instances may optionally have. |
| `property_definitions` | object | no | Map of property name to description. |

**Naming conventions:**
- Forward/reverse relationship type names: UPPER_SNAKE_CASE.
- Source/target entity types: PascalCase.

### 1.4 Cardinality

The original codebase included a `cardinality` field (e.g., `"one_to_many"`, `"many_to_many"`) on relationship type definitions. This was documentary only — not enforced by the validation system at runtime.

### 1.5 Entity Type Tiers

Entity types are classified into tiers that affect how they can be managed:

- **Structural tier** — Instances are pre-populated during ingestion and are not editable by extraction agents (example from reference implementation: `DataSource`, `Folder`).
- **File-based tier** — Instances are pre-populated with skeleton data during ingestion, then enriched by extraction agents (example from reference implementation: `ProductFile`, `SREFile`).
- **Scenario-based tier** — Instances created by a separate aggregator process after extraction (example from reference implementation: `Scenario` — later removed in V2).

### 1.6 Relationship Type Categories

- **Structural relationships** — Pre-populated during ingestion, not agent-editable.
- **Agent-managed relationships** — Created and edited by extraction agents.
- **Aggregator-managed relationships** — Created by post-processing (disabled in V2).


## 2. Entity Model

### 2.1 Entity Instance Structure

Every entity instance has:

| Field | Type | Required | Description |
|---|---|---|---|
| `slug` | string | yes | Globally unique identifier. Unique across ALL entity types, not just within one type. |
| `properties` | object | yes | Key-value map. Must include all `required_properties` from the entity type definition. |

### 2.2 Slugs and ID Generation

Slugs are the primary human-readable identifier for entity instances.

**Slug format:** `{type}:{canonical-name}`

Examples:
- `product:openshift-hyperfleet`
- `repo:my-repo`
- `test-case:test-auth-flow`

The `type` prefix is the entity type (lowercased). The `canonical-name` is a human-readable, URL-safe identifier for the specific instance.

**Slug constraints:**
- Lowercase throughout.
- The canonical name portion uses kebab-case (hyphens allowed, underscores allowed).
- No spaces. Only lowercase letters, numbers, hyphens, underscores, and the `:` separator.
- Must not be empty.
- **Globally unique** — a slug cannot appear in more than one entity type.

**ID derivation:** The JSONL `id` field is deterministically derived from the slug using kartograph's `EntityIdGenerator` (part of the Shared Kernel):

```
id = f"{type_lower}:{sha256(tenant_id:type_lower:slug)[:16]}"
```

This ensures the same entity always gets the same ID regardless of which system generates it. k-extract must use the same hash function as kartograph to produce compatible IDs.

Edge IDs are similarly derived: `f"{label_lower}:{sha256(tenant:start_id:label:end_id)[:16]}"`.

See [output-format.md](../process/output-format.md) for the full JSONL contract.

### 2.3 Property Types

Properties support:
- **Strings** — titles, descriptions, paths, summaries.
- **Booleans** — status flags.
- **Integers** — counts.
- **Arrays of strings** — tags, slug lists, URL lists.

### 2.4 Tags

Some entity types define `tag_definitions` that constrain which values may appear in the `tags` property:
- Tags are an array of strings.
- Each tag must exist in the entity type's `tag_definitions`.
- Multiple tags per entity.
- Different entity types define different tag sets.


## 3. Relationship Model

### 3.1 Relationship Instance Structure

| Field | Type | Required | Description |
|---|---|---|---|
| `source_entity_type` | string | yes | Entity type of the source. |
| `source_slug` | string | yes | Slug of the source entity. |
| `target_entity_type` | string | yes | Entity type of the target. |
| `target_slug` | string | yes | Slug of the target entity. |
| `properties` | object | yes | Key-value map; may be empty. Must include all `required_parameters` from the type definition. |

### 3.2 Instance Identity

A relationship instance is uniquely identified by:
- Its composite key (which relationship type)
- `source_slug` + `target_slug` pair

The `(source_slug, target_slug)` pair is unique within a composite key.

### 3.3 Referential Integrity

- Source/target entity types on the instance must match the relationship type definition.
- Source and target entities must exist.

### 3.4 Directionality

Relationships are directional (source → target). The schema supports both `forward_relationship` and `reverse_relationship` names for semantic traversal in both directions.


## 4. Validation Rules

### 4.1 Entity Validation

1. **Required properties** — Every instance must have all `required_properties`.
2. **Property type validation** — Values must match expected types.
3. **Tag validation** — Tags must be drawn from `tag_definitions`.
4. **Slug presence** — Non-empty slug required.
5. **Slug global uniqueness** — No slug may appear in more than one entity type.

### 4.2 Relationship Validation

1. **Composite key format** — Exactly 3 parts separated by `|`.
2. **Relationship type existence** — The composite key must correspond to an existing type definition. No new types can be created at runtime.
3. **Source/target slug presence** — Non-empty slugs required.
4. **Referential integrity** — Source and target entities must exist.
5. **Entity type consistency** — Instance types must match the definition.
6. **Required parameters** — Instance must include all `required_parameters`.

### 4.3 Naming Convention Validation

- Entity type names: PascalCase.
- Relationship type names: UPPER_SNAKE_CASE.
- Slugs: kebab-case (lowercase, hyphens, underscores).

### 4.4 Structural Protection

Structural entity types and relationship types are protected from agent modification. Validation rejects edits to protected types.

### 4.5 Job Completion Validation

Before an agent's work is committed, every file in the job's file list must have been processed (marked via a property like `processed_by_agent=True`). This prevents files from being silently skipped.


## 5. Ontology Operations

### 5.1 Stage-Then-Commit Pattern

Agents do not write directly to the shared ontology. Instead:

1. **Stage** — Each agent writes changes to its own isolated pending edits store.
2. **Validate** — Pending edits are validated against the current ontology state.
3. **Commit** — On success, edits are applied atomically under an exclusive lock.

This enables multiple concurrent agents to work independently without conflicting writes.

### 5.2 Virtual Ontology (Read Path)

When agents query the ontology, they see a merged view of:
- The current shared ontology
- Their own pending edits (not yet committed)

An agent can read back its own staged changes. Other agents cannot see uncommitted changes.

### 5.3 Entity Operations

- **Upsert (edit)** — Look up existing instance by slug, validate property types and tags, merge property changes (not replace), stage to pending edits.
- **Search** — Multiple modes: by type definition, by slug, by tag filter, by text search across properties, by file path. Results capped with configurable limits.

### 5.4 Relationship Operations

- **Create** — Resolve source/target by slug (auto-detect entity type), construct composite key, reject if relationship already exists, stage to pending edits.
- **Edit** — Requires relationship to exist, merge property changes.
- **Search** — By type definition, list all instances of a type, or filter by involved slug(s).

### 5.5 Validate and Commit

1. Acquire exclusive lock on the ontology.
2. Load current ontology state.
3. Load agent's pending edits.
4. Apply pending edits to produce merged state.
5. Validate: slug uniqueness, type constraints, tag validity, referential integrity, job completion.
6. On success: write merged state atomically. Signal job completion.
7. On failure: reject without modifying shared state.
