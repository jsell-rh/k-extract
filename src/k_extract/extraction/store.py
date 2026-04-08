"""Ontology store: SQLite-backed shared state with per-worker staging.

Implements the stage-then-commit pattern for concurrent agent access to the
shared knowledge graph. Multiple workers can read simultaneously; commits
are serialized via SQLite exclusive transactions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from k_extract.domain.entities import EntityInstance
from k_extract.domain.ontology import (
    Ontology,
    _is_valid_property_value,
    _pascal_to_kebab,
)
from k_extract.domain.relationships import RelationshipInstance
from k_extract.extraction.models import (
    EntityInstanceRow,
    OntologyBase,
    RelationshipInstanceRow,
    StagedEntityRow,
    StagedRelationshipRow,
)

DEFAULT_RESULT_LIMIT = 10


def _row_to_entity(row: EntityInstanceRow | StagedEntityRow) -> EntityInstance:
    """Convert a database row to a domain EntityInstance."""
    return EntityInstance(slug=row.slug, properties=row.properties)


def _row_to_relationship(
    row: RelationshipInstanceRow | StagedRelationshipRow,
) -> RelationshipInstance:
    """Convert a database row to a domain RelationshipInstance."""
    return RelationshipInstance(
        source_entity_type=row.source_entity_type,
        source_slug=row.source_slug,
        target_entity_type=row.target_entity_type,
        target_slug=row.target_slug,
        relationship_type=row.relationship_type,
        properties=row.properties,
    )


def _build_searchable_text(entity: EntityInstance) -> str:
    """Build lowercase searchable text from entity slug and string properties."""
    parts = [entity.slug.lower()]
    for value in entity.properties.values():
        if isinstance(value, str):
            parts.append(value.lower())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    parts.append(item.lower())
    return " ".join(parts)


class OntologyStore:
    """SQLite-backed ontology store with staging and virtual merge.

    Provides:
    - Shared entity/relationship storage with upsert (property merge)
    - Per-worker staging areas for isolated edits
    - Virtual ontology view merging shared + staged for reads
    - Atomic validate-and-commit with exclusive transaction
    """

    def __init__(self, engine: Engine, ontology: Ontology) -> None:
        self._engine = engine
        self._ontology = ontology
        OntologyBase.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine)

    # ------------------------------------------------------------------ #
    # Shared store: Entity operations
    # ------------------------------------------------------------------ #

    def upsert_entity(self, entity: EntityInstance) -> None:
        """Upsert entity to shared store, merging properties."""
        with self._session_factory() as session:
            existing = session.get(EntityInstanceRow, entity.slug)
            if existing:
                merged = {**existing.properties, **entity.properties}
                existing.properties = merged
                existing.entity_type = entity.entity_type
            else:
                session.add(
                    EntityInstanceRow(
                        slug=entity.slug,
                        entity_type=entity.entity_type,
                        properties=dict(entity.properties),
                    )
                )
            session.commit()

    def get_entity_by_slug(
        self, slug: str, *, worker_id: str | None = None
    ) -> EntityInstance | None:
        """Get entity by slug, optionally from virtual view."""
        with self._session_factory() as session:
            if worker_id is not None:
                staged = session.get(StagedEntityRow, (worker_id, slug))
                if staged is not None:
                    shared = session.get(EntityInstanceRow, slug)
                    if shared is not None:
                        merged_props = {
                            **shared.properties,
                            **staged.properties,
                        }
                        return EntityInstance(slug=slug, properties=merged_props)
                    return _row_to_entity(staged)

            shared = session.get(EntityInstanceRow, slug)
            if shared is not None:
                return _row_to_entity(shared)
            return None

    def search_entities_by_type(
        self,
        entity_type: str,
        *,
        worker_id: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
    ) -> tuple[list[EntityInstance], int]:
        """Search entities by PascalCase type name.

        Returns (capped_results, total_count).
        """
        type_prefix = _pascal_to_kebab(entity_type)
        with self._session_factory() as session:
            entities = self._merged_entities_by_type(session, type_prefix, worker_id)
            total = len(entities)
            return entities[:limit], total

    def search_entities_by_slugs(
        self, slugs: list[str], *, worker_id: str | None = None
    ) -> list[EntityInstance]:
        """Get entities by multiple slugs."""
        results = []
        for slug in slugs:
            entity = self.get_entity_by_slug(slug, worker_id=worker_id)
            if entity is not None:
                results.append(entity)
        return results

    def search_entities_by_tag(
        self,
        entity_type: str,
        tags: list[str],
        *,
        worker_id: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
    ) -> tuple[list[EntityInstance], int]:
        """Search entities with any of the specified tags (OR logic)."""
        type_prefix = _pascal_to_kebab(entity_type)
        tag_set = set(tags)
        with self._session_factory() as session:
            entities = self._merged_entities_by_type(session, type_prefix, worker_id)
            matches = []
            for entity in entities:
                entity_tags = entity.properties.get("tags")
                if isinstance(entity_tags, list) and tag_set & set(entity_tags):
                    matches.append(entity)
            total = len(matches)
            return matches[:limit], total

    def search_entities_by_text(
        self,
        entity_type: str,
        terms: list[str],
        *,
        worker_id: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
    ) -> tuple[list[EntityInstance], int]:
        """Search entities by text (AND logic, case-insensitive).

        Searches across all string/list properties and slug.
        """
        type_prefix = _pascal_to_kebab(entity_type)
        lower_terms = [t.lower() for t in terms]
        with self._session_factory() as session:
            entities = self._merged_entities_by_type(session, type_prefix, worker_id)
            matches = []
            for entity in entities:
                searchable = _build_searchable_text(entity)
                if all(term in searchable for term in lower_terms):
                    matches.append(entity)
            total = len(matches)
            return matches[:limit], total

    def search_entities_by_file_path(
        self, file_path: str, *, worker_id: str | None = None
    ) -> list[EntityInstance]:
        """Search entities by file_path property across all types."""
        with self._session_factory() as session:
            entities = self._all_merged_entities(session, worker_id)
            return [
                e
                for e in entities.values()
                if e.properties.get("file_path") == file_path
            ]

    # ------------------------------------------------------------------ #
    # Shared store: Relationship operations
    # ------------------------------------------------------------------ #

    def upsert_relationship(self, relationship: RelationshipInstance) -> None:
        """Upsert relationship to shared store."""
        with self._session_factory() as session:
            existing = session.get(
                RelationshipInstanceRow,
                (
                    relationship.composite_key,
                    relationship.source_slug,
                    relationship.target_slug,
                ),
            )
            if existing:
                merged = {**existing.properties, **relationship.properties}
                existing.properties = merged
                existing.source_entity_type = relationship.source_entity_type
                existing.target_entity_type = relationship.target_entity_type
                existing.relationship_type = relationship.relationship_type
            else:
                session.add(
                    RelationshipInstanceRow(
                        composite_key=relationship.composite_key,
                        source_entity_type=relationship.source_entity_type,
                        source_slug=relationship.source_slug,
                        target_entity_type=relationship.target_entity_type,
                        target_slug=relationship.target_slug,
                        relationship_type=relationship.relationship_type,
                        properties=dict(relationship.properties),
                    )
                )
            session.commit()

    def search_relationships_by_type(
        self,
        composite_key: str,
        *,
        worker_id: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
    ) -> tuple[list[RelationshipInstance], int]:
        """Search relationships by composite key."""
        with self._session_factory() as session:
            rels = self._merged_relationships_by_key(session, composite_key, worker_id)
            total = len(rels)
            return rels[:limit], total

    def search_relationships_by_slug(
        self,
        composite_key: str,
        slug: str,
        *,
        worker_id: str | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
    ) -> tuple[list[RelationshipInstance], int]:
        """Search relationships involving a slug (source or target)."""
        with self._session_factory() as session:
            rels = self._merged_relationships_by_key(session, composite_key, worker_id)
            matches = [
                r for r in rels if r.source_slug == slug or r.target_slug == slug
            ]
            total = len(matches)
            return matches[:limit], total

    # ------------------------------------------------------------------ #
    # Staging operations
    # ------------------------------------------------------------------ #

    def stage_entity(self, worker_id: str, entity: EntityInstance) -> None:
        """Stage entity edit for a worker, merging properties if already staged."""
        with self._session_factory() as session:
            existing = session.get(StagedEntityRow, (worker_id, entity.slug))
            if existing:
                merged = {**existing.properties, **entity.properties}
                existing.properties = merged
                existing.entity_type = entity.entity_type
            else:
                session.add(
                    StagedEntityRow(
                        worker_id=worker_id,
                        slug=entity.slug,
                        entity_type=entity.entity_type,
                        properties=dict(entity.properties),
                    )
                )
            session.commit()

    def stage_relationship(
        self, worker_id: str, relationship: RelationshipInstance
    ) -> None:
        """Stage relationship edit for a worker."""
        with self._session_factory() as session:
            existing = session.get(
                StagedRelationshipRow,
                (
                    worker_id,
                    relationship.composite_key,
                    relationship.source_slug,
                    relationship.target_slug,
                ),
            )
            if existing:
                merged = {**existing.properties, **relationship.properties}
                existing.properties = merged
                existing.source_entity_type = relationship.source_entity_type
                existing.target_entity_type = relationship.target_entity_type
                existing.relationship_type = relationship.relationship_type
            else:
                session.add(
                    StagedRelationshipRow(
                        worker_id=worker_id,
                        composite_key=relationship.composite_key,
                        source_entity_type=relationship.source_entity_type,
                        source_slug=relationship.source_slug,
                        target_entity_type=relationship.target_entity_type,
                        target_slug=relationship.target_slug,
                        relationship_type=relationship.relationship_type,
                        properties=dict(relationship.properties),
                    )
                )
            session.commit()

    def clear_staging(self, worker_id: str) -> None:
        """Clear all staged edits for a worker."""
        with self._session_factory() as session:
            session.execute(
                delete(StagedEntityRow).where(StagedEntityRow.worker_id == worker_id)
            )
            session.execute(
                delete(StagedRelationshipRow).where(
                    StagedRelationshipRow.worker_id == worker_id
                )
            )
            session.commit()

    # ------------------------------------------------------------------ #
    # Validate and commit
    # ------------------------------------------------------------------ #

    def validate_and_commit(
        self, worker_id: str, job_files: list[str] | None = None
    ) -> list[str]:
        """Validate staged edits and commit to shared store atomically.

        Acquires an exclusive transaction (BEGIN IMMEDIATE) to prevent
        concurrent modifications during the read-validate-write cycle.

        Returns list of validation errors (empty on success).
        """
        with self._engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            try:
                # Load shared entities (explicit columns — Connection
                # does not produce ORM instances)
                shared_entities: dict[str, EntityInstance] = {}
                for row in conn.execute(
                    select(
                        EntityInstanceRow.slug,
                        EntityInstanceRow.entity_type,
                        EntityInstanceRow.properties,
                    )
                ):
                    shared_entities[row.slug] = EntityInstance(
                        slug=row.slug, properties=row.properties
                    )

                # Load shared relationships
                shared_rels_list: list[RelationshipInstance] = []
                for row in conn.execute(
                    select(
                        RelationshipInstanceRow.composite_key,
                        RelationshipInstanceRow.source_entity_type,
                        RelationshipInstanceRow.source_slug,
                        RelationshipInstanceRow.target_entity_type,
                        RelationshipInstanceRow.target_slug,
                        RelationshipInstanceRow.relationship_type,
                        RelationshipInstanceRow.properties,
                    )
                ):
                    shared_rels_list.append(
                        RelationshipInstance(
                            source_entity_type=row.source_entity_type,
                            source_slug=row.source_slug,
                            target_entity_type=row.target_entity_type,
                            target_slug=row.target_slug,
                            relationship_type=row.relationship_type,
                            properties=row.properties,
                        )
                    )

                # Load staged entities
                staged_entities_raw: list[tuple[str, str, dict[str, Any]]] = []
                for row in conn.execute(
                    select(
                        StagedEntityRow.slug,
                        StagedEntityRow.entity_type,
                        StagedEntityRow.properties,
                    ).where(StagedEntityRow.worker_id == worker_id)
                ):
                    staged_entities_raw.append(
                        (row.slug, row.entity_type, row.properties)
                    )

                # Load staged relationships
                staged_rels_raw: list[RelationshipInstance] = []
                for row in conn.execute(
                    select(
                        StagedRelationshipRow.composite_key,
                        StagedRelationshipRow.source_entity_type,
                        StagedRelationshipRow.source_slug,
                        StagedRelationshipRow.target_entity_type,
                        StagedRelationshipRow.target_slug,
                        StagedRelationshipRow.relationship_type,
                        StagedRelationshipRow.properties,
                    ).where(StagedRelationshipRow.worker_id == worker_id)
                ):
                    staged_rels_raw.append(
                        RelationshipInstance(
                            source_entity_type=row.source_entity_type,
                            source_slug=row.source_slug,
                            target_entity_type=row.target_entity_type,
                            target_slug=row.target_slug,
                            relationship_type=row.relationship_type,
                            properties=row.properties,
                        )
                    )

                # Build merged entity view
                merged_entities = dict(shared_entities)
                staged_slugs: set[str] = set()
                for slug, _etype, props in staged_entities_raw:
                    staged_slugs.add(slug)
                    if slug in merged_entities:
                        merged_props = {
                            **merged_entities[slug].properties,
                            **props,
                        }
                        merged_entities[slug] = EntityInstance(
                            slug=slug, properties=merged_props
                        )
                    else:
                        merged_entities[slug] = EntityInstance(
                            slug=slug, properties=props
                        )

                # Build merged relationship view
                rel_map: dict[tuple[str, str, str], RelationshipInstance] = {}
                for rel in shared_rels_list:
                    key = (rel.composite_key, rel.source_slug, rel.target_slug)
                    rel_map[key] = rel
                staged_rel_keys: set[tuple[str, str, str]] = set()
                for rel in staged_rels_raw:
                    key = (rel.composite_key, rel.source_slug, rel.target_slug)
                    staged_rel_keys.add(key)
                    if key in rel_map:
                        merged_props = {
                            **rel_map[key].properties,
                            **rel.properties,
                        }
                        rel_map[key] = RelationshipInstance(
                            source_entity_type=rel.source_entity_type,
                            source_slug=rel.source_slug,
                            target_entity_type=rel.target_entity_type,
                            target_slug=rel.target_slug,
                            relationship_type=rel.relationship_type,
                            properties=merged_props,
                        )
                    else:
                        rel_map[key] = rel
                merged_relationships = list(rel_map.values())

                # Validate
                errors = self._validate_merged(
                    merged_entities,
                    merged_relationships,
                    staged_slugs,
                    job_files,
                )
                if errors:
                    conn.execute(text("ROLLBACK"))
                    return errors

                # Write merged entities for staged slugs
                for slug in staged_slugs:
                    entity = merged_entities[slug]
                    stmt = sqlite_insert(EntityInstanceRow).values(
                        slug=entity.slug,
                        entity_type=entity.entity_type,
                        properties=dict(entity.properties),
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["slug"],
                        set_={
                            "entity_type": stmt.excluded.entity_type,
                            "properties": stmt.excluded.properties,
                        },
                    )
                    conn.execute(stmt)

                # Write merged relationships for staged keys
                for key in staged_rel_keys:
                    rel = rel_map[key]
                    stmt = sqlite_insert(RelationshipInstanceRow).values(
                        composite_key=rel.composite_key,
                        source_slug=rel.source_slug,
                        target_slug=rel.target_slug,
                        source_entity_type=rel.source_entity_type,
                        target_entity_type=rel.target_entity_type,
                        relationship_type=rel.relationship_type,
                        properties=dict(rel.properties),
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[
                            "composite_key",
                            "source_slug",
                            "target_slug",
                        ],
                        set_={
                            "source_entity_type": stmt.excluded.source_entity_type,
                            "target_entity_type": stmt.excluded.target_entity_type,
                            "relationship_type": stmt.excluded.relationship_type,
                            "properties": stmt.excluded.properties,
                        },
                    )
                    conn.execute(stmt)

                # Clear staging
                conn.execute(
                    delete(StagedEntityRow).where(
                        StagedEntityRow.worker_id == worker_id
                    )
                )
                conn.execute(
                    delete(StagedRelationshipRow).where(
                        StagedRelationshipRow.worker_id == worker_id
                    )
                )

                conn.execute(text("COMMIT"))
                return []
            except Exception:
                conn.execute(text("ROLLBACK"))
                raise

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _merged_entities_by_type(
        self,
        session: Any,
        type_prefix: str,
        worker_id: str | None,
    ) -> list[EntityInstance]:
        """Load shared + staged entities for a type, merged."""
        shared_rows = (
            session.execute(
                select(EntityInstanceRow).where(
                    EntityInstanceRow.entity_type == type_prefix
                )
            )
            .scalars()
            .all()
        )
        entities: dict[str, EntityInstance] = {
            row.slug: _row_to_entity(row) for row in shared_rows
        }

        if worker_id is not None:
            staged_rows = (
                session.execute(
                    select(StagedEntityRow).where(
                        StagedEntityRow.worker_id == worker_id,
                        StagedEntityRow.entity_type == type_prefix,
                    )
                )
                .scalars()
                .all()
            )
            for row in staged_rows:
                staged = _row_to_entity(row)
                if staged.slug in entities:
                    merged_props = {
                        **entities[staged.slug].properties,
                        **staged.properties,
                    }
                    entities[staged.slug] = EntityInstance(
                        slug=staged.slug, properties=merged_props
                    )
                else:
                    entities[staged.slug] = staged

        return list(entities.values())

    def _all_merged_entities(
        self, session: Any, worker_id: str | None
    ) -> dict[str, EntityInstance]:
        """Load all shared + staged entities, merged."""
        entities: dict[str, EntityInstance] = {}
        shared_rows = session.execute(select(EntityInstanceRow)).scalars().all()
        for row in shared_rows:
            entities[row.slug] = _row_to_entity(row)

        if worker_id is not None:
            staged_rows = (
                session.execute(
                    select(StagedEntityRow).where(
                        StagedEntityRow.worker_id == worker_id
                    )
                )
                .scalars()
                .all()
            )
            for row in staged_rows:
                staged = _row_to_entity(row)
                if staged.slug in entities:
                    merged_props = {
                        **entities[staged.slug].properties,
                        **staged.properties,
                    }
                    entities[staged.slug] = EntityInstance(
                        slug=staged.slug, properties=merged_props
                    )
                else:
                    entities[staged.slug] = staged

        return entities

    def _merged_relationships_by_key(
        self,
        session: Any,
        composite_key: str,
        worker_id: str | None,
    ) -> list[RelationshipInstance]:
        """Load shared + staged relationships for a composite key, merged."""
        shared_rows = (
            session.execute(
                select(RelationshipInstanceRow).where(
                    RelationshipInstanceRow.composite_key == composite_key
                )
            )
            .scalars()
            .all()
        )
        rels: dict[tuple[str, str], RelationshipInstance] = {}
        for row in shared_rows:
            key = (row.source_slug, row.target_slug)
            rels[key] = _row_to_relationship(row)

        if worker_id is not None:
            staged_rows = (
                session.execute(
                    select(StagedRelationshipRow).where(
                        StagedRelationshipRow.worker_id == worker_id,
                        StagedRelationshipRow.composite_key == composite_key,
                    )
                )
                .scalars()
                .all()
            )
            for row in staged_rows:
                key = (row.source_slug, row.target_slug)
                staged = _row_to_relationship(row)
                if key in rels:
                    merged_props = {
                        **rels[key].properties,
                        **staged.properties,
                    }
                    rels[key] = RelationshipInstance(
                        source_entity_type=staged.source_entity_type,
                        source_slug=staged.source_slug,
                        target_entity_type=staged.target_entity_type,
                        target_slug=staged.target_slug,
                        relationship_type=staged.relationship_type,
                        properties=merged_props,
                    )
                else:
                    rels[key] = staged

        return list(rels.values())

    def _validate_merged(
        self,
        merged_entities: dict[str, EntityInstance],
        merged_relationships: list[RelationshipInstance],
        staged_slugs: set[str],
        job_files: list[str] | None,
    ) -> list[str]:
        """Validate the merged ontology state.

        Checks: structural protection, required properties, property types,
        tag validity, referential integrity, entity type consistency,
        required parameters, and job completeness.
        """
        errors: list[str] = []

        # Structural protection on staged entities
        for slug in staged_slugs:
            type_def = self._ontology.find_entity_type_for_slug(slug)
            if type_def and type_def.is_structural:
                errors.append(
                    f"Cannot modify entity of structural type "
                    f"{type_def.type!r}: protected from agent edits"
                )

        # Entity validation
        for entity in merged_entities.values():
            type_def = self._ontology.find_entity_type_for_slug(entity.slug)
            if type_def is None:
                errors.append(
                    f"Unknown entity type for slug prefix: {entity.entity_type!r}"
                )
                continue

            # Required properties
            for prop in type_def.required_properties:
                if prop not in entity.properties:
                    errors.append(
                        f"Missing required property {prop!r} on entity {entity.slug!r}"
                    )

            # Property type validation
            for prop_name, prop_value in entity.properties.items():
                if not _is_valid_property_value(prop_value):
                    errors.append(
                        f"Invalid property type for {prop_name!r} on entity "
                        f"{entity.slug!r}: must be str, bool, int, or "
                        f"list[str]"
                    )

            # Tag validation
            if "tags" in entity.properties and type_def.tag_definitions:
                tags = entity.properties["tags"]
                if isinstance(tags, list):
                    allowed = set(type_def.tag_definitions.keys())
                    for tag in tags:
                        if isinstance(tag, str) and tag not in allowed:
                            errors.append(
                                f"Invalid tag {tag!r} on entity "
                                f"{entity.slug!r}. "
                                f"Allowed tags: {sorted(allowed)}"
                            )
                else:
                    errors.append(
                        f"Property 'tags' on entity {entity.slug!r} must be "
                        f"an array of strings, got {type(tags).__name__}"
                    )

        # Relationship validation
        for rel in merged_relationships:
            rel_type_def = self._ontology.get_relationship_type(rel.composite_key)
            if rel_type_def is None:
                errors.append(f"Unknown relationship type: {rel.composite_key!r}")
                continue

            # Referential integrity + type consistency
            source = merged_entities.get(rel.source_slug)
            if source is None:
                errors.append(f"Source entity not found: {rel.source_slug!r}")
            else:
                expected = _pascal_to_kebab(rel.source_entity_type)
                if source.entity_type != expected:
                    errors.append(
                        f"Source entity {rel.source_slug!r} is of type "
                        f"{source.entity_type!r}, expected {expected!r}"
                    )

            target = merged_entities.get(rel.target_slug)
            if target is None:
                errors.append(f"Target entity not found: {rel.target_slug!r}")
            else:
                expected = _pascal_to_kebab(rel.target_entity_type)
                if target.entity_type != expected:
                    errors.append(
                        f"Target entity {rel.target_slug!r} is of type "
                        f"{target.entity_type!r}, expected {expected!r}"
                    )

            # Required parameters
            for param in rel_type_def.required_parameters:
                if param not in rel.properties:
                    errors.append(
                        f"Missing required parameter {param!r} on "
                        f"relationship {rel.composite_key!r}"
                    )

        # Job completeness
        if job_files is not None:
            for fp in job_files:
                found = any(
                    entity.properties.get("file_path") == fp
                    and entity.properties.get("processed_by_agent") is True
                    for entity in merged_entities.values()
                )
                if not found:
                    errors.append(f"File not processed: {fp!r}")

        return errors
