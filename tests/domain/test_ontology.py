"""Tests for ontology type definitions, container, and validation."""

import pytest
from pydantic import ValidationError

from k_extract.domain.entities import EntityInstance
from k_extract.domain.ontology import (
    EntityTypeDefinition,
    Ontology,
    RelationshipCategory,
    RelationshipDirection,
    RelationshipTypeDefinition,
    Tier,
    _is_valid_property_value,
    _pascal_to_kebab,
)
from k_extract.domain.relationships import RelationshipInstance

# --- Fixtures ---


def _product_type() -> EntityTypeDefinition:
    return EntityTypeDefinition(
        type="Product",
        description="A product in the catalog",
        tier=Tier.FILE_BASED,
        required_properties=["name", "description"],
        optional_properties=["url", "tags"],
        property_definitions={
            "name": "Product name",
            "description": "Product description",
            "url": "Product URL",
            "tags": "Product tags",
        },
        tag_definitions={"core": "Core product", "addon": "Add-on product"},
    )


def _data_source_type() -> EntityTypeDefinition:
    return EntityTypeDefinition(
        type="DataSource",
        description="A data source for extraction",
        tier=Tier.STRUCTURAL,
        required_properties=["path"],
        optional_properties=[],
        property_definitions={"path": "Path to data source"},
    )


def _repo_type() -> EntityTypeDefinition:
    return EntityTypeDefinition(
        type="Repo",
        description="A code repository",
        tier=Tier.FILE_BASED,
        required_properties=["name"],
        optional_properties=["url"],
        property_definitions={
            "name": "Repository name",
            "url": "Repository URL",
        },
    )


def _owns_rel_type() -> RelationshipTypeDefinition:
    return RelationshipTypeDefinition(
        source_entity_type="Product",
        target_entity_type="Repo",
        forward_relationship=RelationshipDirection(
            type="OWNS", description="Product owns repository"
        ),
        reverse_relationship=RelationshipDirection(
            type="OWNED_BY", description="Repository owned by product"
        ),
        category=RelationshipCategory.AGENT_MANAGED,
        required_parameters=["since"],
        optional_parameters=["notes"],
        property_definitions={
            "since": "When ownership started",
            "notes": "Additional notes",
        },
    )


def _contains_rel_type() -> RelationshipTypeDefinition:
    return RelationshipTypeDefinition(
        source_entity_type="DataSource",
        target_entity_type="Product",
        forward_relationship=RelationshipDirection(
            type="CONTAINS", description="Data source contains product"
        ),
        category=RelationshipCategory.STRUCTURAL,
        required_parameters=[],
        optional_parameters=[],
    )


def _sample_ontology() -> Ontology:
    """Build a sample ontology for testing."""
    product_type = _product_type()
    repo_type = _repo_type()
    ds_type = _data_source_type()
    owns_rel = _owns_rel_type()
    contains_rel = _contains_rel_type()

    product = EntityInstance(
        slug="product:openshift",
        properties={"name": "OpenShift", "description": "Container platform"},
    )
    repo = EntityInstance(
        slug="repo:my-repo",
        properties={"name": "my-repo"},
    )
    ds = EntityInstance(
        slug="data-source:git",
        properties={"path": "/data/git"},
    )

    return Ontology(
        entity_types={
            "Product": product_type,
            "Repo": repo_type,
            "DataSource": ds_type,
        },
        relationship_types={
            owns_rel.composite_key: owns_rel,
            contains_rel.composite_key: contains_rel,
        },
        entities={
            "product:openshift": product,
            "repo:my-repo": repo,
            "data-source:git": ds,
        },
    )


# --- Tier Tests ---


class TestTier:
    def test_tier_values(self) -> None:
        assert Tier.STRUCTURAL == "structural"
        assert Tier.FILE_BASED == "file-based"
        assert Tier.SCENARIO_BASED == "scenario-based"

    def test_tier_from_string(self) -> None:
        assert Tier("structural") == Tier.STRUCTURAL
        assert Tier("file-based") == Tier.FILE_BASED
        assert Tier("scenario-based") == Tier.SCENARIO_BASED

    def test_invalid_tier(self) -> None:
        with pytest.raises(ValueError):
            Tier("invalid")


# --- EntityTypeDefinition Tests ---


