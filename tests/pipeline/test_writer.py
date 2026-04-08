"""Tests for the JSONL streaming writer.

Covers:
- Single operation writes
- Batch writes
- Append mode (partial output valid)
- Concurrent write safety
- Output is valid JSONL
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from k_extract.domain.mutations import (
    CreateOperation,
    DefineOperation,
    DefineType,
    OpType,
    generate_node_id,
)
from k_extract.pipeline.writer import JsonlWriter


def _make_define() -> DefineOperation:
    return DefineOperation(
        op=OpType.DEFINE,
        type=DefineType.NODE,
        label="Person",
        description="A person entity",
        required_properties=["name"],
    )


def _make_create(slug: str = "person:alice") -> CreateOperation:
    node_id = generate_node_id("t", "person", slug)
    return CreateOperation(
        op=OpType.CREATE,
        type=DefineType.NODE,
        id=node_id,
        label="Person",
        set_properties={
            "slug": slug,
            "name": "Alice",
            "data_source_id": "ds-1",
            "source_path": "people/alice.md",
        },
    )


class TestJsonlWriter:
    """Tests for JsonlWriter."""

    @pytest.fixture()
    def output_path(self, tmp_path: Path) -> Path:
        return tmp_path / "output.jsonl"

    @pytest.mark.asyncio()
    async def test_write_single_operation(self, output_path: Path) -> None:
        """A single write produces exactly one JSON line."""
        writer = JsonlWriter(output_path)
        op = _make_define()
        await writer.write_operation(op)

        lines = output_path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["op"] == "DEFINE"
        assert parsed["label"] == "Person"

    @pytest.mark.asyncio()
    async def test_write_multiple_operations(self, output_path: Path) -> None:
        """Multiple writes produce multiple JSON lines."""
        writer = JsonlWriter(output_path)
        await writer.write_operation(_make_define())
        await writer.write_operation(_make_create())

        lines = output_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["op"] == "DEFINE"
        assert json.loads(lines[1])["op"] == "CREATE"

    @pytest.mark.asyncio()
    async def test_write_batch(self, output_path: Path) -> None:
        """Batch write produces one line per operation."""
        writer = JsonlWriter(output_path)
        ops = [_make_define(), _make_create()]
        await writer.write_operations(ops)

        lines = output_path.read_text().strip().split("\n")
        assert len(lines) == 2

    @pytest.mark.asyncio()
    async def test_append_mode(self, output_path: Path) -> None:
        """Subsequent writes append to the file (don't overwrite)."""
        writer = JsonlWriter(output_path)
        await writer.write_operation(_make_define())
        await writer.write_operation(_make_create())

        lines = output_path.read_text().strip().split("\n")
        assert len(lines) == 2

    @pytest.mark.asyncio()
    async def test_partial_output_is_valid_jsonl(self, output_path: Path) -> None:
        """Each line is independently parseable as JSON."""
        writer = JsonlWriter(output_path)
        await writer.write_operation(_make_define())
        await writer.write_operation(_make_create())

        for line in output_path.read_text().strip().split("\n"):
            parsed = json.loads(line)
            assert "op" in parsed

    @pytest.mark.asyncio()
    async def test_concurrent_writes(self, output_path: Path) -> None:
        """Concurrent writes don't corrupt the output."""
        writer = JsonlWriter(output_path)

        async def write_n(n: int) -> None:
            for i in range(n):
                await writer.write_operation(_make_create(f"person:p{i}"))

        await asyncio.gather(write_n(10), write_n(10))

        lines = output_path.read_text().strip().split("\n")
        assert len(lines) == 20
        for line in lines:
            parsed = json.loads(line)
            assert parsed["op"] == "CREATE"

    @pytest.mark.asyncio()
    async def test_none_fields_excluded(self, output_path: Path) -> None:
        """None fields (start_id, end_id on nodes) are excluded from output."""
        writer = JsonlWriter(output_path)
        await writer.write_operation(_make_create())

        line = output_path.read_text().strip()
        parsed = json.loads(line)
        assert "start_id" not in parsed
        assert "end_id" not in parsed

    @pytest.mark.asyncio()
    async def test_path_property(self, output_path: Path) -> None:
        """Writer exposes its output path."""
        writer = JsonlWriter(output_path)
        assert writer.path == output_path
