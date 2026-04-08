"""Tests for relationship instance model and validation."""

import pytest
from pydantic import ValidationError

from k_extract.domain.relationships import RelationshipInstance


class TestRelationshipInstanceCreation:
    """Test creating valid relationship instances."""

    def test_valid_relationship(self) -> None:
        rel = RelationshipInstance(
            source_entity_type="TestSuite",
            source_slug="test-suite:auth-tests",
            target_entity_type="TestCase",
            target_slug="test-case:login-test",
            relationship_type="CONTAINS",
            properties={},
        )
        assert rel.source_entity_type == "TestSuite"
        assert rel.source_slug == "test-suite:auth-tests"
        assert rel.target_entity_type == "TestCase"
        assert rel.target_slug == "test-case:login-test"
        assert rel.relationship_type == "CONTAINS"
        assert rel.properties == {}

    def test_relationship_with_properties(self) -> None:
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={"weight": 5, "label": "primary"},
        )
        assert rel.properties["weight"] == 5
        assert rel.properties["label"] == "primary"

    def test_relationship_properties_accept_any_type(self) -> None:
        """Spec section 3.1 describes relationship properties as a generic object
        with no type constraints — unlike entity properties (section 2.3)."""
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:openshift",
            target_entity_type="Repo",
            target_slug="repo:my-repo",
            relationship_type="OWNS",
            properties={
                "weight": 0.75,
                "metadata": {"nested": "object"},
                "count": 42,
                "active": True,
                "label": "primary",
            },
        )
        assert rel.properties["weight"] == 0.75
        assert rel.properties["metadata"] == {"nested": "object"}

    def test_composite_key(self) -> None:
        rel = RelationshipInstance(
            source_entity_type="TestSuite",
            source_slug="test-suite:auth-tests",
            target_entity_type="TestCase",
            target_slug="test-case:login-test",
            relationship_type="CONTAINS",
            properties={},
        )
        assert rel.composite_key == "TestSuite|CONTAINS|TestCase"

    def test_composite_key_multi_word(self) -> None:
        rel = RelationshipInstance(
            source_entity_type="DataSource",
            source_slug="data-source:git-repo",
            target_entity_type="ProductFile",
            target_slug="product-file:readme",
            relationship_type="HAS_FILE",
            properties={},
        )
        assert rel.composite_key == "DataSource|HAS_FILE|ProductFile"


class TestRelationshipValidation:
    """Test relationship instance validation rules."""

    def test_source_type_must_be_pascal_case(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            RelationshipInstance(
                source_entity_type="test_suite",
                source_slug="test-suite:auth",
                target_entity_type="TestCase",
                target_slug="test-case:login",
                relationship_type="CONTAINS",
                properties={},
            )

    def test_target_type_must_be_pascal_case(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            RelationshipInstance(
                source_entity_type="TestSuite",
                source_slug="test-suite:auth",
                target_entity_type="test_case",
                target_slug="test-case:login",
                relationship_type="CONTAINS",
                properties={},
            )

    def test_relationship_type_must_be_upper_snake_case(self) -> None:
        with pytest.raises(ValidationError, match="UPPER_SNAKE_CASE"):
            RelationshipInstance(
                source_entity_type="TestSuite",
                source_slug="test-suite:auth",
                target_entity_type="TestCase",
                target_slug="test-case:login",
                relationship_type="contains",
                properties={},
            )

    def test_source_slug_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError, match="Slug must not be empty"):
            RelationshipInstance(
                source_entity_type="TestSuite",
                source_slug="",
                target_entity_type="TestCase",
                target_slug="test-case:login",
                relationship_type="CONTAINS",
                properties={},
            )

    def test_target_slug_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError, match="Slug must not be empty"):
            RelationshipInstance(
                source_entity_type="TestSuite",
                source_slug="test-suite:auth",
                target_entity_type="TestCase",
                target_slug="",
                relationship_type="CONTAINS",
                properties={},
            )

    def test_source_slug_must_be_valid_format(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            RelationshipInstance(
                source_entity_type="TestSuite",
                source_slug="INVALID",
                target_entity_type="TestCase",
                target_slug="test-case:login",
                relationship_type="CONTAINS",
                properties={},
            )

    def test_target_slug_must_be_valid_format(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            RelationshipInstance(
                source_entity_type="TestSuite",
                source_slug="test-suite:auth",
                target_entity_type="TestCase",
                target_slug="Bad:Slug",
                relationship_type="CONTAINS",
                properties={},
            )
