"""Operation models and ID generation for kartograph-compatible JSONL mutations.

Implements specs/process/output-format.md:
- Deterministic ID generation matching kartograph's EntityIdGenerator
- DEFINE and CREATE operation models with field validation
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, model_validator

# ID format: {type_lowercase}:{16_hex_chars}
ID_RE = re.compile(r"^[0-9a-z_]+:[0-9a-f]{16}$")


class OpType(StrEnum):
    """Mutation operation types."""

    DEFINE = "DEFINE"
    CREATE = "CREATE"


class DefineType(StrEnum):
    """DEFINE target types."""

    NODE = "node"
    EDGE = "edge"


def generate_node_id(tenant_id: str, type_lower: str, slug: str) -> str:
    """Generate a deterministic node ID matching kartograph's algorithm.

    Format: f"{type_lower}:{sha256(tenant_id:type_lower:slug)[:16]}"

    Args:
        tenant_id: The tenant identifier.
        type_lower: Lowercase entity type name.
        slug: The entity slug.

    Returns:
        A node ID in the format "{type_lower}:{16_hex_chars}".
    """
    hash_input = f"{tenant_id}:{type_lower}:{slug}"
    hex_digest = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
    return f"{type_lower}:{hex_digest}"


def generate_edge_id(tenant_id: str, start_id: str, label: str, end_id: str) -> str:
    """Generate a deterministic edge ID matching kartograph's algorithm.

    Format: f"{label_lower}:{sha256(tenant:start_id:label:end_id)[:16]}"

    Args:
        tenant_id: The tenant identifier.
        start_id: The source node ID.
        label: The relationship label.
        end_id: The target node ID.

    Returns:
        An edge ID in the format "{label_lower}:{16_hex_chars}".
    """
    label_lower = label.lower()
    hash_input = f"{tenant_id}:{start_id}:{label}:{end_id}"
    hex_digest = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
    return f"{label_lower}:{hex_digest}"


class DefineOperation(BaseModel):
    """A DEFINE operation — type declaration (node or edge).

    Must appear before any CREATE operations in the JSONL output.
    """

    op: Literal[OpType.DEFINE]
    type: DefineType
    label: str
    description: str
    required_properties: list[str]


class CreateOperation(BaseModel):
    """A CREATE operation — entity/relationship discovery.

    Nodes must include slug in set_properties.
    Edges must include start_id and end_id.
    All must include data_source_id and source_path in set_properties.
    """

    op: Literal[OpType.CREATE]
    type: DefineType
    id: str
    label: str
    set_properties: dict[str, Any]
    start_id: str | None = None
    end_id: str | None = None

    @model_validator(mode="after")
    def validate_create_fields(self) -> CreateOperation:
        """Validate required fields per CREATE type."""
        errors: list[str] = []

        # Validate ID format
        if not ID_RE.match(self.id):
            errors.append(
                f"Invalid ID format: {self.id!r}. "
                "Must match ^[0-9a-z_]+:[0-9a-f]{16}$"
            )

        # System properties required on all CREATEs
        if "data_source_id" not in self.set_properties:
            errors.append("set_properties must include 'data_source_id'")
        if "source_path" not in self.set_properties:
            errors.append("set_properties must include 'source_path'")

        if self.type == DefineType.NODE:
            # Nodes must include slug
            if "slug" not in self.set_properties:
                errors.append("set_properties must include 'slug' for node CREATE")
            # Nodes must not have start_id/end_id
            if self.start_id is not None:
                errors.append("Node CREATE must not have start_id")
            if self.end_id is not None:
                errors.append("Node CREATE must not have end_id")
        elif self.type == DefineType.EDGE:
            # Edges must have start_id and end_id
            if self.start_id is None:
                errors.append("Edge CREATE must have start_id")
            if self.end_id is None:
                errors.append("Edge CREATE must have end_id")
            # Validate start_id/end_id format
            if self.start_id is not None and not ID_RE.match(self.start_id):
                errors.append(
                    f"Invalid start_id format: {self.start_id!r}. "
                    "Must match ^[0-9a-z_]+:[0-9a-f]{16}$"
                )
            if self.end_id is not None and not ID_RE.match(self.end_id):
                errors.append(
                    f"Invalid end_id format: {self.end_id!r}. "
                    "Must match ^[0-9a-z_]+:[0-9a-f]{16}$"
                )

        if errors:
            msg = "; ".join(errors)
            raise ValueError(msg)

        return self


# Union type for all operations k-extract emits
MutationOperation = DefineOperation | CreateOperation