class TestEntityTypeDefinition:
    def test_valid_entity_type(self) -> None:
        et = _product_type()
        assert et.type == "Product"
        assert et.tier == Tier.FILE_BASED
        assert "name" in et.required_properties
        assert "url" in et.optional_properties
        assert "core" in et.tag_definitions

    def test_pascal_case_validation(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            EntityTypeDefinition(
                type="invalid_name",
                description="Bad",
                tier=Tier.FILE_BASED,
                required_properties=[],
                optional_properties=[],
                property_definitions={},
            )

    def test_pascal_case_with_numbers(self) -> None:
        et = EntityTypeDefinition(
            type="Product2",
            description="Product v2",
            tier=Tier.FILE_BASED,
            required_properties=[],
            optional_properties=[],
            property_definitions={},
        )
        assert et.type == "Product2"

    def test_single_lowercase_letter_rejected(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            EntityTypeDefinition(
                type="product",
                description="Bad",
                tier=Tier.FILE_BASED,
                required_properties=[],
                optional_properties=[],
                property_definitions={},
            )

    def test_is_structural(self) -> None:
        ds_type = _data_source_type()
        assert ds_type.is_structural is True

        product_type = _product_type()
        assert product_type.is_structural is False

    def test_property_defaults_optional(self) -> None:
        et = EntityTypeDefinition(
            type="Simple",
            description="Simple type",
            tier=Tier.FILE_BASED,
            required_properties=[],
            optional_properties=[],
            property_definitions={},
        )
        assert et.property_defaults == {}
        assert et.tag_definitions == {}

    def test_property_defaults_with_values(self) -> None:
        et = EntityTypeDefinition(
            type="Product",
            description="A product",
            tier=Tier.FILE_BASED,
            required_properties=["name"],
            optional_properties=["is_active", "count", "tags"],
            property_definitions={
                "name": "Name",
                "is_active": "Active flag",
                "count": "Count",
                "tags": "Tags",
            },
            property_defaults={
                "is_active": True,
                "count": 0,
                "tags": ["default"],
            },
        )
        assert et.property_defaults["is_active"] is True
        assert et.property_defaults["count"] == 0
        assert et.property_defaults["tags"] == ["default"]


# --- RelationshipDirection Tests ---


class TestRelationshipDirection:
    def test_valid_direction(self) -> None:
        rd = RelationshipDirection(type="OWNS", description="Ownership")
        assert rd.type == "OWNS"
        assert rd.description == "Ownership"

    def test_upper_snake_case_validation(self) -> None:
        with pytest.raises(ValidationError, match="UPPER_SNAKE_CASE"):
            RelationshipDirection(type="owns", description="Bad")

    def test_multi_word_upper_snake_case(self) -> None:
        rd = RelationshipDirection(type="HAS_FILE", description="Has file")
        assert rd.type == "HAS_FILE"

    def test_description_optional(self) -> None:
        rd = RelationshipDirection(type="OWNS")
        assert rd.description == ""


# --- RelationshipTypeDefinition Tests ---


class TestRelationshipTypeDefinition:
    def test_valid_relationship_type(self) -> None:
        rt = _owns_rel_type()
        assert rt.source_entity_type == "Product"
        assert rt.target_entity_type == "Repo"
        assert rt.forward_relationship.type == "OWNS"
        assert rt.reverse_relationship is not None
        assert rt.reverse_relationship.type == "OWNED_BY"

    def test_composite_key_construction(self) -> None:
        rt = _owns_rel_type()
        assert rt.composite_key == "Product|OWNS|Repo"

    def test_composite_key_parsing(self) -> None:
        source, rel, target = RelationshipTypeDefinition.parse_composite_key(
            "Product|OWNS|Repo"
        )
        assert source == "Product"
        assert rel == "OWNS"
        assert target == "Repo"

    def test_composite_key_parsing_invalid_too_few_parts(self) -> None:
        with pytest.raises(ValueError, match="exactly 3 parts"):
            RelationshipTypeDefinition.parse_composite_key("Product|OWNS")

    def test_composite_key_parsing_invalid_too_many_parts(self) -> None:
        with pytest.raises(ValueError, match="exactly 3 parts"):
            RelationshipTypeDefinition.parse_composite_key("A|B|C|D")

    def test_composite_key_parsing_no_separator(self) -> None:
        with pytest.raises(ValueError, match="exactly 3 parts"):
            RelationshipTypeDefinition.parse_composite_key("ProductOWNSRepo")

    def test_reverse_relationship_optional(self) -> None:
        rt = _contains_rel_type()
        assert rt.reverse_relationship is None

    def test_source_type_must_be_pascal_case(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            RelationshipTypeDefinition(
                source_entity_type="product",
                target_entity_type="Repo",
                forward_relationship=RelationshipDirection(type="OWNS"),
                category=RelationshipCategory.AGENT_MANAGED,
                required_parameters=[],
                optional_parameters=[],
            )

    def test_target_type_must_be_pascal_case(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            RelationshipTypeDefinition(
                source_entity_type="Product",
                target_entity_type="repo",
                forward_relationship=RelationshipDirection(type="OWNS"),
                category=RelationshipCategory.AGENT_MANAGED,
                required_parameters=[],
                optional_parameters=[],
            )

    def test_is_structural(self) -> None:
        contains = _contains_rel_type()
        assert contains.is_structural is True

        owns = _owns_rel_type()
        assert owns.is_structural is False

    def test_property_definitions_optional(self) -> None:
        rt = _contains_rel_type()
        assert rt.property_definitions == {}


# --- Ontology Container Tests ---


class TestOntologyContainer:
    def test_empty_ontology(self) -> None:
        ontology = Ontology()
        assert ontology.entity_types == {}
        assert ontology.relationship_types == {}
        assert ontology.entities == {}
        assert ontology.relationships == []

    def test_entity_type_key_mismatch_rejected(self) -> None:
        product_type = _product_type()
        with pytest.raises(ValidationError, match="does not match"):
            Ontology(entity_types={"WrongKey": product_type})

    def test_entity_key_slug_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            Ontology(
                entities={
                    "wrong-key": EntityInstance(slug="product:test", properties={})
                }
            )

    def test_relationship_type_key_mismatch_rejected(self) -> None:
        owns_rel = _owns_rel_type()
        with pytest.raises(ValidationError, match="does not match"):
            Ontology(relationship_types={"WrongKey": owns_rel})

    def test_duplicate_relationship_rejected(self) -> None:
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={"since": "2024"},
        )
        with pytest.raises(ValidationError, match="Duplicate relationship"):
            Ontology(relationships=[rel, rel])

    def test_same_composite_key_different_slugs_allowed(self) -> None:
        rel1 = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:a",
            target_entity_type="Repo",
            target_slug="repo:b",
            relationship_type="OWNS",
            properties={},
        )
        rel2 = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:c",
            target_entity_type="Repo",
            target_slug="repo:d",
            relationship_type="OWNS",
            properties={},
        )
        ontology = Ontology(relationships=[rel1, rel2])
        assert len(ontology.relationships) == 2

    def test_get_entity_type(self) -> None:
        ontology = _sample_ontology()
        et = ontology.get_entity_type("Product")
        assert et is not None
        assert et.type == "Product"

    def test_get_entity_type_not_found(self) -> None:
        ontology = _sample_ontology()
        assert ontology.get_entity_type("NonExistent") is None

    def test_get_relationship_type(self) -> None:
        ontology = _sample_ontology()
        rt = ontology.get_relationship_type("Product|OWNS|Repo")
        assert rt is not None
        assert rt.forward_relationship.type == "OWNS"

    def test_get_relationship_type_not_found(self) -> None:
        ontology = _sample_ontology()
        assert ontology.get_relationship_type("X|Y|Z") is None

    def test_get_entity_by_slug(self) -> None:
        ontology = _sample_ontology()
        entity = ontology.get_entity_by_slug("product:openshift")
        assert entity is not None
        assert entity.slug == "product:openshift"

    def test_get_entity_by_slug_not_found(self) -> None:
        ontology = _sample_ontology()
        assert ontology.get_entity_by_slug("product:nonexistent") is None

    def test_get_entities_by_type(self) -> None:
        ontology = _sample_ontology()
        products = ontology.get_entities_by_type("Product")
        assert len(products) == 1
        assert products[0].slug == "product:openshift"

    def test_get_entities_by_type_empty(self) -> None:
        ontology = _sample_ontology()
        results = ontology.get_entities_by_type("NonExistent")
        assert results == []

    def test_get_relationships_by_composite_key(self) -> None:
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={"since": "2024"},
        )
        ontology.relationships.append(rel)
        results = ontology.get_relationships_by_composite_key("Product|OWNS|Repo")
        assert len(results) == 1
        assert results[0].source_slug == "product:openshift"

    def test_get_relationships_by_composite_key_empty(self) -> None:
        ontology = _sample_ontology()
        results = ontology.get_relationships_by_composite_key("X|Y|Z")
        assert results == []

    def test_find_entity_type_for_slug(self) -> None:
        ontology = _sample_ontology()
        et = ontology.find_entity_type_for_slug("product:openshift")
        assert et is not None
        assert et.type == "Product"

    def test_find_entity_type_for_slug_multi_word(self) -> None:
        ontology = _sample_ontology()
        et = ontology.find_entity_type_for_slug("data-source:git")
        assert et is not None
        assert et.type == "DataSource"

    def test_find_entity_type_for_slug_acronym(self) -> None:
        sre_type = EntityTypeDefinition(
            type="SREFile",
            description="An SRE file",
            tier=Tier.FILE_BASED,
            required_properties=["path"],
            optional_properties=[],
            property_definitions={"path": "File path"},
        )
        ontology = Ontology(
            entity_types={"SREFile": sre_type},
            entities={
                "sre-file:alert-rules": EntityInstance(
                    slug="sre-file:alert-rules", properties={"path": "/sre/alerts"}
                )
            },
        )
        et = ontology.find_entity_type_for_slug("sre-file:alert-rules")
        assert et is not None
        assert et.type == "SREFile"

    def test_get_entities_by_type_acronym(self) -> None:
        sre_type = EntityTypeDefinition(
            type="SREFile",
            description="An SRE file",
            tier=Tier.FILE_BASED,
            required_properties=["path"],
            optional_properties=[],
            property_definitions={"path": "File path"},
        )
        entity = EntityInstance(
            slug="sre-file:alert-rules", properties={"path": "/sre/alerts"}
        )
        ontology = Ontology(
            entity_types={"SREFile": sre_type},
            entities={"sre-file:alert-rules": entity},
        )
        results = ontology.get_entities_by_type("SREFile")
        assert len(results) == 1
        assert results[0].slug == "sre-file:alert-rules"

    def test_kebab_prefix_collision_sre_rejected(self) -> None:
        """SREFile and SreFile both map to 'sre-file' — must be rejected."""
        sre_type_1 = EntityTypeDefinition(
            type="SREFile",
            description="SRE file v1",
            tier=Tier.FILE_BASED,
            required_properties=[],
            optional_properties=[],
            property_definitions={},
        )
        sre_type_2 = EntityTypeDefinition(
            type="SreFile",
            description="SRE file v2",
            tier=Tier.FILE_BASED,
            required_properties=[],
            optional_properties=[],
            property_definitions={},
        )
        with pytest.raises(ValidationError, match="kebab-case prefix"):
            Ontology(
                entity_types={"SREFile": sre_type_1, "SreFile": sre_type_2},
            )

    def test_kebab_prefix_collision_api_rejected(self) -> None:
        """APIClient and ApiClient both map to 'api-client' — must be rejected."""
        api_type_1 = EntityTypeDefinition(
            type="APIClient",
            description="API client v1",
            tier=Tier.FILE_BASED,
            required_properties=[],
            optional_properties=[],
            property_definitions={},
        )
        api_type_2 = EntityTypeDefinition(
            type="ApiClient",
            description="API client v2",
            tier=Tier.FILE_BASED,
            required_properties=[],
            optional_properties=[],
            property_definitions={},
        )
        with pytest.raises(ValidationError, match="kebab-case prefix"):
            Ontology(
                entity_types={"APIClient": api_type_1, "ApiClient": api_type_2},
            )

    def test_no_kebab_collision_for_distinct_prefixes(self) -> None:
        """Entity types with distinct kebab prefixes are accepted."""
        ontology = _sample_ontology()
        # Product → product, Repo → repo, DataSource → data-source — all distinct
        assert len(ontology.entity_types) == 3

    def test_find_entity_type_for_slug_not_found(self) -> None:
        ontology = _sample_ontology()
        assert ontology.find_entity_type_for_slug("unknown:thing") is None

    def test_is_structural_entity_type(self) -> None:
        ontology = _sample_ontology()
        assert ontology.is_structural_entity_type("DataSource") is True
        assert ontology.is_structural_entity_type("Product") is False
        assert ontology.is_structural_entity_type("NonExistent") is False


