"""Tests for DEFINE generation from ontology config.

Covers:
- Entity type → node DEFINE
- Relationship type → edge DEFINE
- Ordering: nodes before edges
- All fields mapped correctly
"""

from __future__ import annotations

from k_extract.config.schema import (
    EntityTypeConfig,
    OntologyConfig,
    RelationshipTypeConfig,
)
from k_extract.domain.mutations import DefineType, OpType
from k_extract.pipeline.defines import generate_defines


class TestGenerateDefines:
    """Tests for generate_defines."""

    def test_entity_types_produce_node_defines(self) -> None:
        """Each entity type produces a node DEFINE."""
        ontology = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Person",
                    description="A person entity",
                    required_properties=["name"],
                    optional_properties=["email"],
                ),
            ],
            relationship_types=[],
        )
        defines = generate_defines(ontology)
        assert len(defines) == 1
        assert defines[0].op == OpType.DEFINE
        assert defines[0].type == DefineType.NODE
        assert defines[0].label == "Person"
        assert defines[0].description == "A person entity"
        assert defines[0].required_properties == ["name"]

    def test_relationship_types_produce_edge_defines(self) -> None:
        """Each relationship type produces an edge DEFINE."""
        ontology = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Person",
                    description="A person",
                    required_properties=[],
                    optional_properties=[],
                ),
            ],
            relationship_types=[
                RelationshipTypeConfig(
                    label="KNOWS",
                    description="A friendship",
                    source_entity_type="Person",
                    target_entity_type="Person",
                    required_properties=["since"],
                    optional_properties=[],
                ),
            ],
        )
        defines = generate_defines(ontology)
        assert len(defines) == 2
        edge_define = defines[1]
        assert edge_define.type == DefineType.EDGE
        assert edge_define.label == "KNOWS"
        assert edge_define.description == "A friendship"
        assert edge_define.required_properties == ["since"]

    def test_node_defines_before_edge_defines(self) -> None:
        """Node DEFINEs appear before edge DEFINEs in the output."""
        ontology = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Person",
                    description="A person",
                    required_properties=[],
                    optional_properties=[],
                ),
                EntityTypeConfig(
                    label="Company",
                    description="A company",
                    required_properties=["name"],
                    optional_properties=[],
                ),
            ],
            relationship_types=[
                RelationshipTypeConfig(
                    label="WORKS_AT",
                    description="Employment",
                    source_entity_type="Person",
                    target_entity_type="Company",
                    required_properties=[],
                    optional_properties=[],
                ),
            ],
        )
        defines = generate_defines(ontology)
        assert len(defines) == 3
        assert defines[0].type == DefineType.NODE
        assert defines[0].label == "Person"
        assert defines[1].type == DefineType.NODE
        assert defines[1].label == "Company"
        assert defines[2].type == DefineType.EDGE
        assert defines[2].label == "WORKS_AT"

    def test_empty_ontology_produces_no_defines(self) -> None:
        """An ontology with no types produces no DEFINEs."""
        ontology = OntologyConfig(entity_types=[], relationship_types=[])
        defines = generate_defines(ontology)
        assert defines == []

    def test_empty_required_properties_preserved(self) -> None:
        """Entity types with no required properties produce DEFINEs with empty list."""
        ontology = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Tag",
                    description="A tag",
                    required_properties=[],
                    optional_properties=[],
                ),
            ],
            relationship_types=[],
        )
        defines = generate_defines(ontology)
        assert defines[0].required_properties == []
