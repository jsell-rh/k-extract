"""Tests for the ontology store.

Covers CRUD, staging, virtual view, validation, and concurrency.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event

from k_extract.domain.entities import EntityInstance
from k_extract.domain.ontology import (
    EntityTypeDefinition,
    Ontology,
    RelationshipDirection,
    RelationshipTypeDefinition,
    Tier,
)
from k_extract.domain.relationships import RelationshipInstance
from k_extract.extraction.store import (
    DEFAULT_RESULT_LIMIT,
    OntologyStore,
)

# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture()
def engine():
    """In-memory SQLite engine with WAL mode."""
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
    rel_type = RelationshipTypeDefinition(
        source_entity_type="Product",
        target_entity_type="Product",
        forward_relationship=RelationshipDirection(
            type="REFERENCES", description="References another product"
        ),
        required_parameters=["context"],
        optional_parameters=[],
        property_definitions={"context": "Reference context"},
    )
    return Ontology(
        entity_types={
            "Product": product_type,
            "DataSource": source_type,
        },
        relationship_types={
            rel_type.composite_key: rel_type,
        },
    )


@pytest.fixture()
def store(engine, ontology) -> OntologyStore:
    """OntologyStore backed by an in-memory SQLite engine."""
    return OntologyStore(engine, ontology)


def _make_product(slug: str, **extra_props) -> EntityInstance:
    """Helper to create a Product entity instance."""
    props: dict = {"title": f"Title for {slug}", "description": f"Desc for {slug}"}
    props.update(extra_props)
    return EntityInstance(slug=slug, properties=props)


def _make_relationship(
    source_slug: str,
    target_slug: str,
    **extra_props,
) -> RelationshipInstance:
    """Helper to create a Product|REFERENCES|Product relationship."""
    props: dict = {"context": "test context"}
    props.update(extra_props)
    return RelationshipInstance(
        source_entity_type="Product",
        source_slug=source_slug,
        target_entity_type="Product",
        target_slug=target_slug,
        relationship_type="REFERENCES",
        properties=props,
    )


# ------------------------------------------------------------------ #
# Entity CRUD
# ------------------------------------------------------------------ #


class TestEntityCRUD:
    def test_upsert_and_get(self, store: OntologyStore) -> None:
        entity = _make_product("product:alpha")
        store.upsert_entity(entity)

        result = store.get_entity_by_slug("product:alpha")
        assert result is not None
        assert result.slug == "product:alpha"
        assert result.properties["title"] == "Title for product:alpha"

    def test_upsert_merges_properties(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:alpha", summary="v1"))
        store.upsert_entity(
            EntityInstance(
                slug="product:alpha",
                properties={"summary": "v2", "file_path": "/a.txt"},
            )
        )

        result = store.get_entity_by_slug("product:alpha")
        assert result is not None
        # Original properties preserved, new ones merged
        assert result.properties["title"] == "Title for product:alpha"
        assert result.properties["summary"] == "v2"
        assert result.properties["file_path"] == "/a.txt"

    def test_get_nonexistent_returns_none(self, store: OntologyStore) -> None:
        assert store.get_entity_by_slug("product:nope") is None

    def test_search_by_type(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a"))
        store.upsert_entity(_make_product("product:b"))

        results, total = store.search_entities_by_type("Product")
        assert total == 2
        assert len(results) == 2
        slugs = {e.slug for e in results}
        assert slugs == {"product:a", "product:b"}

    def test_search_by_type_empty(self, store: OntologyStore) -> None:
        results, total = store.search_entities_by_type("Product")
        assert total == 0
        assert results == []

    def test_search_by_slugs(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a"))
        store.upsert_entity(_make_product("product:b"))

        results = store.search_entities_by_slugs(
            ["product:a", "product:b", "product:missing"]
        )
        assert len(results) == 2

    def test_search_by_tag(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a", tags=["frontend"]))
        store.upsert_entity(_make_product("product:b", tags=["backend"]))
        store.upsert_entity(_make_product("product:c", tags=["frontend", "backend"]))

        results, total = store.search_entities_by_tag("Product", ["frontend"])
        assert total == 2
        slugs = {e.slug for e in results}
        assert slugs == {"product:a", "product:c"}

    def test_search_by_text(self, store: OntologyStore) -> None:
        store.upsert_entity(
            _make_product("product:auth-service", summary="handles login")
        )
        store.upsert_entity(
            _make_product("product:payment", summary="handles payments")
        )

        results, total = store.search_entities_by_text("Product", ["login"])
        assert total == 1
        assert results[0].slug == "product:auth-service"

    def test_search_by_text_and_logic(self, store: OntologyStore) -> None:
        store.upsert_entity(
            _make_product("product:alpha", summary="handles auth login")
        )
        store.upsert_entity(_make_product("product:beta", summary="handles auth"))

        results, total = store.search_entities_by_text("Product", ["auth", "login"])
        assert total == 1
        assert results[0].slug == "product:alpha"

    def test_search_by_file_path(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a", file_path="/src/a.py"))
        store.upsert_entity(_make_product("product:b", file_path="/src/b.py"))

        results = store.search_entities_by_file_path("/src/a.py")
        assert len(results) == 1
        assert results[0].slug == "product:a"

    def test_search_by_file_path_not_found(self, store: OntologyStore) -> None:
        results = store.search_entities_by_file_path("/nope")
        assert results == []


# ------------------------------------------------------------------ #
# Result capping
# ------------------------------------------------------------------ #


class TestResultCapping:
    def test_default_cap(self, store: OntologyStore) -> None:
        for i in range(15):
            store.upsert_entity(_make_product(f"product:item-{i}"))

        results, total = store.search_entities_by_type("Product")
        assert total == 15
        assert len(results) == DEFAULT_RESULT_LIMIT

    def test_custom_limit(self, store: OntologyStore) -> None:
        for i in range(5):
            store.upsert_entity(_make_product(f"product:item-{i}"))

        results, total = store.search_entities_by_type("Product", limit=3)
        assert total == 5
        assert len(results) == 3

    def test_relationship_cap(self, store: OntologyStore) -> None:
        for i in range(15):
            store.upsert_entity(_make_product(f"product:src-{i}"))
        store.upsert_entity(_make_product("product:target"))

        for i in range(15):
            store.upsert_relationship(
                _make_relationship(f"product:src-{i}", "product:target")
            )

        results, total = store.search_relationships_by_type(
            "Product|REFERENCES|Product"
        )
        assert total == 15
        assert len(results) == DEFAULT_RESULT_LIMIT


# ------------------------------------------------------------------ #
# Relationship CRUD
# ------------------------------------------------------------------ #


class TestRelationshipCRUD:
    def test_upsert_and_search(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a"))
        store.upsert_entity(_make_product("product:b"))
        rel = _make_relationship("product:a", "product:b")
        store.upsert_relationship(rel)

        results, total = store.search_relationships_by_type(
            "Product|REFERENCES|Product"
        )
        assert total == 1
        assert results[0].source_slug == "product:a"
        assert results[0].target_slug == "product:b"

    def test_upsert_merges_properties(self, store: OntologyStore) -> None:
        rel1 = _make_relationship("product:a", "product:b", context="v1")
        store.upsert_relationship(rel1)
        rel2 = _make_relationship("product:a", "product:b", context="v2", note="new")
        store.upsert_relationship(rel2)

        results, _ = store.search_relationships_by_type("Product|REFERENCES|Product")
        assert len(results) == 1
        assert results[0].properties["context"] == "v2"
        assert results[0].properties["note"] == "new"

    def test_search_by_slug(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a"))
        store.upsert_entity(_make_product("product:b"))
        store.upsert_entity(_make_product("product:c"))
        store.upsert_relationship(_make_relationship("product:a", "product:b"))
        store.upsert_relationship(_make_relationship("product:a", "product:c"))

        results, total = store.search_relationships_by_slug(
            "Product|REFERENCES|Product", "product:a"
        )
        assert total == 2

        results, total = store.search_relationships_by_slug(
            "Product|REFERENCES|Product", "product:b"
        )
        assert total == 1
        assert results[0].source_slug == "product:a"


# ------------------------------------------------------------------ #
# Staging
# ------------------------------------------------------------------ #


class TestStaging:
    def test_stage_entity(self, store: OntologyStore) -> None:
        entity = _make_product("product:alpha")
        store.stage_entity("worker-1", entity)

        # Not visible in shared store
        assert store.get_entity_by_slug("product:alpha") is None

        # Visible via virtual view
        result = store.get_entity_by_slug("product:alpha", worker_id="worker-1")
        assert result is not None
        assert result.slug == "product:alpha"

    def test_stage_entity_merges_properties(self, store: OntologyStore) -> None:
        store.stage_entity(
            "worker-1",
            EntityInstance(
                slug="product:alpha",
                properties={"title": "v1", "description": "d1"},
            ),
        )
        store.stage_entity(
            "worker-1",
            EntityInstance(
                slug="product:alpha",
                properties={"title": "v2", "summary": "new"},
            ),
        )

        result = store.get_entity_by_slug("product:alpha", worker_id="worker-1")
        assert result is not None
        assert result.properties["title"] == "v2"
        assert result.properties["description"] == "d1"
        assert result.properties["summary"] == "new"

    def test_stage_relationship(self, store: OntologyStore) -> None:
        rel = _make_relationship("product:a", "product:b")
        store.stage_relationship("worker-1", rel)

        # Not visible in shared store
        results, total = store.search_relationships_by_type(
            "Product|REFERENCES|Product"
        )
        assert total == 0

        # Visible via virtual view
        results, total = store.search_relationships_by_type(
            "Product|REFERENCES|Product", worker_id="worker-1"
        )
        assert total == 1

    def test_staging_isolation(self, store: OntologyStore) -> None:
        store.stage_entity("worker-1", _make_product("product:w1-only"))
        store.stage_entity("worker-2", _make_product("product:w2-only"))

        # Worker 1 can't see worker 2's staged entities
        assert store.get_entity_by_slug("product:w2-only", worker_id="worker-1") is None
        # Worker 2 can't see worker 1's staged entities
        assert store.get_entity_by_slug("product:w1-only", worker_id="worker-2") is None
        # Each worker sees their own
        assert (
            store.get_entity_by_slug("product:w1-only", worker_id="worker-1")
            is not None
        )

    def test_clear_staging(self, store: OntologyStore) -> None:
        store.stage_entity("worker-1", _make_product("product:alpha"))
        store.stage_relationship(
            "worker-1", _make_relationship("product:a", "product:b")
        )
        store.clear_staging("worker-1")

        assert store.get_entity_by_slug("product:alpha", worker_id="worker-1") is None
        results, total = store.search_relationships_by_type(
            "Product|REFERENCES|Product", worker_id="worker-1"
        )
        assert total == 0

    def test_clear_staging_does_not_affect_other_workers(
        self, store: OntologyStore
    ) -> None:
        store.stage_entity("worker-1", _make_product("product:w1"))
        store.stage_entity("worker-2", _make_product("product:w2"))
        store.clear_staging("worker-1")

        assert store.get_entity_by_slug("product:w2", worker_id="worker-2") is not None


# ------------------------------------------------------------------ #
# Virtual view
# ------------------------------------------------------------------ #


class TestVirtualView:
    def test_shared_entity_visible(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:shared"))

        result = store.get_entity_by_slug("product:shared", worker_id="worker-1")
        assert result is not None

    def test_staged_overrides_shared(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:alpha", summary="shared-version"))
        store.stage_entity(
            "worker-1",
            EntityInstance(
                slug="product:alpha", properties={"summary": "staged-version"}
            ),
        )

        result = store.get_entity_by_slug("product:alpha", worker_id="worker-1")
        assert result is not None
        # Staged property overrides shared
        assert result.properties["summary"] == "staged-version"
        # Shared properties preserved
        assert result.properties["title"] == "Title for product:alpha"

    def test_staged_only_entity(self, store: OntologyStore) -> None:
        store.stage_entity("worker-1", _make_product("product:new-only"))

        result = store.get_entity_by_slug("product:new-only", worker_id="worker-1")
        assert result is not None

        # Not visible to other workers
        assert (
            store.get_entity_by_slug("product:new-only", worker_id="worker-2") is None
        )

    def test_virtual_search_by_type(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:shared"))
        store.stage_entity("worker-1", _make_product("product:staged"))

        results, total = store.search_entities_by_type("Product", worker_id="worker-1")
        assert total == 2
        slugs = {e.slug for e in results}
        assert slugs == {"product:shared", "product:staged"}

    def test_virtual_search_by_tag(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:shared", tags=["frontend"]))
        store.stage_entity(
            "worker-1", _make_product("product:staged", tags=["frontend"])
        )

        results, total = store.search_entities_by_tag(
            "Product", ["frontend"], worker_id="worker-1"
        )
        assert total == 2

    def test_virtual_search_by_text(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:shared", summary="alpha beta"))
        store.stage_entity(
            "worker-1", _make_product("product:staged", summary="alpha gamma")
        )

        results, total = store.search_entities_by_text(
            "Product", ["alpha"], worker_id="worker-1"
        )
        assert total == 2

    def test_virtual_search_by_file_path(self, store: OntologyStore) -> None:
        store.stage_entity(
            "worker-1",
            _make_product("product:staged", file_path="/src/staged.py"),
        )

        results = store.search_entities_by_file_path(
            "/src/staged.py", worker_id="worker-1"
        )
        assert len(results) == 1
        assert results[0].slug == "product:staged"

    def test_virtual_relationship_view(self, store: OntologyStore) -> None:
        store.upsert_relationship(
            _make_relationship("product:a", "product:b", context="shared")
        )
        store.stage_relationship(
            "worker-1",
            _make_relationship("product:a", "product:b", context="staged"),
        )

        results, total = store.search_relationships_by_type(
            "Product|REFERENCES|Product", worker_id="worker-1"
        )
        assert total == 1
        # Staged replaces shared
        assert results[0].properties["context"] == "staged"


# ------------------------------------------------------------------ #
# Validate and commit
# ------------------------------------------------------------------ #


class TestValidateAndCommit:
    def test_successful_commit(self, store: OntologyStore) -> None:
        # Pre-populate shared store with a product
        store.upsert_entity(_make_product("product:existing"))

        # Worker stages an update
        store.stage_entity(
            "worker-1",
            EntityInstance(
                slug="product:existing",
                properties={"summary": "updated by worker"},
            ),
        )

        errors = store.validate_and_commit("worker-1")
        assert errors == []

        # Staged changes committed to shared store
        result = store.get_entity_by_slug("product:existing")
        assert result is not None
        assert result.properties["summary"] == "updated by worker"
        assert result.properties["title"] == "Title for product:existing"

        # Staging area cleared
        assert (
            store.get_entity_by_slug("product:existing", worker_id="worker-1") == result
        )

    def test_commit_new_entity(self, store: OntologyStore) -> None:
        store.stage_entity("worker-1", _make_product("product:brand-new"))

        errors = store.validate_and_commit("worker-1")
        assert errors == []

        result = store.get_entity_by_slug("product:brand-new")
        assert result is not None

    def test_commit_with_relationship(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:src"))
        store.upsert_entity(_make_product("product:tgt"))
        store.stage_relationship(
            "worker-1", _make_relationship("product:src", "product:tgt")
        )

        errors = store.validate_and_commit("worker-1")
        assert errors == []

        results, _ = store.search_relationships_by_type("Product|REFERENCES|Product")
        assert len(results) == 1

    def test_required_properties_validation(self, store: OntologyStore) -> None:
        # Stage entity missing required properties
        store.stage_entity(
            "worker-1",
            EntityInstance(slug="product:incomplete", properties={"title": "t"}),
        )

        errors = store.validate_and_commit("worker-1")
        assert any("Missing required property 'description'" in e for e in errors)

    def test_tag_validation(self, store: OntologyStore) -> None:
        store.stage_entity(
            "worker-1",
            _make_product("product:bad-tags", tags=["invalid-tag"]),
        )

        errors = store.validate_and_commit("worker-1")
        assert any("Invalid tag 'invalid-tag'" in e for e in errors)

    def test_referential_integrity_validation(self, store: OntologyStore) -> None:
        # Stage a relationship to a non-existent entity
        store.stage_relationship(
            "worker-1",
            _make_relationship("product:exists", "product:missing"),
        )
        store.upsert_entity(_make_product("product:exists"))

        errors = store.validate_and_commit("worker-1")
        assert any("Target entity not found" in e for e in errors)

    def test_entity_type_consistency_validation(self, store: OntologyStore) -> None:
        # Create a DataSource entity, then stage a relationship claiming
        # it's a Product
        store.upsert_entity(
            EntityInstance(
                slug="data-source:my-src",
                properties={"name": "my source"},
            )
        )
        store.upsert_entity(_make_product("product:tgt"))

        # Relationship says source is Product, but entity is DataSource
        store.stage_relationship(
            "worker-1",
            RelationshipInstance(
                source_entity_type="Product",
                source_slug="data-source:my-src",
                target_entity_type="Product",
                target_slug="product:tgt",
                relationship_type="REFERENCES",
                properties={"context": "test"},
            ),
        )

        errors = store.validate_and_commit("worker-1")
        assert any("is of type" in e and "expected" in e for e in errors)

    def test_structural_protection(self, store: OntologyStore) -> None:
        store.stage_entity(
            "worker-1",
            EntityInstance(
                slug="data-source:protected",
                properties={"name": "should fail"},
            ),
        )

        errors = store.validate_and_commit("worker-1")
        assert any("structural type" in e for e in errors)

    def test_job_completeness_validation(self, store: OntologyStore) -> None:
        store.upsert_entity(
            _make_product(
                "product:file-a",
                file_path="/a.py",
                processed_by_agent=True,
            )
        )

        errors = store.validate_and_commit("worker-1", job_files=["/a.py", "/b.py"])
        assert any("File not processed: '/b.py'" in e for e in errors)

    def test_job_completeness_success(self, store: OntologyStore) -> None:
        store.upsert_entity(
            _make_product(
                "product:file-a",
                file_path="/a.py",
                processed_by_agent=True,
            )
        )
        store.upsert_entity(
            _make_product(
                "product:file-b",
                file_path="/b.py",
                processed_by_agent=True,
            )
        )

        errors = store.validate_and_commit("worker-1", job_files=["/a.py", "/b.py"])
        assert errors == []

    def test_failed_validation_leaves_store_unchanged(
        self, store: OntologyStore
    ) -> None:
        store.upsert_entity(_make_product("product:original", summary="original"))

        # Stage a valid entity update AND a structural violation
        store.stage_entity(
            "worker-1",
            EntityInstance(slug="product:original", properties={"summary": "modified"}),
        )
        store.stage_entity(
            "worker-1",
            EntityInstance(slug="data-source:bad", properties={"name": "should fail"}),
        )

        errors = store.validate_and_commit("worker-1")
        assert len(errors) > 0

        # Shared store unchanged
        result = store.get_entity_by_slug("product:original")
        assert result is not None
        assert result.properties["summary"] == "original"

    def test_empty_staging_commits_successfully(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a"))
        errors = store.validate_and_commit("worker-1")
        assert errors == []

    def test_required_parameters_on_relationship(self, store: OntologyStore) -> None:
        store.upsert_entity(_make_product("product:a"))
        store.upsert_entity(_make_product("product:b"))
        store.stage_relationship(
            "worker-1",
            RelationshipInstance(
                source_entity_type="Product",
                source_slug="product:a",
                target_entity_type="Product",
                target_slug="product:b",
                relationship_type="REFERENCES",
                properties={},  # Missing required "context"
            ),
        )

        errors = store.validate_and_commit("worker-1")
        assert any("Missing required parameter 'context'" in e for e in errors)


# ------------------------------------------------------------------ #
# Concurrency
# ------------------------------------------------------------------ #


class TestConcurrency:
    def test_concurrent_commits_serialize(self) -> None:
        """Two workers committing to different entities should both succeed."""
        # Use a file-based SQLite DB for real cross-thread concurrency
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            eng = create_engine(f"sqlite:///{db_path}")

            @event.listens_for(eng, "connect")
            def _set_wal(dbapi_conn, _rec):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.close()

            product_type = EntityTypeDefinition(
                type="Product",
                description="A product entity",
                tier=Tier.FILE_BASED,
                required_properties=["title", "description"],
                optional_properties=["summary"],
                property_definitions={
                    "title": "t",
                    "description": "d",
                    "summary": "s",
                },
            )
            ont = Ontology(entity_types={"Product": product_type})
            st = OntologyStore(eng, ont)

            # Pre-populate
            st.upsert_entity(_make_product("product:shared-a"))
            st.upsert_entity(_make_product("product:shared-b"))

            # Worker 1 stages change to entity A
            st.stage_entity(
                "worker-1",
                EntityInstance(
                    slug="product:shared-a",
                    properties={"summary": "from-worker-1"},
                ),
            )
            # Worker 2 stages change to entity B
            st.stage_entity(
                "worker-2",
                EntityInstance(
                    slug="product:shared-b",
                    properties={"summary": "from-worker-2"},
                ),
            )

            results: dict[str, list[str]] = {}
            barrier = threading.Barrier(2)

            def commit_worker(worker_id: str) -> None:
                barrier.wait()
                errors = st.validate_and_commit(worker_id)
                results[worker_id] = errors

            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(commit_worker, "worker-1")
                f2 = pool.submit(commit_worker, "worker-2")
                f1.result(timeout=30)
                f2.result(timeout=30)

            assert results["worker-1"] == []
            assert results["worker-2"] == []

            # Both changes applied
            a = st.get_entity_by_slug("product:shared-a")
            b = st.get_entity_by_slug("product:shared-b")
            assert a is not None
            assert a.properties["summary"] == "from-worker-1"
            assert b is not None
            assert b.properties["summary"] == "from-worker-2"

            eng.dispose()

    def test_concurrent_reads_during_write(self) -> None:
        """Reads should not block during a commit."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            eng = create_engine(f"sqlite:///{db_path}")

            @event.listens_for(eng, "connect")
            def _set_wal(dbapi_conn, _rec):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.close()

            product_type = EntityTypeDefinition(
                type="Product",
                description="A product entity",
                tier=Tier.FILE_BASED,
                required_properties=["title", "description"],
                optional_properties=[],
                property_definitions={"title": "t", "description": "d"},
            )
            ont = Ontology(entity_types={"Product": product_type})
            st = OntologyStore(eng, ont)

            st.upsert_entity(_make_product("product:readable"))
            st.stage_entity(
                "writer",
                EntityInstance(
                    slug="product:readable",
                    properties={"title": "updated", "description": "updated"},
                ),
            )

            read_result: list[EntityInstance | None] = [None]

            def reader() -> None:
                read_result[0] = st.get_entity_by_slug("product:readable")

            # Run read concurrently with commit
            read_thread = threading.Thread(target=reader)
            read_thread.start()
            errors = st.validate_and_commit("writer")
            read_thread.join(timeout=10)

            assert errors == []
            assert read_result[0] is not None

            eng.dispose()