# --- Entity Validation Tests ---


class TestEntityValidation:
    def test_valid_entity(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={"name": "Test", "description": "A test product"},
        )
        errors = ontology.validate_entity(entity)
        assert errors == []

    def test_unknown_entity_type(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="widget:unknown",
            properties={"name": "Unknown"},
        )
        errors = ontology.validate_entity(entity)
        assert len(errors) == 1
        assert "Unknown entity type" in errors[0]

    def test_missing_required_property(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={"name": "Test"},  # missing 'description'
        )
        errors = ontology.validate_entity(entity)
        assert any("Missing required property 'description'" in e for e in errors)

    def test_missing_all_required_properties(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={},
        )
        errors = ontology.validate_entity(entity)
        assert any("Missing required property 'name'" in e for e in errors)
        assert any("Missing required property 'description'" in e for e in errors)

    def test_extra_properties_accepted(self) -> None:
        """Spec section 4.1 does not reject unknown properties — only required
        properties, property types, tags, slug presence, and slug uniqueness."""
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={
                "name": "Test",
                "description": "Desc",
                "extra_metadata": "value",
            },
        )
        errors = ontology.validate_entity(entity)
        assert errors == []

    def test_valid_tags(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={
                "name": "Test",
                "description": "Desc",
                "tags": ["core", "addon"],
            },
        )
        errors = ontology.validate_entity(entity)
        assert errors == []

    def test_invalid_tag(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={
                "name": "Test",
                "description": "Desc",
                "tags": ["core", "nonexistent"],
            },
        )
        errors = ontology.validate_entity(entity)
        assert any("Invalid tag 'nonexistent'" in e for e in errors)

    def test_tags_as_string_rejected(self) -> None:
        """Tags must be an array of strings; a scalar string must produce an error."""
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={
                "name": "Test",
                "description": "Desc",
                "tags": "core",
            },
        )
        errors = ontology.validate_entity(entity)
        assert any("array of strings" in e for e in errors)

    def test_tags_as_int_rejected(self) -> None:
        """Tags must be an array of strings; an integer must produce an error."""
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={
                "name": "Test",
                "description": "Desc",
                "tags": 42,
            },
        )
        errors = ontology.validate_entity(entity)
        assert any("array of strings" in e for e in errors)

    def test_tags_as_bool_rejected(self) -> None:
        """Tags must be an array of strings; a boolean must produce an error."""
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={
                "name": "Test",
                "description": "Desc",
                "tags": True,
            },
        )
        errors = ontology.validate_entity(entity)
        assert any("array of strings" in e for e in errors)

    def test_structural_type_protection(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="data-source:local",
            properties={"path": "/data/local"},
        )
        errors = ontology.validate_entity(entity)
        assert len(errors) == 1
        assert (
            "structural type" in errors[0].lower() or "protected" in errors[0].lower()
        )

    def test_file_based_type_not_protected(self) -> None:
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="product:test",
            properties={"name": "Test", "description": "A test product"},
        )
        errors = ontology.validate_entity(entity)
        assert errors == []

    def test_entity_with_multi_word_type_slug(self) -> None:
        """Multi-word PascalCase types resolve correctly in slug lookup.

        DataSource is structural, so validation rejects edits — but the type
        lookup itself must still work (confirmed by getting a structural
        protection error rather than an 'unknown type' error).
        """
        ontology = _sample_ontology()
        entity = EntityInstance(
            slug="data-source:local",
            properties={"path": "/data/local"},
        )
        errors = ontology.validate_entity(entity)
        assert len(errors) == 1
        assert "structural" in errors[0].lower()


