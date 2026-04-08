"""Pydantic models for the extraction.yaml config file.

Implements the config schema from specs/process/config-schema.md.
The config file bridges `k-extract init` and `k-extract run`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator, model_validator

PASCAL_CASE_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
UPPER_SNAKE_CASE_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


class DataSourceConfig(BaseModel):
    """A data source entry: name + path."""

    name: str
    path: str

    @field_validator("path")
    @classmethod
    def validate_path_non_empty(cls, v: str) -> str:
        """Data source paths must be non-empty strings."""
        if not v:
            msg = "Data source path must not be empty"
            raise ValueError(msg)
        return v


class EntityTypeConfig(BaseModel):
    """Entity type definition within the config ontology."""

    label: str
    description: str
    required_properties: list[str]
    optional_properties: list[str]
    tag_definitions: dict[str, str] = {}

    @field_validator("label")
    @classmethod
    def validate_label_pascal_case(cls, v: str) -> str:
        """Entity type labels must be PascalCase."""
        if not PASCAL_CASE_RE.match(v):
            msg = f"Entity type label must be PascalCase: {v!r}"
            raise ValueError(msg)
        return v


class RelationshipTypeConfig(BaseModel):
    """Relationship type definition within the config ontology."""

    label: str
    description: str
    source_entity_type: str
    target_entity_type: str
    required_properties: list[str]
    optional_properties: list[str]

    @field_validator("label")
    @classmethod
    def validate_label_upper_snake_case(cls, v: str) -> str:
        """Relationship type labels must be UPPER_SNAKE_CASE."""
        if not UPPER_SNAKE_CASE_RE.match(v):
            msg = f"Relationship type label must be UPPER_SNAKE_CASE: {v!r}"
            raise ValueError(msg)
        return v


class OntologyConfig(BaseModel):
    """Ontology section: entity types and relationship types."""

    entity_types: list[EntityTypeConfig]
    relationship_types: list[RelationshipTypeConfig]

    @model_validator(mode="after")
    def validate_relationship_entity_references(self) -> OntologyConfig:
        """Relationship source/target must reference defined entity types."""
        entity_labels = {et.label for et in self.entity_types}
        for rt in self.relationship_types:
            if rt.source_entity_type not in entity_labels:
                msg = (
                    f"Relationship type {rt.label!r} references undefined "
                    f"source entity type {rt.source_entity_type!r}"
                )
                raise ValueError(msg)
            if rt.target_entity_type not in entity_labels:
                msg = (
                    f"Relationship type {rt.label!r} references undefined "
                    f"target entity type {rt.target_entity_type!r}"
                )
                raise ValueError(msg)
        return self


class PromptsConfig(BaseModel):
    """Prompts section: system prompt and job description template."""

    system_prompt: str
    job_description_template: str


class OutputConfig(BaseModel):
    """Output section: file path and optional database path."""

    file: str
    database: str = "extraction.db"

    @field_validator("file")
    @classmethod
    def validate_file_non_empty(cls, v: str) -> str:
        """Output file path must be non-empty."""
        if not v:
            msg = "Output file path must not be empty"
            raise ValueError(msg)
        return v


class ExtractionConfig(BaseModel):
    """Top-level config model for extraction.yaml."""

    problem_statement: str
    data_sources: list[DataSourceConfig]
    ontology: OntologyConfig
    prompts: PromptsConfig
    output: OutputConfig
