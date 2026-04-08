"""Ontology type definitions and container model.

Implements the dual-type architecture (entities + relationships) with
schema (type definitions) and data (instances).
"""

from __future__ import annotations

import enum
import re

from pydantic import BaseModel, field_validator, model_validator

from k_extract.domain.entities import EntityInstance
from k_extract.domain.relationships import RelationshipInstance

PASCAL_CASE_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
UPPER_SNAKE_CASE_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


class Tier(enum.StrEnum):
    """Entity type classification tier (spec section 1.5)."""

    STRUCTURAL = "structural"
    FILE_BASED = "file-based"
    SCENARIO_BASED = "scenario-based"


class RelationshipDirection(BaseModel):
    """Forward or reverse relationship descriptor."""

    type: str
    description: str = ""

    @field_validator("type")
    @classmethod
    def validate_upper_snake_case(cls, v: str) -> str:
        if not UPPER_SNAKE_CASE_RE.match(v):
            msg = f"Relationship type name must be UPPER_SNAKE_CASE: {v!r}"
            raise ValueError(msg)
        return v


class EntityTypeDefinition(BaseModel):
    """Schema for an entity type (spec section 1.2)."""

    type: str
    description: str
    tier: Tier
    required_properties: list[str]
    optional_properties: list[str]
    property_definitions: dict[str, str]
    property_defaults: dict[str, str | bool | int | list[str]] = {}
    tag_definitions: dict[str, str] = {}

    @field_validator("type")
    @classmethod
    def validate_pascal_case(cls, v: str) -> str:
        if not PASCAL_CASE_RE.match(v):
            msg = f"Entity type name must be PascalCase: {v!r}"
            raise ValueError(msg)
        return v

    @property
    def is_structural(self) -> bool:
        """Structural types are protected from agent modification."""
        return self.tier == Tier.STRUCTURAL


class RelationshipTypeDefinition(BaseModel):
    """Schema for a relationship type (spec section 1.3).

    Keyed by composite key: "SourceEntityType|RELATIONSHIP_NAME|TargetEntityType"
    """

    source_entity_type: str
    target_entity_type: str
    forward_relationship: RelationshipDirection
    reverse_relationship: RelationshipDirection | None = None
    required_parameters: list[str]
    optional_parameters: list[str]
    property_definitions: dict[str, str] = {}

    @field_validator("source_entity_type", "target_entity_type")
    @classmethod
    def validate_entity_type_pascal_case(cls, v: str) -> str:
        if not PASCAL_CASE_RE.match(v):
            msg = f"Entity type name must be PascalCase: {v!r}"
            raise ValueError(msg)
        return v

    @property
    def composite_key(self) -> str:
        """Construct the composite key: SourceType|REL_NAME|TargetType."""
        return (
            f"{self.source_entity_type}"
            f"|{self.forward_relationship.type}"
            f"|{self.target_entity_type}"
        )

    @staticmethod
    def parse_composite_key(key: str) -> tuple[str, str, str]:
        """Parse a composite key into (source_type, rel_name, target_type).

        Raises ValueError if the key does not have exactly 3 parts.
        """
        parts = key.split("|")
        if len(parts) != 3:
            msg = f"Composite key must have exactly 3 parts separated by '|': {key!r}"
            raise ValueError(msg)
        return parts[0], parts[1], parts[2]