# --- Relationship Validation Tests ---


class TestRelationshipValidation:
    def test_valid_relationship(self) -> None:
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={"since": "2024-01-01"},
        )
        errors = ontology.validate_relationship(rel)
        assert errors == []

    def test_unknown_relationship_type(self) -> None:
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="MANAGES",
            properties={},
        )
        errors = ontology.validate_relationship(rel)
        assert len(errors) == 1
        assert "Unknown relationship type" in errors[0]

    def test_missing_required_parameter(self) -> None:
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={},  # missing 'since'
        )
        errors = ontology.validate_relationship(rel)
        assert any("Missing required parameter 'since'" in e for e in errors)

    def test_source_entity_not_found(self) -> None:
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:nonexistent",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={"since": "2024"},
        )
        errors = ontology.validate_relationship(rel)
        assert any("Source entity not found" in e for e in errors)

    def test_target_entity_not_found(self) -> None:
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:nonexistent",
            relationship_type="OWNS",
            properties={"since": "2024"},
        )
        errors = ontology.validate_relationship(rel)
        assert any("Target entity not found" in e for e in errors)

    def test_structural_relationship_type_protection(self) -> None:
        """Structural relationship types are protected from agent modification."""
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="DataSource",
            source_slug="data-source:git",
            target_entity_type="Product",
            target_slug="product:openshift",
            relationship_type="CONTAINS",
            properties={},
        )
        errors = ontology.validate_relationship(rel)
        assert len(errors) == 1
        err = errors[0].lower()
        assert "structural type" in err or "protected" in err

    def test_source_entity_type_mismatch(self) -> None:
        """Source slug's type prefix must match declared source_entity_type."""
        ontology = _sample_ontology()
        # repo:my-repo is a Repo, not a Product
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="repo:my-repo",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={"since": "2024"},
        )
        errors = ontology.validate_relationship(rel)
        assert any("Source entity" in e and "expected" in e for e in errors)

    def test_target_entity_type_mismatch(self) -> None:
        """Target slug's type prefix must match declared target_entity_type."""
        ontology = _sample_ontology()
        # product:openshift is a Product, not a Repo
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="product:openshift",
            relationship_type="OWNS",
            properties={"since": "2024"},
        )
        errors = ontology.validate_relationship(rel)
        assert any("Target entity" in e and "expected" in e for e in errors)

    def test_both_entity_types_mismatch(self) -> None:
        """Both source and target type mismatches should be reported."""
        ontology = _sample_ontology()
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="repo:my-repo",
            target_entity_type="Repo",
            target_slug="product:openshift",
            relationship_type="OWNS",
            properties={"since": "2024"},
        )
        errors = ontology.validate_relationship(rel)
        assert any("Source entity" in e and "expected" in e for e in errors)
        assert any("Target entity" in e and "expected" in e for e in errors)


