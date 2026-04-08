"""Entity instance model and validation.

Implements spec section 2: entity instances with slug validation
and property type constraints.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

# Slug format: {type}:{canonical-name}
# type: lowercase letters, numbers, hyphens
# canonical-name: lowercase letters, numbers, hyphens, underscores
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9_-]*$")

# Property value type
PropertyValue = str | bool | int | list[str]


class EntityInstance(BaseModel):
    """An entity instance in the ontology (spec section 2.1).

    Identified by a globally unique slug in the format {type}:{canonical-name}.
    """

    slug: str
    properties: dict[str, PropertyValue]

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Validate slug format: lowercase, kebab-case, {type}:{canonical-name}."""
        if not v:
            msg = "Slug must not be empty"
            raise ValueError(msg)
        if not SLUG_RE.match(v):
            msg = (
                f"Invalid slug format: {v!r}. "
                "Must be {{type}}:{{canonical-name}} where type is lowercase "
                "letters/numbers/hyphens and canonical-name is lowercase "
                "letters/numbers/hyphens/underscores."
            )
            raise ValueError(msg)
        return v

    @property
    def entity_type(self) -> str:
        """Extract the type prefix from the slug.

        For a slug like 'product:openshift-hyperfleet', returns 'product'.
        """
        return self.slug.split(":")[0]

    @property
    def canonical_name(self) -> str:
        """Extract the canonical name from the slug.

        For a slug like 'product:openshift-hyperfleet', returns 'openshift-hyperfleet'.
        """
        return self.slug.split(":", 1)[1]
