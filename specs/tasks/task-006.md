# Task 006: Ontology Store — SQLite-backed Shared State with Staging

**Status:** `in-progress`
**Spec Reference:** specs/domain/domain-model.md (Section 5), specs/concurrency/concurrency-model.md
**Branch:** task-006
**PR:** #6
**Review:** (none)

## Description

Implement the SQLite-backed ontology store that holds the shared knowledge graph state and per-worker staging areas. This is the core data layer that agent tools interact with.

Reference: specs/domain/domain-model.md Section 5 (Ontology Operations), specs/concurrency/concurrency-model.md sections 1, 4–5.

### What to build

1. **SQLAlchemy models for ontology storage:**
   - `EntityInstance` table: `slug` (PK), `entity_type`, `properties` (JSON)
   - `RelationshipInstance` table: composite key + `source_slug` + `target_slug` (unique together), `properties` (JSON)
   - `StagedEntity` table: same fields as EntityInstance + `worker_id`
   - `StagedRelationship` table: same fields as RelationshipInstance + `worker_id`

2. **Shared ontology store operations:**
   - Entity CRUD: upsert by slug (merge properties, not replace), search by type/slug/tag/text/file_path
   - Relationship CRUD: upsert by (source_slug, target_slug) within composite key, search by type/slug
   - Result capping (default 10 with warning if more exist)

3. **Staging area (per-worker isolation):**
   - Stage entity edits: write to StagedEntity with worker_id
   - Stage relationship edits: write to StagedRelationship with worker_id
   - Clear staging area for a worker

4. **Virtual ontology (read path):**
   - Merge shared ontology + worker's staged edits for queries
   - Worker sees its own uncommitted changes; other workers don't
   - Upsert semantics: staged overrides shared for matching slugs

5. **Validate and commit (atomic write path):**
   - Acquire exclusive transaction on the shared store
   - Load shared state + worker's staged edits
   - Build merged view
   - Validate: slug uniqueness, required properties, tag validity, referential integrity, structural protection, job completeness
   - On success: write merged state, clear staging area
   - On failure: rollback, return errors

6. **Concurrency:**
   - Multiple workers can read simultaneously
   - Exclusive access for commits (SQLite transaction)
   - Entity + relationship stores treated as single critical section

### File layout

- `src/k_extract/extraction/store.py` — Ontology store (shared + staging + virtual merge)
- `src/k_extract/extraction/models.py` — SQLAlchemy models for entity/relationship storage
- `tests/extraction/test_store.py` — Store tests including concurrent access scenarios

## Acceptance Criteria

- [ ] SQLAlchemy models for shared entities, shared relationships, staged entities, staged relationships
- [ ] Entity upsert (merge properties) and search (by type, slug, tag, text, file_path)
- [ ] Relationship upsert and search (by type, slug)
- [ ] Per-worker staging area with isolation
- [ ] Virtual ontology merging shared + staged for reads
- [ ] Atomic validate-and-commit with full validation suite
- [ ] Exclusive transaction for commits, shared reads for queries
- [ ] Unit tests for CRUD, merge, validation, and concurrent commit scenarios

## Relevant Commits

(none yet)