class Ontology(BaseModel):
    """Container for entity/relationship type definitions and instances.

    Provides lookup methods and validation for the ontology as a whole.
    """

    entity_types: dict[str, EntityTypeDefinition] = {}
    relationship_types: dict[str, RelationshipTypeDefinition] = {}
    entities: dict[str, EntityInstance] = {}
    relationships: list[RelationshipInstance] = []

    @model_validator(mode="after")
    def validate_relationship_type_keys(self) -> Ontology:
        """Ensure relationship_types dict keys match the composite keys."""
        for key, rel_type in self.relationship_types.items():
            if key != rel_type.composite_key:
                msg = (
                    f"Relationship type dict key {key!r} does not match "
                    f"composite key {rel_type.composite_key!r}"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_entity_type_keys(self) -> Ontology:
        """Ensure entity_types dict keys match the type field."""
        for key, entity_type in self.entity_types.items():
            if key != entity_type.type:
                msg = (
                    f"Entity type dict key {key!r} does not match "
                    f"type field {entity_type.type!r}"
                )
                raise ValueError(msg)
        return self

    def get_entity_type(self, type_name: str) -> EntityTypeDefinition | None:
        """Look up an entity type definition by type name."""
        return self.entity_types.get(type_name)

    def get_relationship_type(
        self, composite_key: str
    ) -> RelationshipTypeDefinition | None:
        """Look up a relationship type definition by composite key."""
        return self.relationship_types.get(composite_key)

    def get_entity_by_slug(self, slug: str) -> EntityInstance | None:
        """Look up an entity instance by slug."""
        return self.entities.get(slug)

    def get_entities_by_type(self, type_name: str) -> list[EntityInstance]:
        """Get all entity instances of a given type.

        Args:
            type_name: PascalCase entity type name.
        """
        prefix = _pascal_to_kebab(type_name)
        return [e for e in self.entities.values() if e.entity_type == prefix]

    def find_entity_type_for_slug(self, slug: str) -> EntityTypeDefinition | None:
        """Find the entity type definition that matches a slug's type prefix."""
        slug_prefix = slug.split(":")[0]
        for entity_type_def in self.entity_types.values():
            if _pascal_to_kebab(entity_type_def.type) == slug_prefix:
                return entity_type_def
        return None

    def get_relationships_by_composite_key(
        self, composite_key: str
    ) -> list[RelationshipInstance]:
        """Get all relationship instances for a given composite key."""
        return [r for r in self.relationships if r.composite_key == composite_key]

    def validate_entity(self, entity: EntityInstance) -> list[str]:
        """Validate an entity instance against the ontology.

        Returns a list of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Find matching entity type definition via slug prefix
        entity_type_def = self.find_entity_type_for_slug(entity.slug)
        if entity_type_def is None:
            errors.append(
                f"Unknown entity type for slug prefix: {entity.entity_type!r}"
            )
            return errors

        # Check required properties
        for prop in entity_type_def.required_properties:
            if prop not in entity.properties:
                errors.append(
                    f"Missing required property {prop!r} on entity {entity.slug!r}"
                )

        # Check property types
        all_known = set(entity_type_def.required_properties) | set(
            entity_type_def.optional_properties
        )
        for prop_name, prop_value in entity.properties.items():
            if prop_name not in all_known:
                errors.append(
                    f"Unknown property {prop_name!r} on entity {entity.slug!r}"
                )
            if not _is_valid_property_value(prop_value):
                errors.append(
                    f"Invalid property type for {prop_name!r} on entity "
                    f"{entity.slug!r}: must be str, bool, int, or list[str]"
                )

        # Check tag validation
        if "tags" in entity.properties and entity_type_def.tag_definitions:
            tags = entity.properties["tags"]
            if isinstance(tags, list):
                allowed_tags = set(entity_type_def.tag_definitions.keys())
                for tag in tags:
                    if isinstance(tag, str) and tag not in allowed_tags:
                        errors.append(
                            f"Invalid tag {tag!r} on entity {entity.slug!r}. "
                            f"Allowed tags: {sorted(allowed_tags)}"
                        )

        return errors

    def validate_relationship(self, relationship: RelationshipInstance) -> list[str]:
        """Validate a relationship instance against the ontology.

        Returns a list of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Check relationship type exists
        rel_type_def = self.get_relationship_type(relationship.composite_key)
        if rel_type_def is None:
            errors.append(f"Unknown relationship type: {relationship.composite_key!r}")
            return errors

        # Check entity type consistency
        if relationship.source_entity_type != rel_type_def.source_entity_type:
            errors.append(
                f"Source entity type {relationship.source_entity_type!r} does not "
                f"match definition {rel_type_def.source_entity_type!r}"
            )
        if relationship.target_entity_type != rel_type_def.target_entity_type:
            errors.append(
                f"Target entity type {relationship.target_entity_type!r} does not "
                f"match definition {rel_type_def.target_entity_type!r}"
            )

        # Check referential integrity — source and target entities must exist
        if self.get_entity_by_slug(relationship.source_slug) is None:
            errors.append(f"Source entity not found: {relationship.source_slug!r}")
        if self.get_entity_by_slug(relationship.target_slug) is None:
            errors.append(f"Target entity not found: {relationship.target_slug!r}")

        # Check required parameters
        for param in rel_type_def.required_parameters:
            if param not in relationship.properties:
                errors.append(
                    f"Missing required parameter {param!r} on relationship "
                    f"{relationship.composite_key!r}"
                )

        return errors

    def is_structural_entity_type(self, type_name: str) -> bool:
        """Check whether an entity type is structural (protected from agent edits)."""
        entity_type_def = self.get_entity_type(type_name)
        if entity_type_def is None:
            return False
        return entity_type_def.is_structural


def _is_valid_property_value(value: object) -> bool:
    """Check if a value is a valid property type (str, bool, int, list[str])."""
    if isinstance(value, str | bool | int):
        return True
    if isinstance(value, list):
        return all(isinstance(item, str) for item in value)
    return False


def _pascal_to_kebab(name: str) -> str:
    """Convert PascalCase to kebab-case for slug type prefix matching.

    Example: 'DataSource' -> 'data-source', 'Product' -> 'product'
    """
    # Insert hyphen before uppercase letters that follow a lowercase letter or digit
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    return s.lower()
