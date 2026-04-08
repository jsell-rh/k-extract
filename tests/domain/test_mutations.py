"""Tests for ID generation and operation models.

Covers:
- Node and edge ID generation (determinism, format)
- DEFINE operation model validation
- CREATE operation model validation (system properties, node/edge constraints)
"""

from __future__ import annotations

import hashlib

import pytest

from k_extract.domain.mutations import (
    CreateOperation,
    DefineOperation,
    DefineType,
    OpType,
    generate_edge_id,
    generate_node_id,
)


class TestNodeIdGeneration:
    """Tests for generate_node_id."""

    def test_produces_correct_format(self) -> None:
        """Node ID matches ^[0-9a-z_]+:[0-9a-f]{16}$."""
        node_id = generate_node_id("tenant-1", "person", "person:alice-smith")
        prefix, hex_part = node_id.split(":")
        assert prefix == "person"
        assert len(hex_part) == 16
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_deterministic(self) -> None:
        """Same inputs always produce the same ID."""
        id1 = generate_node_id("t", "product", "product:foo")
        id2 = generate_node_id("t", "product", "product:foo")
        assert id1 == id2

    def test_different_inputs_produce_different_ids(self) -> None:
        """Different slugs produce different IDs."""
        id1 = generate_node_id("t", "person", "person:alice")
        id2 = generate_node_id("t", "person", "person:bob")
        assert id1 != id2

    def test_different_tenants_produce_different_ids(self) -> None:
        """Different tenant IDs produce different IDs for the same slug."""
        id1 = generate_node_id("tenant-a", "person", "person:alice")
        id2 = generate_node_id("tenant-b", "person", "person:alice")
        assert id1 != id2

    def test_matches_manual_sha256(self) -> None:
        """Verify the hash matches manual SHA256 computation."""
        tenant = "my-tenant"
        type_lower = "repo"
        slug = "repo:my-repo"
        expected_hex = hashlib.sha256(
            f"{tenant}:{type_lower}:{slug}".encode()
        ).hexdigest()[:16]
        expected_id = f"{type_lower}:{expected_hex}"
        assert generate_node_id(tenant, type_lower, slug) == expected_id

    def test_type_with_underscore(self) -> None:
        """Type names with underscores are valid in IDs."""
        node_id = generate_node_id("t", "test_case", "test_case:auth-flow")
        prefix, hex_part = node_id.split(":")
        assert prefix == "test_case"
        assert len(hex_part) == 16


class TestEdgeIdGeneration:
    """Tests for generate_edge_id."""

    def test_produces_correct_format(self) -> None:
        """Edge ID matches ^[0-9a-z_]+:[0-9a-f]{16}$."""
        edge_id = generate_edge_id(
            "tenant-1",
            "person:1a2b3c4d5e6f7890",
            "KNOWS",
            "person:abcdef0123456789",
        )
        prefix, hex_part = edge_id.split(":")
        assert prefix == "knows"
        assert len(hex_part) == 16

    def test_deterministic(self) -> None:
        """Same inputs always produce the same edge ID."""
        id1 = generate_edge_id("t", "a:1234567890abcdef", "REL", "b:abcdef1234567890")
        id2 = generate_edge_id("t", "a:1234567890abcdef", "REL", "b:abcdef1234567890")
        assert id1 == id2

    def test_label_lowercased_in_prefix(self) -> None:
        """The label prefix is lowercased in the output."""
        edge_id = generate_edge_id(
            "t", "a:1234567890abcdef", "CONTAINS", "b:abcdef1234567890"
        )
        assert edge_id.startswith("contains:")

    def test_different_endpoints_produce_different_ids(self) -> None:
        """Swapping start/end produces a different ID."""
        id1 = generate_edge_id("t", "a:1234567890abcdef", "REL", "b:abcdef1234567890")
        id2 = generate_edge_id("t", "b:abcdef1234567890", "REL", "a:1234567890abcdef")
        assert id1 != id2

    def test_matches_manual_sha256(self) -> None:
        """Verify the hash matches manual SHA256 computation."""
        tenant = "my-tenant"
        start = "person:1a2b3c4d5e6f7890"
        label = "KNOWS"
        end = "person:abcdef0123456789"
        expected_hex = hashlib.sha256(
            f"{tenant}:{start}:{label}:{end}".encode()
        ).hexdigest()[:16]
        expected_id = f"knows:{expected_hex}"
        assert generate_edge_id(tenant, start, label, end) == expected_id


class TestDefineOperation:
    """Tests for DefineOperation model."""

    def test_valid_node_define(self) -> None:
        """A valid node DEFINE is accepted."""
        op = DefineOperation(
            op=OpType.DEFINE,
            type=DefineType.NODE,
            label="Person",
            description="A person entity",
            required_properties=["name"],
        )
        assert op.op == OpType.DEFINE
        assert op.type == DefineType.NODE
        assert op.label == "Person"

    def test_valid_edge_define(self) -> None:
        """A valid edge DEFINE is accepted."""
        op = DefineOperation(
            op=OpType.DEFINE,
            type=DefineType.EDGE,
            label="KNOWS",
            description="A relationship between people",
            required_properties=["since"],
        )
        assert op.type == DefineType.EDGE

    def test_empty_required_properties(self) -> None:
        """DEFINE with no required properties is valid."""
        op = DefineOperation(
            op=OpType.DEFINE,
            type=DefineType.NODE,
            label="Tag",
            description="A tag",
            required_properties=[],
        )
        assert op.required_properties == []


