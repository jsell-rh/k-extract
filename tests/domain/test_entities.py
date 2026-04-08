"""Tests for entity instance model and slug validation."""

import pytest
from pydantic import ValidationError

from k_extract.domain.entities import EntityInstance


class TestEntityInstanceCreation:
    """Test creating valid entity instances."""

    def test_valid_entity(self) -> None:
        entity = EntityInstance(
            slug="product:openshift-hyperfleet",
            properties={"name": "OpenShift HyperFleet"},
        )
        assert entity.slug == "product:openshift-hyperfleet"
        assert entity.properties == {"name": "OpenShift HyperFleet"}

    def test_entity_type_from_slug(self) -> None:
        entity = EntityInstance(
            slug="product:openshift-hyperfleet",
            properties={},
        )
        assert entity.entity_type == "product"

    def test_canonical_name_from_slug(self) -> None:
        entity = EntityInstance(
            slug="product:openshift-hyperfleet",
            properties={},
        )
        assert entity.canonical_name == "openshift-hyperfleet"

    def test_slug_with_underscores(self) -> None:
        entity = EntityInstance(
            slug="repo:my_repo",
            properties={},
        )
        assert entity.slug == "repo:my_repo"
        assert entity.entity_type == "repo"
        assert entity.canonical_name == "my_repo"

    def test_entity_with_all_property_types(self) -> None:
        entity = EntityInstance(
            slug="product:test",
            properties={
                "name": "Test Product",
                "is_active": True,
                "count": 42,
                "tags": ["alpha", "beta"],
            },
        )
        assert entity.properties["name"] == "Test Product"
        assert entity.properties["is_active"] is True
        assert entity.properties["count"] == 42
        assert entity.properties["tags"] == ["alpha", "beta"]

    def test_slug_with_numbers(self) -> None:
        entity = EntityInstance(
            slug="test-case:auth-flow-v2",
            properties={},
        )
        assert entity.entity_type == "test-case"
        assert entity.canonical_name == "auth-flow-v2"

    def test_multi_word_type_prefix(self) -> None:
        entity = EntityInstance(
            slug="test-case:test-auth-flow",
            properties={},
        )
        assert entity.entity_type == "test-case"

    def test_empty_properties(self) -> None:
        entity = EntityInstance(slug="product:x1", properties={})
        assert entity.properties == {}


class TestSlugValidation:
    """Test slug format validation."""

    def test_empty_slug_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Slug must not be empty"):
            EntityInstance(slug="", properties={})

    def test_uppercase_slug_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            EntityInstance(slug="Product:openshift", properties={})

    def test_slug_without_colon_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            EntityInstance(slug="productopenshift", properties={})

    def test_slug_with_spaces_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            EntityInstance(slug="product:open shift", properties={})

    def test_slug_with_only_colon_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            EntityInstance(slug=":", properties={})

    def test_slug_missing_canonical_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            EntityInstance(slug="product:", properties={})

    def test_slug_missing_type_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            EntityInstance(slug=":name", properties={})

    def test_slug_with_dots_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid slug format"):
            EntityInstance(slug="product:my.product", properties={})
