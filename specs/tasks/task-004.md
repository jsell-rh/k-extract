# Task 004: Output Format — JSONL Mutation Writer and ID Generation

**Status:** `needs-revision`
**Spec Reference:** specs/process/output-format.md
**Branch:** task-004
**PR:** #4
**Review:** specs/reviews/task-004.md

## Description

Implement the JSONL output writer that produces kartograph-compatible mutations. This includes deterministic ID generation (must match kartograph's algorithm), operation models, and a streaming JSONL writer.

Reference: specs/process/output-format.md.

### What to build

1. **ID generation** (compatible with kartograph's EntityIdGenerator):
   - Node ID: `f"{type_lower}:{sha256(tenant_id:type_lower:slug)[:16]}"`
   - Edge ID: `f"{label_lower}:{sha256(tenant:start_id:label:end_id)[:16]}"`
   - ID format regex: `^[0-9a-z_]+:[0-9a-f]{16}$`
   - Must produce identical IDs to kartograph for the same inputs

2. **Operation models:**
   - `DEFINE` — type declaration (node or edge): `op`, `type`, `label`, `description`, `required_properties`
   - `CREATE` — entity/relationship discovery: `op`, `type`, `id`, `label`, `set_properties` (must include `data_source_id`, `source_path`; nodes must include `slug`). Edges add `start_id`, `end_id`.
   - Models should validate required fields per operation type

3. **JSONL writer:**
   - Streaming: write one JSON line at a time (append mode)
   - Thread-safe / async-safe for concurrent workers
   - Partial output is always valid (interrupted run produces usable JSONL)

4. **DEFINE generation from ontology:**
   - Given the ontology config (entity types + relationship types), emit all DEFINE operations
   - DEFINEs must appear before any CREATE

### File layout

- `src/k_extract/domain/mutations.py` — Operation models and ID generation
- `src/k_extract/pipeline/writer.py` — JSONL streaming writer
- `tests/domain/test_mutations.py` — ID generation and operation validation tests
- `tests/pipeline/test_writer.py` — Writer tests

## Acceptance Criteria

- [ ] Node and edge ID generation matching kartograph's SHA256-based algorithm
- [ ] DEFINE and CREATE operation models with field validation
- [ ] System properties (`data_source_id`, `source_path`, `slug`) enforced on CREATE
- [ ] Streaming JSONL writer (append mode, one line per operation)
- [ ] DEFINE generation from ontology config
- [ ] Unit tests for ID generation, operation validation, and writer

## Relevant Commits

- `0f26200` — chore(task-004): begin implementation
- `7567e4e` — chore(task-004): add PR number
- `7bc8cb6` — feat(task-004): implement JSONL mutation writer and ID generation
