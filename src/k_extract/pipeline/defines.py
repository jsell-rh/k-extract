"""DEFINE and CREATE operation generation from ontology config.

Given the ontology config (entity types + relationship types),
produces all DEFINE operations that must appear before any CREATE.
Also provides CREATE generation for committed entities/relationships.
"""

from __future__ import annotations

from k_extract.config.schema import OntologyConfig
from k_extract.domain.entities import EntityInstance
from k_extract.domain.mutations import (
    CreateOperation,
    DefineOperation,
    DefineType,
    OpType,
    generate_edge_id,
    generate_node_id,
)
from k_extract.domain.ontology import Ontology
from k_extract.domain.relationships import RelationshipInstance

TENANT_ID = "default"


def generate_defines(ontology: OntologyConfig) -> list[DefineOperation]:
    """Generate all DEFINE operations from an ontology config.

    Produces node DEFINEs for each entity type, then edge DEFINEs
    for each relationship type. DEFINEs must appear before any CREATE
    in the JSONL output.

    Args:
        ontology: The ontology config containing entity and relationship types.

    Returns:
        A list of DefineOperation instances.
    """
    defines: list[DefineOperation] = []

    for entity_type in ontology.entity_types:
        defines.append(
            DefineOperation(
                op=OpType.DEFINE,
                type=DefineType.NODE,
                label=entity_type.label,
                description=entity_type.description,
                required_properties=list(entity_type.required_properties),
            )
        )

    for rel_type in ontology.relationship_types:
        defines.append(
            DefineOperation(
                op=OpType.DEFINE,
                type=DefineType.EDGE,
                label=rel_type.label,
                description=rel_type.description,
                required_properties=list(rel_type.required_properties),
            )
        )

    return defines


def generate_creates(
    entities: list[EntityInstance],
    relationships: list[RelationshipInstance],
    data_source: str,
    ontology: Ontology,
) -> list[CreateOperation]:
    """Generate CREATE operations from committed entities and relationships.

    Produces node CREATEs for entities and edge CREATEs for relationships,
    with deterministic IDs and system properties.
    """
    creates: list[CreateOperation] = []

    for entity in entities:
        type_def = ontology.find_entity_type_for_slug(entity.slug)
        entity_type_name = type_def.type if type_def else entity.entity_type
        type_lower = entity_type_name.lower()

        set_properties = dict(entity.properties)
        set_properties["slug"] = entity.slug
        if "data_source_id" not in set_properties:
            set_properties["data_source_id"] = data_source
        if "source_path" not in set_properties:
            set_properties["source_path"] = entity.properties.get("file_path", "")

        creates.append(
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.NODE,
                id=generate_node_id(TENANT_ID, type_lower, entity.slug),
                label=entity_type_name,
                set_properties=set_properties,
            )
        )

    for rel in relationships:
        source_type_lower = rel.source_entity_type.lower()
        target_type_lower = rel.target_entity_type.lower()
        start_id = generate_node_id(TENANT_ID, source_type_lower, rel.source_slug)
        end_id = generate_node_id(TENANT_ID, target_type_lower, rel.target_slug)

        set_properties = dict(rel.properties)
        if "data_source_id" not in set_properties:
            set_properties["data_source_id"] = data_source
        if "source_path" not in set_properties:
            set_properties["source_path"] = rel.properties.get("file_path", "")

        creates.append(
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.EDGE,
                id=generate_edge_id(
                    TENANT_ID,
                    start_id,
                    rel.relationship_type,
                    end_id,
                ),
                label=rel.relationship_type,
                start_id=start_id,
                end_id=end_id,
                set_properties=set_properties,
            )
        )

    return creates
