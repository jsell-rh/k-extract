"""SQLAlchemy models for ontology storage.

Implements the four-table schema: shared entities, shared relationships,
staged entities (per-worker), and staged relationships (per-worker).
"""

from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OntologyBase(DeclarativeBase):
    """SQLAlchemy declarative base for ontology storage tables."""


class EntityInstanceRow(OntologyBase):
    """Shared entity instance storage.

    Primary key: slug.
    """

    __tablename__ = "entity_instances"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, nullable=False)


class RelationshipInstanceRow(OntologyBase):
    """Shared relationship instance storage.

    Primary key: (composite_key, source_slug, target_slug).
    """

    __tablename__ = "relationship_instances"

    composite_key: Mapped[str] = mapped_column(String, primary_key=True)
    source_slug: Mapped[str] = mapped_column(String, primary_key=True)
    target_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source_entity_type: Mapped[str] = mapped_column(String, nullable=False)
    target_entity_type: Mapped[str] = mapped_column(String, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, nullable=False)


class StagedEntityRow(OntologyBase):
    """Per-worker staged entity instance.

    Primary key: (worker_id, slug).
    """

    __tablename__ = "staged_entities"

    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    slug: Mapped[str] = mapped_column(String, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, nullable=False)


class StagedRelationshipRow(OntologyBase):
    """Per-worker staged relationship instance.

    Primary key: (worker_id, composite_key, source_slug, target_slug).
    """

    __tablename__ = "staged_relationships"

    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    composite_key: Mapped[str] = mapped_column(String, primary_key=True)
    source_slug: Mapped[str] = mapped_column(String, primary_key=True)
    target_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source_entity_type: Mapped[str] = mapped_column(String, nullable=False)
    target_entity_type: Mapped[str] = mapped_column(String, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, nullable=False)
