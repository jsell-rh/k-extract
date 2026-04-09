"""Tests for extraction agent tools.

Covers all five tools (search_entities, search_relationships, manage_entity,
manage_relationship, validate_and_commit), every mode, validation paths,
result capping, and the tool factory.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event

from k_extract.domain.entities import EntityInstance
from k_extract.domain.ontology import (
    EntityTypeDefinition,
    Ontology,
    RelationshipCategory,
    RelationshipDirection,
    RelationshipTypeDefinition,
    Tier,
)
from k_extract.domain.relationships import RelationshipInstance
from k_extract.extraction.store import OntologyStore
from k_extract.extraction.tools import (
    create_extraction_tools,
    create_tool_server,
)

# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture()
def engine():
    """In-memory SQLite engine."""
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _set_wal(dbapi_conn, _rec):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return eng


@pytest.fixture()
def ontology() -> Ontology:
    """Test ontology with entity/relationship type definitions."""
    product_type = EntityTypeDefinition(
        type="Product",
        description="A product entity",
        tier=Tier.FILE_BASED,
        required_properties=["title", "description"],
        optional_properties=[
            "file_path",
            "tags",
            "processed_by_agent",
            "summary",
        ],
        property_definitions={
            "title": "Product title",
            "description": "Product description",
            "file_path": "Source file path",
            "tags": "Classification tags",
            "processed_by_agent": "Whether the agent processed this entity",
            "summary": "A summary",
        },
        tag_definitions={
            "frontend": "Frontend component",
            "backend": "Backend component",
        },
    )
    source_type = EntityTypeDefinition(
        type="DataSource",
        description="A data source (structural)",
        tier=Tier.STRUCTURAL,
        required_properties=["name"],
        optional_properties=[],
        property_definitions={"name": "Source name"},
    )
    ref_type = RelationshipTypeDefinition(
        source_entity_type="Product",
        target_entity_type="Product",
        forward_relationship=RelationshipDirection(
            type="REFERENCES", description="References another product"
        ),
        category=RelationshipCategory.AGENT_MANAGED,
        required_parameters=["context"],
        optional_parameters=[],
        property_definitions={"context": "Reference context"},
    )
    contains_type = RelationshipTypeDefinition(
        source_entity_type="DataSource",
        target_entity_type="Product",
        forward_relationship=RelationshipDirection(
            type="CONTAINS", description="Data source contains product"
        ),
        category=RelationshipCategory.STRUCTURAL,
        required_parameters=[],
        optional_parameters=[],
    )
    return Ontology(
        entity_types={
            "Product": product_type,
            "DataSource": source_type,
        },
        relationship_types={
            ref_type.composite_key: ref_type,
            contains_type.composite_key: contains_type,
        },
    )


@pytest.fixture()
def store(engine, ontology) -> OntologyStore:
    """OntologyStore backed by an in-memory SQLite engine."""
    return OntologyStore(engine, ontology)


@pytest.fixture()
def worker_id() -> str:
    return "worker-01"


@pytest.fixture()
def tools(worker_id, store, ontology):
    """Create the five extraction tool functions."""
    return create_extraction_tools(worker_id, store, ontology)


@pytest.fixture()
def search_entities_fn(tools):
    return tools[0]


@pytest.fixture()
def search_relationships_fn(tools):
    return tools[1]


@pytest.fixture()
def manage_entity_fn(tools):
    return tools[2]


@pytest.fixture()
def manage_relationship_fn(tools):
    return tools[3]


@pytest.fixture()
def validate_and_commit_fn(tools):
    return tools[4]


def _seed_products(store: OntologyStore, count: int = 3) -> list[EntityInstance]:
    """Seed the store with product entities."""
    entities = []
    for i in range(count):
        e = EntityInstance(
            slug=f"product:item-{i}",
            properties={
                "title": f"Item {i}",
                "description": f"Description for item {i}",
                "file_path": f"/src/item_{i}.py",
            },
        )
        store.upsert_entity(e)
        entities.append(e)
    return entities


def _parse_result(result: dict) -> tuple[bool, dict | list | str]:
    """Parse tool result into (is_error, parsed_data)."""
    is_error = result.get("is_error", False)
    text = result["content"][0]["text"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = text
    return is_error, data


# ================================================================== #
# search_entities tests
# ================================================================== #


class TestSearchEntitiesTypeDefinition:
    """Type Definition mode."""

    @pytest.mark.asyncio
    async def test_returns_schema(self, search_entities_fn, store, ontology):
        _seed_products(store, 2)
        result = await search_entities_fn.handler({"entity_type": "Product"})
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["entity_type"] == "Product"
        assert data["instance_count"] == 2
        assert data["description"] == "A product entity"
        assert data["tier"] == "file-based"
        assert "title" in data["required_properties"]
        assert "tag_definitions" in data

    @pytest.mark.asyncio
    async def test_unknown_type(self, search_entities_fn):
        result = await search_entities_fn.handler({"entity_type": "Unknown"})
        is_error, _ = _parse_result(result)
        assert is_error


class TestSearchEntitiesBySlug:
    """Get by Slugs mode."""

    @pytest.mark.asyncio
    async def test_returns_full_instances(self, search_entities_fn, store):
        entities = _seed_products(store)
        slugs = [e.slug for e in entities[:2]]
        result = await search_entities_fn.handler({"slugs": slugs})
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data) == 2
        assert data[0]["slug"] == slugs[0]
        assert "entity_type" in data[0]

    @pytest.mark.asyncio
    async def test_not_found(self, search_entities_fn):
        result = await search_entities_fn.handler({"slugs": ["product:nonexistent"]})
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_auto_resolves_entity_type(self, search_entities_fn, store):
        _seed_products(store, 1)
        result = await search_entities_fn.handler({"slugs": ["product:item-0"]})
        is_error, data = _parse_result(result)
        assert not is_error
        assert data[0]["entity_type"] == "Product"


class TestSearchEntitiesByFilePath:
    """Get by file_path mode."""

    @pytest.mark.asyncio
    async def test_returns_by_file_path(self, search_entities_fn, store):
        _seed_products(store)
        result = await search_entities_fn.handler({"file_path": "/src/item_0.py"})
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data) == 1
        assert data[0]["slug"] == "product:item-0"
        assert data[0]["entity_type"] == "Product"

    @pytest.mark.asyncio
    async def test_not_found(self, search_entities_fn, store):
        _seed_products(store)
        result = await search_entities_fn.handler({"file_path": "/nonexistent.py"})
        is_error, _ = _parse_result(result)
        assert is_error


class TestSearchEntitiesByTags:
    """Filter by Tags mode."""

    @pytest.mark.asyncio
    async def test_or_logic(self, search_entities_fn, store):
        e = EntityInstance(
            slug="product:tagged-1",
            properties={
                "title": "Tagged 1",
                "description": "A tagged product",
                "tags": ["frontend"],
            },
        )
        store.upsert_entity(e)
        e2 = EntityInstance(
            slug="product:tagged-2",
            properties={
                "title": "Tagged 2",
                "description": "Another tagged product",
                "tags": ["backend"],
            },
        )
        store.upsert_entity(e2)

        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "tags": ["frontend", "backend"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["total"] == 2
        slugs = [r["slug"] for r in data["results"]]
        assert "product:tagged-1" in slugs
        assert "product:tagged-2" in slugs

    @pytest.mark.asyncio
    async def test_warns_on_unknown_tags(self, search_entities_fn, store):
        _seed_products(store)
        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "tags": ["nonexistent-tag"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert "tag_warnings" in data

    @pytest.mark.asyncio
    async def test_result_cap(self, search_entities_fn, store):
        for i in range(15):
            e = EntityInstance(
                slug=f"product:tagged-{i}",
                properties={
                    "title": f"Tagged {i}",
                    "description": f"Desc {i}",
                    "tags": ["frontend"],
                },
            )
            store.upsert_entity(e)

        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "tags": ["frontend"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 10
        assert data["total"] == 15
        assert "warning" in data

    @pytest.mark.asyncio
    async def test_summary_format(self, search_entities_fn, store):
        e = EntityInstance(
            slug="product:summary-test",
            properties={
                "title": "My Title",
                "description": "Some desc",
                "tags": ["frontend"],
            },
        )
        store.upsert_entity(e)
        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "tags": ["frontend"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        item = data["results"][0]
        assert "slug" in item
        assert "title" in item


class TestSearchEntitiesByText:
    """Search by Text mode."""

    @pytest.mark.asyncio
    async def test_and_logic_case_insensitive(self, search_entities_fn, store):
        e = EntityInstance(
            slug="product:alpha-beta",
            properties={
                "title": "Alpha Product",
                "description": "Beta description",
            },
        )
        store.upsert_entity(e)

        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "search_terms": ["Alpha", "beta"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_no_match(self, search_entities_fn, store):
        _seed_products(store)
        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "search_terms": ["zzzzz-no-match"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["total"] == 0


class TestSearchEntitiesModifiers:
    """Common modifiers: limit, show_all, include_fields."""

    @pytest.mark.asyncio
    async def test_custom_limit(self, search_entities_fn, store):
        for i in range(5):
            e = EntityInstance(
                slug=f"product:item-{i}",
                properties={
                    "title": f"Item {i}",
                    "description": f"Desc {i}",
                    "tags": ["frontend"],
                },
            )
            store.upsert_entity(e)

        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "tags": ["frontend"],
                "limit": 2,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 2
        assert data["total"] == 5

    @pytest.mark.asyncio
    async def test_show_all(self, search_entities_fn, store):
        for i in range(15):
            e = EntityInstance(
                slug=f"product:item-{i}",
                properties={
                    "title": f"Item {i}",
                    "description": f"Desc {i}",
                    "tags": ["frontend"],
                },
            )
            store.upsert_entity(e)

        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "tags": ["frontend"],
                "show_all": True,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 15
        assert "warning" not in data

    @pytest.mark.asyncio
    async def test_include_fields(self, search_entities_fn, store):
        e = EntityInstance(
            slug="product:field-test",
            properties={
                "title": "Title",
                "description": "Desc",
                "summary": "My summary",
                "tags": ["frontend"],
            },
        )
        store.upsert_entity(e)

        result = await search_entities_fn.handler(
            {
                "entity_type": "Product",
                "tags": ["frontend"],
                "include_fields": ["summary", "description"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        item = data["results"][0]
        assert "slug" in item
        assert "summary" in item
        assert "description" in item
        # title should not be present when include_fields is used
        assert "title" not in item


class TestSearchEntitiesInputValidation:
    """Input validation and error cases."""

    @pytest.mark.asyncio
    async def test_no_inputs(self, search_entities_fn):
        result = await search_entities_fn.handler({})
        is_error, _ = _parse_result(result)
        assert is_error


# ================================================================== #
# search_relationships tests
# ================================================================== #


class TestSearchRelationshipsTypeDefinition:
    """Type Definition mode."""

    @pytest.mark.asyncio
    async def test_by_forward_type(self, search_relationships_fn, store):
        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data) >= 1
        assert data[0]["forward_type"] == "REFERENCES"
        assert data[0]["source_entity_type"] == "Product"
        assert "required_parameters" in data[0]

    @pytest.mark.asyncio
    async def test_by_composite_key(self, search_relationships_fn, store):
        result = await search_relationships_fn.handler(
            {
                "relationship_type": "Product|REFERENCES|Product",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data) == 1
        assert data[0]["composite_key"] == "Product|REFERENCES|Product"

    @pytest.mark.asyncio
    async def test_unknown_type(self, search_relationships_fn):
        result = await search_relationships_fn.handler(
            {
                "relationship_type": "UNKNOWN_TYPE",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_no_input(self, search_relationships_fn):
        result = await search_relationships_fn.handler({})
        is_error, _ = _parse_result(result)
        assert is_error


class TestSearchRelationshipsBySlug:
    """List by Slug mode."""

    @pytest.mark.asyncio
    async def test_one_slug(self, search_relationships_fn, store):
        _seed_products(store, 3)
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:item-0",
            target_entity_type="Product",
            target_slug="product:item-1",
            relationship_type="REFERENCES",
            properties={"context": "test"},
        )
        store.upsert_relationship(rel)

        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "slug": "product:item-0",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 1

    @pytest.mark.asyncio
    async def test_two_slugs(self, search_relationships_fn, store):
        _seed_products(store, 3)
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:item-0",
            target_entity_type="Product",
            target_slug="product:item-1",
            relationship_type="REFERENCES",
            properties={"context": "test"},
        )
        store.upsert_relationship(rel)

        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "slug": "product:item-0",
                "second_slug": "product:item-1",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data) == 1
        assert data[0]["source_slug"] == "product:item-0"

    @pytest.mark.asyncio
    async def test_result_cap(self, search_relationships_fn, store):
        _seed_products(store, 15)
        for i in range(1, 15):
            rel = RelationshipInstance(
                source_entity_type="Product",
                source_slug="product:item-0",
                target_entity_type="Product",
                target_slug=f"product:item-{i}",
                relationship_type="REFERENCES",
                properties={"context": f"ctx-{i}"},
            )
            store.upsert_relationship(rel)

        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "slug": "product:item-0",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 10
        assert data["total"] == 14
        assert "warning" in data


class TestSearchRelationshipsListAll:
    """List All mode."""

    @pytest.mark.asyncio
    async def test_list_all_without_slug(self, search_relationships_fn, store):
        """List All mode: list_instances=True, no slug — returns all instances."""
        _seed_products(store, 3)
        for i in range(1, 3):
            rel = RelationshipInstance(
                source_entity_type="Product",
                source_slug="product:item-0",
                target_entity_type="Product",
                target_slug=f"product:item-{i}",
                relationship_type="REFERENCES",
                properties={"context": f"ctx-{i}"},
            )
            store.upsert_relationship(rel)

        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "list_instances": True,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["total"] == 2
        assert len(data["results"]) == 2

    @pytest.mark.asyncio
    async def test_list_all_with_cap(self, search_relationships_fn, store):
        """List All mode respects the default result cap."""
        _seed_products(store, 15)
        for i in range(1, 15):
            rel = RelationshipInstance(
                source_entity_type="Product",
                source_slug="product:item-0",
                target_entity_type="Product",
                target_slug=f"product:item-{i}",
                relationship_type="REFERENCES",
                properties={"context": f"ctx-{i}"},
            )
            store.upsert_relationship(rel)

        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "list_instances": True,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 10
        assert data["total"] == 14
        assert "warning" in data

    @pytest.mark.asyncio
    async def test_list_all_show_all(self, search_relationships_fn, store):
        """List All mode with show_all=True returns all without cap."""
        _seed_products(store, 15)
        for i in range(1, 15):
            rel = RelationshipInstance(
                source_entity_type="Product",
                source_slug="product:item-0",
                target_entity_type="Product",
                target_slug=f"product:item-{i}",
                relationship_type="REFERENCES",
                properties={"context": f"ctx-{i}"},
            )
            store.upsert_relationship(rel)

        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "list_instances": True,
                "show_all": True,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 14
        assert "warning" not in data

    @pytest.mark.asyncio
    async def test_list_by_slug_show_all(self, search_relationships_fn, store):
        """List by Slug with show_all=True returns uncapped results."""
        _seed_products(store, 15)
        for i in range(1, 15):
            rel = RelationshipInstance(
                source_entity_type="Product",
                source_slug="product:item-0",
                target_entity_type="Product",
                target_slug=f"product:item-{i}",
                relationship_type="REFERENCES",
                properties={"context": f"ctx-{i}"},
            )
            store.upsert_relationship(rel)

        result = await search_relationships_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "slug": "product:item-0",
                "show_all": True,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data["results"]) == 14


# ================================================================== #
# manage_entity tests
# ================================================================== #


class TestManageEntity:
    """manage_entity tool tests."""

    @pytest.mark.asyncio
    async def test_edit_success(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"summary": "A new summary"},
                "mode": "edit",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "staged"
        # Verify entity_type is PascalCase (cross-tool format consistency)
        assert data["entity"]["entity_type"] == "Product"
        # Verify original properties preserved
        assert data["entity"]["properties"]["title"] == "Item 0"
        assert data["entity"]["properties"]["summary"] == "A new summary"

    @pytest.mark.asyncio
    async def test_invalid_mode(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"summary": "test"},
                "mode": "delete",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_structural_type_rejected(self, manage_entity_fn, store):
        e = EntityInstance(
            slug="data-source:my-source",
            properties={"name": "My Source"},
        )
        store.upsert_entity(e)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "DataSource",
                "slug": "data-source:my-source",
                "properties": {"name": "Changed"},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_unknown_entity_type(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "UnknownType",
                "slug": "unknown:test",
                "properties": {"x": "y"},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_slug_not_found(self, manage_entity_fn, store):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:nonexistent",
                "properties": {"summary": "test"},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_empty_properties(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_invalid_property_type(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"summary": {"nested": "dict"}},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_invalid_tag(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"tags": ["nonexistent-tag"]},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_valid_tags(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"tags": ["frontend", "backend"]},
                "mode": "edit",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["entity"]["properties"]["tags"] == ["frontend", "backend"]

    @pytest.mark.asyncio
    async def test_tags_wrong_type(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"tags": "not-a-list"},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_entity_type_mismatch(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "DataSource",
                "slug": "product:item-0",
                "properties": {"name": "test"},
                "mode": "edit",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_partial_update_preserves_existing(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"summary": "new summary"},
                "mode": "edit",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        props = data["entity"]["properties"]
        assert props["title"] == "Item 0"
        assert props["description"] == "Description for item 0"
        assert props["summary"] == "new summary"


class TestManageEntityCreate:
    """manage_entity create mode tests."""

    @pytest.mark.asyncio
    async def test_create_success(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:new-item",
                "properties": {
                    "title": "New Item",
                    "description": "A brand new item",
                },
                "mode": "create",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "staged"
        assert data["mode"] == "create"
        assert data["entity"]["slug"] == "product:new-item"
        assert data["entity"]["entity_type"] == "Product"
        assert data["entity"]["properties"]["title"] == "New Item"

    @pytest.mark.asyncio
    async def test_create_returns_existing_from_shared(self, manage_entity_fn, store):
        _seed_products(store, 1)
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {
                    "title": "Duplicate",
                    "description": "Already exists",
                },
                "mode": "create",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "already_exists"
        assert data["entity"]["slug"] == "product:item-0"
        assert data["entity"]["entity_type"] == "Product"

    @pytest.mark.asyncio
    async def test_create_returns_existing_from_staging(self, manage_entity_fn):
        # Create first
        await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:staged-item",
                "properties": {
                    "title": "Staged",
                    "description": "First creation",
                },
                "mode": "create",
            }
        )
        # Try again
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:staged-item",
                "properties": {
                    "title": "Duplicate",
                    "description": "Second creation",
                },
                "mode": "create",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "already_exists"
        assert data["entity"]["slug"] == "product:staged-item"
        assert data["entity"]["properties"]["title"] == "Staged"

    @pytest.mark.asyncio
    async def test_create_validates_slug_format(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "wrong-prefix:item",
                "properties": {
                    "title": "Bad Slug",
                    "description": "Wrong prefix",
                },
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_create_validates_slug_pydantic(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:UPPERCASE",
                "properties": {
                    "title": "Bad Slug",
                    "description": "Uppercase canonical",
                },
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_create_rejects_structural_type(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "DataSource",
                "slug": "data-source:my-ds",
                "properties": {"name": "My DS"},
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_create_validates_required_properties(self, manage_entity_fn):
        # Product requires "title" and "description"
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:missing-props",
                "properties": {"title": "Only Title"},
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_create_validates_property_types(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:bad-prop",
                "properties": {
                    "title": "Valid",
                    "description": {"nested": "dict"},
                },
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_create_validates_tags(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:bad-tags",
                "properties": {
                    "title": "Valid",
                    "description": "Valid",
                    "tags": ["nonexistent-tag"],
                },
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_create_with_valid_tags(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:tagged-new",
                "properties": {
                    "title": "Tagged",
                    "description": "With tags",
                    "tags": ["frontend"],
                },
                "mode": "create",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["entity"]["properties"]["tags"] == ["frontend"]

    @pytest.mark.asyncio
    async def test_create_empty_properties_rejected(self, manage_entity_fn):
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:empty",
                "properties": {},
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_create_then_commit(
        self, manage_entity_fn, validate_and_commit_fn, store
    ):
        """validate_and_commit successfully commits newly created entities."""
        # Create a new entity
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:commit-new",
                "properties": {
                    "title": "Commit New",
                    "description": "Testing commit of new entity",
                    "file_path": "/src/new.py",
                    "processed_by_agent": True,
                },
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert not is_error

        # Commit
        result = await validate_and_commit_fn.handler({"job_files": ["/src/new.py"]})
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "committed"

        # Verify entity is in shared store
        entity = store.get_entity_by_slug("product:commit-new")
        assert entity is not None
        assert entity.properties["title"] == "Commit New"

    @pytest.mark.asyncio
    async def test_create_then_edit(self, manage_entity_fn):
        """Create then edit same entity in same session."""
        # Create
        await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:create-edit",
                "properties": {
                    "title": "Original",
                    "description": "Original desc",
                },
                "mode": "create",
            }
        )
        # Edit
        result = await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:create-edit",
                "properties": {"summary": "Added summary"},
                "mode": "edit",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["entity"]["properties"]["title"] == "Original"
        assert data["entity"]["properties"]["summary"] == "Added summary"


# ================================================================== #
# manage_relationship tests
# ================================================================== #


class TestManageRelationshipCreate:
    """Create mode."""

    @pytest.mark.asyncio
    async def test_create_success(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "create",
                "properties": {"context": "related products"},
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "staged"
        assert data["mode"] == "create"
        assert data["relationship"]["composite_key"] == "Product|REFERENCES|Product"

    @pytest.mark.asyncio
    async def test_auto_detects_entity_types(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "create",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["relationship"]["source_entity_type"] == "Product"
        assert data["relationship"]["target_entity_type"] == "Product"

    @pytest.mark.asyncio
    async def test_returns_existing_on_duplicate(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        # Create first
        await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "create",
                "properties": {"context": "original"},
            }
        )
        # Try again
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "create",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "already_exists"
        assert data["relationship"]["source_slug"] == "product:item-0"
        assert data["relationship"]["target_slug"] == "product:item-1"
        assert data["relationship"]["composite_key"] == "Product|REFERENCES|Product"

    @pytest.mark.asyncio
    async def test_source_not_found(self, manage_relationship_fn, store):
        _seed_products(store, 1)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:nonexistent",
                "target_slug": "product:item-0",
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_target_not_found(self, manage_relationship_fn, store):
        _seed_products(store, 1)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:nonexistent",
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_unknown_relationship_type(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "UNKNOWN_REL",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_structural_rejected(self, manage_relationship_fn, store):
        _seed_products(store, 1)
        ds = EntityInstance(
            slug="data-source:my-ds",
            properties={"name": "My DS"},
        )
        store.upsert_entity(ds)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "CONTAINS",
                "source_slug": "data-source:my-ds",
                "target_slug": "product:item-0",
                "mode": "create",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_invalid_mode(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "delete",
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error


class TestManageRelationshipEdit:
    """Edit mode."""

    @pytest.mark.asyncio
    async def test_edit_success(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:item-0",
            target_entity_type="Product",
            target_slug="product:item-1",
            relationship_type="REFERENCES",
            properties={"context": "original"},
        )
        store.upsert_relationship(rel)

        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "edit",
                "properties": {"context": "updated"},
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "staged"
        assert data["mode"] == "edit"
        assert data["relationship"]["properties"]["context"] == "updated"

    @pytest.mark.asyncio
    async def test_edit_not_found(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "edit",
                "properties": {"context": "test"},
            }
        )
        is_error, _ = _parse_result(result)
        assert is_error

    @pytest.mark.asyncio
    async def test_edit_merges_properties(self, manage_relationship_fn, store):
        _seed_products(store, 2)
        rel = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:item-0",
            target_entity_type="Product",
            target_slug="product:item-1",
            relationship_type="REFERENCES",
            properties={"context": "original"},
        )
        store.upsert_relationship(rel)

        result = await manage_relationship_fn.handler(
            {
                "relationship_type": "REFERENCES",
                "source_slug": "product:item-0",
                "target_slug": "product:item-1",
                "mode": "edit",
                "properties": {"context": "merged"},
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["relationship"]["properties"]["context"] == "merged"


# ================================================================== #
# validate_and_commit tests
# ================================================================== #


class TestValidateAndCommit:
    """validate_and_commit tool tests."""

    @pytest.mark.asyncio
    async def test_commit_success(
        self, manage_entity_fn, validate_and_commit_fn, store
    ):
        e = EntityInstance(
            slug="product:commit-test",
            properties={
                "title": "Commit Test",
                "description": "Testing commit",
                "file_path": "/src/test.py",
            },
        )
        store.upsert_entity(e)

        # Stage an edit
        await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:commit-test",
                "properties": {"processed_by_agent": True},
                "mode": "edit",
            }
        )

        result = await validate_and_commit_fn.handler(
            {
                "job_files": ["/src/test.py"],
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "committed"

        # Verify committed to shared store
        entity = store.get_entity_by_slug("product:commit-test")
        assert entity is not None
        assert entity.properties["processed_by_agent"] is True

    @pytest.mark.asyncio
    async def test_validation_failure(self, validate_and_commit_fn, store, worker_id):
        # Stage a structural entity edit directly
        ds = EntityInstance(
            slug="data-source:test",
            properties={"name": "test"},
        )
        store.stage_entity(worker_id, ds)

        result = await validate_and_commit_fn.handler({})
        is_error, data = _parse_result(result)
        assert is_error
        assert data["status"] == "validation_failed"
        assert len(data["errors"]) > 0

    @pytest.mark.asyncio
    async def test_job_completeness_failure(
        self, manage_entity_fn, validate_and_commit_fn, store
    ):
        e = EntityInstance(
            slug="product:incomplete",
            properties={
                "title": "Incomplete",
                "description": "Not processed",
                "file_path": "/src/incomplete.py",
            },
        )
        store.upsert_entity(e)

        result = await validate_and_commit_fn.handler(
            {
                "job_files": ["/src/incomplete.py"],
            }
        )
        is_error, data = _parse_result(result)
        assert is_error
        assert any("not processed" in e.lower() for e in data["errors"])

    @pytest.mark.asyncio
    async def test_commit_without_job_files(
        self, manage_entity_fn, validate_and_commit_fn, store
    ):
        e = EntityInstance(
            slug="product:no-job",
            properties={
                "title": "No Job",
                "description": "Testing without job files",
            },
        )
        store.upsert_entity(e)

        await manage_entity_fn.handler(
            {
                "entity_type": "Product",
                "slug": "product:no-job",
                "properties": {"summary": "test"},
                "mode": "edit",
            }
        )

        result = await validate_and_commit_fn.handler({})
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["status"] == "committed"


# ================================================================== #
# Tool result format tests
# ================================================================== #


class TestToolResultFormat:
    """All tools return correct result format."""

    @pytest.mark.asyncio
    async def test_success_format(self, search_entities_fn, store):
        _seed_products(store, 1)
        result = await search_entities_fn.handler({"slugs": ["product:item-0"]})
        assert "content" in result
        assert isinstance(result["content"], list)
        assert result["content"][0]["type"] == "text"
        assert "is_error" not in result

    @pytest.mark.asyncio
    async def test_error_format(self, search_entities_fn):
        result = await search_entities_fn.handler({})
        assert "content" in result
        assert isinstance(result["content"], list)
        assert result["content"][0]["type"] == "text"
        assert result["is_error"] is True


# ================================================================== #
# Tool factory tests
# ================================================================== #


class TestToolFactory:
    """Tool factory and MCP server creation."""

    def test_creates_five_tools(self, tools):
        assert len(tools) == 5

    def test_tools_are_sdk_mcp_tools(self, tools):
        from claude_agent_sdk import SdkMcpTool

        for t in tools:
            assert isinstance(t, SdkMcpTool)

    def test_create_tool_server(self, worker_id, store, ontology):
        server = create_tool_server(worker_id, store, ontology)
        assert server is not None

    def test_tools_bound_to_worker(self, store, ontology):
        """Two workers get independent staging areas."""
        tools_w1 = create_extraction_tools("worker-1", store, ontology)
        tools_w2 = create_extraction_tools("worker-2", store, ontology)
        assert tools_w1 is not tools_w2

    @pytest.mark.asyncio
    async def test_worker_isolation(self, store, ontology):
        """Staged edits by one worker are not visible to another."""
        _seed_products(store, 1)
        tools_w1 = create_extraction_tools("worker-1", store, ontology)
        tools_w2 = create_extraction_tools("worker-2", store, ontology)

        manage_w1 = tools_w1[2]  # manage_entity
        search_w2 = tools_w2[0]  # search_entities

        # Worker 1 stages an edit
        await manage_w1.handler(
            {
                "entity_type": "Product",
                "slug": "product:item-0",
                "properties": {"summary": "worker-1-edit"},
                "mode": "edit",
            }
        )

        # Worker 2 should not see the staged edit
        result = await search_w2.handler({"slugs": ["product:item-0"]})
        _, data = _parse_result(result)
        assert "summary" not in data[0]["properties"]


class TestSearchRelationshipsMultiKey:
    """Slug-based lookups across multiple composite keys for same forward type."""

    @pytest.fixture()
    def multi_key_ontology(self) -> Ontology:
        """Ontology with two REFERENCES composite keys."""
        product_type = EntityTypeDefinition(
            type="Product",
            description="A product entity",
            tier=Tier.FILE_BASED,
            required_properties=["title"],
            optional_properties=[],
            property_definitions={"title": "Product title"},
        )
        component_type = EntityTypeDefinition(
            type="Component",
            description="A component entity",
            tier=Tier.FILE_BASED,
            required_properties=["title"],
            optional_properties=[],
            property_definitions={"title": "Component title"},
        )
        ref_pp = RelationshipTypeDefinition(
            source_entity_type="Product",
            target_entity_type="Product",
            forward_relationship=RelationshipDirection(
                type="REFERENCES", description="Product references product"
            ),
            category=RelationshipCategory.AGENT_MANAGED,
            required_parameters=[],
            optional_parameters=[],
        )
        ref_cp = RelationshipTypeDefinition(
            source_entity_type="Component",
            target_entity_type="Product",
            forward_relationship=RelationshipDirection(
                type="REFERENCES", description="Component references product"
            ),
            category=RelationshipCategory.AGENT_MANAGED,
            required_parameters=[],
            optional_parameters=[],
        )
        return Ontology(
            entity_types={
                "Product": product_type,
                "Component": component_type,
            },
            relationship_types={
                ref_pp.composite_key: ref_pp,
                ref_cp.composite_key: ref_cp,
            },
        )

    @pytest.fixture()
    def multi_key_store(self, engine, multi_key_ontology) -> OntologyStore:
        return OntologyStore(engine, multi_key_ontology)

    @pytest.fixture()
    def multi_key_tools(self, multi_key_store, multi_key_ontology):
        return create_extraction_tools("worker-mk", multi_key_store, multi_key_ontology)

    @pytest.fixture()
    def mk_search_rel(self, multi_key_tools):
        return multi_key_tools[1]

    @pytest.mark.asyncio
    async def test_slug_lookup_spans_multiple_composite_keys(
        self, mk_search_rel, multi_key_store
    ):
        """One slug lookup should find relationships across all matching keys."""
        p0 = EntityInstance(slug="product:p0", properties={"title": "P0"})
        p1 = EntityInstance(slug="product:p1", properties={"title": "P1"})
        c0 = EntityInstance(slug="component:c0", properties={"title": "C0"})
        multi_key_store.upsert_entity(p0)
        multi_key_store.upsert_entity(p1)
        multi_key_store.upsert_entity(c0)

        # Product -> Product relationship
        rel_pp = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:p0",
            target_entity_type="Product",
            target_slug="product:p1",
            relationship_type="REFERENCES",
            properties={},
        )
        # Component -> Product relationship involving p0 as target
        rel_cp = RelationshipInstance(
            source_entity_type="Component",
            source_slug="component:c0",
            target_entity_type="Product",
            target_slug="product:p0",
            relationship_type="REFERENCES",
            properties={},
        )
        multi_key_store.upsert_relationship(rel_pp)
        multi_key_store.upsert_relationship(rel_cp)

        # Search for slug "product:p0" — should find both relationships
        result = await mk_search_rel.handler(
            {
                "relationship_type": "REFERENCES",
                "slug": "product:p0",
                "show_all": True,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["total"] == 2
        slugs_found = {(r["source_slug"], r["target_slug"]) for r in data["results"]}
        assert ("product:p0", "product:p1") in slugs_found
        assert ("component:c0", "product:p0") in slugs_found

    @pytest.mark.asyncio
    async def test_two_slug_lookup_spans_multiple_composite_keys(
        self, mk_search_rel, multi_key_store
    ):
        """Two-slug lookup should find across all matching keys."""
        p0 = EntityInstance(slug="product:p0", properties={"title": "P0"})
        c0 = EntityInstance(slug="component:c0", properties={"title": "C0"})
        multi_key_store.upsert_entity(p0)
        multi_key_store.upsert_entity(c0)

        rel_cp = RelationshipInstance(
            source_entity_type="Component",
            source_slug="component:c0",
            target_entity_type="Product",
            target_slug="product:p0",
            relationship_type="REFERENCES",
            properties={},
        )
        multi_key_store.upsert_relationship(rel_cp)

        result = await mk_search_rel.handler(
            {
                "relationship_type": "REFERENCES",
                "slug": "component:c0",
                "second_slug": "product:p0",
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert len(data) == 1
        assert data[0]["composite_key"] == "Component|REFERENCES|Product"

    @pytest.mark.asyncio
    async def test_list_all_spans_multiple_composite_keys(
        self, mk_search_rel, multi_key_store
    ):
        """List All should aggregate instances across all matching keys."""
        p0 = EntityInstance(slug="product:p0", properties={"title": "P0"})
        p1 = EntityInstance(slug="product:p1", properties={"title": "P1"})
        c0 = EntityInstance(slug="component:c0", properties={"title": "C0"})
        multi_key_store.upsert_entity(p0)
        multi_key_store.upsert_entity(p1)
        multi_key_store.upsert_entity(c0)

        rel_pp = RelationshipInstance(
            source_entity_type="Product",
            source_slug="product:p0",
            target_entity_type="Product",
            target_slug="product:p1",
            relationship_type="REFERENCES",
            properties={},
        )
        rel_cp = RelationshipInstance(
            source_entity_type="Component",
            source_slug="component:c0",
            target_entity_type="Product",
            target_slug="product:p0",
            relationship_type="REFERENCES",
            properties={},
        )
        multi_key_store.upsert_relationship(rel_pp)
        multi_key_store.upsert_relationship(rel_cp)

        result = await mk_search_rel.handler(
            {
                "relationship_type": "REFERENCES",
                "list_instances": True,
                "show_all": True,
            }
        )
        is_error, data = _parse_result(result)
        assert not is_error
        assert data["total"] == 2


class TestSearchEntitiesReadOnlyHint:
    """Read-only tools have correct annotations."""

    def test_search_entities_readonly(self, tools):
        assert tools[0].annotations is not None
        assert tools[0].annotations.readOnlyHint is True

    def test_search_relationships_readonly(self, tools):
        assert tools[1].annotations is not None
        assert tools[1].annotations.readOnlyHint is True

    def test_manage_entity_not_readonly(self, tools):
        ann = tools[2].annotations
        assert ann is None or ann.readOnlyHint is not True

    def test_manage_relationship_not_readonly(self, tools):
        ann = tools[3].annotations
        assert ann is None or ann.readOnlyHint is not True