# --- Helper Function Tests ---


class TestHelperFunctions:
    def test_is_valid_property_value_string(self) -> None:
        assert _is_valid_property_value("hello") is True

    def test_is_valid_property_value_bool(self) -> None:
        assert _is_valid_property_value(True) is True
        assert _is_valid_property_value(False) is True

    def test_is_valid_property_value_int(self) -> None:
        assert _is_valid_property_value(42) is True
        assert _is_valid_property_value(0) is True

    def test_is_valid_property_value_list_of_strings(self) -> None:
        assert _is_valid_property_value(["a", "b", "c"]) is True

    def test_is_valid_property_value_empty_list(self) -> None:
        assert _is_valid_property_value([]) is True

    def test_is_valid_property_value_mixed_list_invalid(self) -> None:
        assert _is_valid_property_value(["a", 1]) is False

    def test_is_valid_property_value_float_invalid(self) -> None:
        assert _is_valid_property_value(3.14) is False

    def test_is_valid_property_value_dict_invalid(self) -> None:
        assert _is_valid_property_value({"key": "value"}) is False

    def test_is_valid_property_value_none_invalid(self) -> None:
        assert _is_valid_property_value(None) is False

    def test_pascal_to_kebab_simple(self) -> None:
        assert _pascal_to_kebab("Product") == "product"

    def test_pascal_to_kebab_multi_word(self) -> None:
        assert _pascal_to_kebab("DataSource") == "data-source"

    def test_pascal_to_kebab_three_words(self) -> None:
        assert _pascal_to_kebab("TestSuiteRunner") == "test-suite-runner"

    def test_pascal_to_kebab_acronym(self) -> None:
        assert _pascal_to_kebab("SREFile") == "sre-file"

    def test_pascal_to_kebab_acronym_at_end(self) -> None:
        assert _pascal_to_kebab("ProductSRE") == "product-sre"

    def test_pascal_to_kebab_product_file(self) -> None:
        assert _pascal_to_kebab("ProductFile") == "product-file"

    def test_pascal_to_kebab_single_letter(self) -> None:
        assert _pascal_to_kebab("A") == "a"
