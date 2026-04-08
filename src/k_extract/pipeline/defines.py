"""DEFINE operation generation from ontology config.

Given the ontology config (entity types + relationship types),
produces all DEFINE operations that must appear before any CREATE.
"""

from __future__ import annotations

from k_extract.config.schema import OntologyConfig
from k_extract.domain.mutations import DefineOperation, DefineType, OpType


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