class TestCreateOperation:
    """Tests for CreateOperation model."""

    def test_valid_node_create(self) -> None:
        """A valid node CREATE is accepted."""
        node_id = generate_node_id("t", "person", "person:alice")
        op = CreateOperation(
            op=OpType.CREATE,
            type=DefineType.NODE,
            id=node_id,
            label="Person",
            set_properties={
                "slug": "person:alice",
                "name": "Alice",
                "data_source_id": "ds-1",
                "source_path": "people/alice.md",
            },
        )
        assert op.op == OpType.CREATE
        assert op.type == DefineType.NODE

    def test_valid_edge_create(self) -> None:
        """A valid edge CREATE is accepted."""
        start = generate_node_id("t", "person", "person:alice")
        end = generate_node_id("t", "person", "person:bob")
        edge_id = generate_edge_id("t", start, "KNOWS", end)
        op = CreateOperation(
            op=OpType.CREATE,
            type=DefineType.EDGE,
            id=edge_id,
            label="KNOWS",
            start_id=start,
            end_id=end,
            set_properties={
                "since": "2020",
                "data_source_id": "ds-1",
                "source_path": "people/alice.md",
            },
        )
        assert op.start_id == start
        assert op.end_id == end

    def test_node_create_missing_slug(self) -> None:
        """Node CREATE without slug in set_properties is rejected."""
        node_id = generate_node_id("t", "person", "person:alice")
        with pytest.raises(ValueError, match="slug"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.NODE,
                id=node_id,
                label="Person",
                set_properties={
                    "name": "Alice",
                    "data_source_id": "ds-1",
                    "source_path": "people/alice.md",
                },
            )

    def test_create_missing_data_source_id(self) -> None:
        """CREATE without data_source_id is rejected."""
        node_id = generate_node_id("t", "person", "person:alice")
        with pytest.raises(ValueError, match="data_source_id"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.NODE,
                id=node_id,
                label="Person",
                set_properties={
                    "slug": "person:alice",
                    "name": "Alice",
                    "source_path": "people/alice.md",
                },
            )

    def test_create_missing_source_path(self) -> None:
        """CREATE without source_path is rejected."""
        node_id = generate_node_id("t", "person", "person:alice")
        with pytest.raises(ValueError, match="source_path"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.NODE,
                id=node_id,
                label="Person",
                set_properties={
                    "slug": "person:alice",
                    "name": "Alice",
                    "data_source_id": "ds-1",
                },
            )

    def test_edge_create_missing_start_id(self) -> None:
        """Edge CREATE without start_id is rejected."""
        with pytest.raises(ValueError, match="start_id"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.EDGE,
                id="knows:1234567890abcdef",
                label="KNOWS",
                end_id="person:abcdef1234567890",
                set_properties={
                    "data_source_id": "ds-1",
                    "source_path": "x.md",
                },
            )

    def test_edge_create_missing_end_id(self) -> None:
        """Edge CREATE without end_id is rejected."""
        with pytest.raises(ValueError, match="end_id"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.EDGE,
                id="knows:1234567890abcdef",
                label="KNOWS",
                start_id="person:1234567890abcdef",
                set_properties={
                    "data_source_id": "ds-1",
                    "source_path": "x.md",
                },
            )

    def test_node_create_with_start_id_rejected(self) -> None:
        """Node CREATE with start_id is rejected."""
        node_id = generate_node_id("t", "person", "person:alice")
        with pytest.raises(ValueError, match="start_id"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.NODE,
                id=node_id,
                label="Person",
                start_id="person:1234567890abcdef",
                set_properties={
                    "slug": "person:alice",
                    "name": "Alice",
                    "data_source_id": "ds-1",
                    "source_path": "people/alice.md",
                },
            )

    def test_invalid_id_format(self) -> None:
        """CREATE with invalid ID format is rejected."""
        with pytest.raises(ValueError, match="Invalid ID format"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.NODE,
                id="bad-id",
                label="Person",
                set_properties={
                    "slug": "person:alice",
                    "data_source_id": "ds-1",
                    "source_path": "x.md",
                },
            )

    def test_invalid_start_id_format(self) -> None:
        """Edge CREATE with invalid start_id format is rejected."""
        with pytest.raises(ValueError, match="start_id format"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.EDGE,
                id="knows:1234567890abcdef",
                label="KNOWS",
                start_id="not-valid",
                end_id="person:abcdef1234567890",
                set_properties={
                    "data_source_id": "ds-1",
                    "source_path": "x.md",
                },
            )

    def test_invalid_end_id_format(self) -> None:
        """Edge CREATE with invalid end_id format is rejected."""
        with pytest.raises(ValueError, match="end_id format"):
            CreateOperation(
                op=OpType.CREATE,
                type=DefineType.EDGE,
                id="knows:1234567890abcdef",
                label="KNOWS",
                start_id="person:1234567890abcdef",
                end_id="not-valid",
                set_properties={
                    "data_source_id": "ds-1",
                    "source_path": "x.md",
                },
            )
