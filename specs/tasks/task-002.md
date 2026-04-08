# Task 002: Domain Model — Ontology Type Definitions and Instances

**Status:** `in-progress`
**Spec Reference:** specs/domain/domain-model.md
**Branch:** task-002
**PR:** (none)
**Review:** (none)

## Description

Implement the core domain model as Pydantic models in `src/k_extract/domain/`. This covers ontology type definitions (schema), entity/relationship instances (data), and all validation rules. No database or persistence — pure domain logic.

Reference: specs/domain/domain-model.md sections 1–5.

### What to build

1. **Entity type definition model** (Section 1.2):
   - Fields: `type` (PascalCase), `description`, `tier`, `required_properties`, `optional_properties`, `property_definitions`, `property_defaults`, `tag_definitions`
   - Tier enum: structural, file-based, scenario-based

2. **Relationship type definition model** (Section 1.3):
   - Fields: `source_entity_type`, `target_entity_type`, `forward_relationship`, `reverse_relationship`, `required_parameters`, `optional_parameters`, `property_definitions`
   - Composite key construction and parsing: `"SourceType|REL_NAME|TargetType"`

3. **Entity instance model** (Section 2):
   - Fields: `slug`, `properties`
   - Slug validation: lowercase, kebab-case, `{type}:{canonical-name}` format
   - Property types: strings, booleans, integers, arrays of strings

4. **Relationship instance model** (Section 3):
   - Fields: `source_entity_type`, `source_slug`, `target_entity_type`, `target_slug`, `properties`
   - Identity: composite key + (source_slug, target_slug)

5. **Validation rules** (Section 4):
   - Entity: required properties, property types, tag validation, slug presence/uniqueness
   - Relationship: composite key format, type existence, referential integrity, required parameters
   - Naming conventions: PascalCase (entity types), UPPER_SNAKE_CASE (relationship types), kebab-case (slugs)
   - Structural type protection

6. **Ontology container model** (Section 1.1):
   - Holds entity type definitions, relationship type definitions, entity instances, relationship instances
   - Lookup by type, by slug, by composite key

### File layout

- `src/k_extract/domain/ontology.py` — Type definitions and ontology container
- `src/k_extract/domain/entities.py` — Entity instance model and validation
- `src/k_extract/domain/relationships.py` — Relationship instance model and validation
- `tests/domain/` — Unit tests for all models and validation rules

## Acceptance Criteria

- [ ] Entity type definition model with all fields and PascalCase validation
- [ ] Relationship type definition model with composite key construction/parsing
- [ ] Entity instance model with slug validation (format, presence)
- [ ] Relationship instance model with identity by composite key + slug pair
- [ ] Validation: required properties, property types, tag membership, naming conventions
- [ ] Structural type protection (tier-based editability check)
- [ ] Ontology container with lookup methods
- [ ] Comprehensive unit tests for all validation rules and edge cases

## Relevant Commits

(none yet)
