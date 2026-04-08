"""Relationship instance model and validation.

Implements spec section 3: relationship instances with composite key
identity and referential integrity.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

from k_extract.domain.entities import PropertyValue

PASCAL_CASE_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9_-]*$")
UPPER_SNAKE_CASE_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


class RelationshipInstance(BaseModel):
    """A relationship instance in the ontology (spec section 3.1).

    Uniquely identified by composite key + (source_slug, target_slug).
    The composite key is "SourceType|REL_NAME|TargetType".
    """

    source_entity_type: str
    source_slug: str
    target_entity_type: str
    target_slug: str
    relationship_type: str
    properties: dict[str, PropertyValue]

    @field_validator("source_entity_type", "target_entity_type")
    @classmethod
    def validate_entity_type_pascal_case(cls, v: str) -> str:
        if not PASCAL_CASE_RE.match(v):
            msg = f"Entity type name must be PascalCase: {v!r}"
            raise ValueError(msg)
        return v

    @field_validator("relationship_type")
    @classmethod
    def validate_relationship_type(cls, v: str) -> str:
        if not UPPER_SNAKE_CASE_RE.match(v):
            msg = f"Relationship type name must be UPPER_SNAKE_CASE: {v!r}"
            raise ValueError(msg)
        return v

    @field_validator("source_slug", "target_slug")
    @classmethod
    def validate_slug_not_empty(cls, v: str) -> str:
        if not v:
            msg = "Slug must not be empty"
            raise ValueError(msg)
        if not SLUG_RE.match(v):
            msg = (
                f"Invalid slug format: {v!r}. "
                "Must be {{type}}:{{canonical-name}} format."
            )
            raise ValueError(msg)
        return v

    @property
    def composite_key(self) -> str:
        """Construct the composite key: SourceType|REL_NAME|TargetType."""
        return (
            f"{self.source_entity_type}"
            f"|{self.relationship_type}"
            f"|{self.target_entity_type}"
        )
