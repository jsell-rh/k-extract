"""Agent tools for knowledge graph extraction.

Implements the five agent tools as Python functions using the Claude Agent SDK's
``@tool`` decorator. Tools are registered via ``create_sdk_mcp_server`` and run
in-process. Each tool is bound to a specific worker's staging area at
instantiation time via the factory function.
"""

from __future__ import annotations

import copy
import json
from typing import Annotated, Any, NotRequired, TypedDict

from claude_agent_sdk import ToolAnnotations, create_sdk_mcp_server, tool

from k_extract.domain.entities import EntityInstance
from k_extract.domain.ontology import (
    Ontology,
    _is_valid_property_value,
    _pascal_to_kebab,
)
from k_extract.domain.relationships import RelationshipInstance
from k_extract.extraction.store import OntologyStore


def _ok(text: str) -> dict[str, Any]:
    """Build a success tool result."""
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    """Build an error tool result."""
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _entity_to_dict(
    entity: EntityInstance, ontology: Ontology | None = None
) -> dict[str, Any]:
    """Serialize an entity instance for tool output.

    When ontology is provided, entity_type is resolved to PascalCase.
    Otherwise uses the raw kebab-case entity_type from the slug prefix.
    """
    entity_type: str = entity.entity_type
    if ontology is not None:
        type_def = ontology.find_entity_type_for_slug(entity.slug)
        if type_def is not None:
            entity_type = type_def.type
    return {
        "slug": entity.slug,
        "entity_type": entity_type,
        "properties": dict(entity.properties),
    }


def _relationship_to_dict(rel: RelationshipInstance) -> dict[str, Any]:
    """Serialize a relationship instance for tool output."""
    return {
        "composite_key": rel.composite_key,
        "source_entity_type": rel.source_entity_type,
        "source_slug": rel.source_slug,
        "target_entity_type": rel.target_entity_type,
        "target_slug": rel.target_slug,
        "relationship_type": rel.relationship_type,
        "properties": dict(rel.properties),
    }


# ---------------------------------------------------------------------- #
# Input schemas (TypedDicts)
# ---------------------------------------------------------------------- #


class SearchEntitiesInput(TypedDict):
    entity_type: NotRequired[
        Annotated[str, "PascalCase entity type name (for type def, filter, search)"]
    ]
    slugs: NotRequired[Annotated[list[str], "One or more slugs to look up"]]
    file_path: NotRequired[Annotated[str, "File path to search for"]]
    tags: NotRequired[Annotated[list[str], "Tags to filter by (OR logic)"]]
    search_terms: NotRequired[
        Annotated[list[str], "Text search terms (AND logic, case-insensitive)"]
    ]
    limit: NotRequired[Annotated[int, "Max results to return (default 10)"]]
    show_all: NotRequired[Annotated[bool, "Return all matches (no cap)"]]
    include_fields: NotRequired[
        Annotated[list[str], "Return slug + these fields instead of slug+title"]
    ]


class SearchRelationshipsInput(TypedDict):
    relationship_type: NotRequired[
        Annotated[str, "Forward relationship type (e.g. REFERENCES) or composite key"]
    ]
    slug: NotRequired[Annotated[str, "Filter by this slug (source or target)"]]
    second_slug: NotRequired[Annotated[str, "Second slug for pair lookup"]]
    list_instances: NotRequired[
        Annotated[bool, "Return instances instead of type definition (List All mode)"]
    ]
    limit: NotRequired[Annotated[int, "Max results to return (default 10)"]]
    show_all: NotRequired[Annotated[bool, "Return all matches (no cap)"]]


class ManageEntityInput(TypedDict):
    entity_type: Annotated[str, "PascalCase entity type name"]
    slug: Annotated[str, "Entity slug to edit"]
    properties: Annotated[dict[str, Any], "Properties to set (partial update)"]
    mode: Annotated[str, "Must be 'edit'"]


class ManageRelationshipInput(TypedDict):
    relationship_type: Annotated[str, "Forward relationship type (e.g. REFERENCES)"]
    source_slug: Annotated[str, "Source entity slug"]
    target_slug: Annotated[str, "Target entity slug"]
    mode: Annotated[str, "Either 'create' or 'edit'"]
    properties: NotRequired[Annotated[dict[str, Any], "Properties to set"]]


class ValidateAndCommitInput(TypedDict):
    job_files: NotRequired[
        Annotated[list[str], "File paths for job completeness check"]
    ]


# ---------------------------------------------------------------------- #
# Tool factory
# ---------------------------------------------------------------------- #


def create_extraction_tools(
    worker_id: str,
    store: OntologyStore,
    ontology: Ontology,
) -> list[Any]:
    """Create the five extraction tools bound to a specific worker.

    Returns a list of SdkMcpTool instances ready for create_sdk_mcp_server.
    """

    # ------------------------------------------------------------------ #
    # 1. search_entities
    # ------------------------------------------------------------------ #

    @tool(
        "search_entities",
        "Search entity instances or get type definitions from the ontology.",
        SearchEntitiesInput,
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def search_entities(args: dict[str, Any]) -> dict[str, Any]:
        entity_type = args.get("entity_type")
        slugs = args.get("slugs")
        file_path = args.get("file_path")
        tags = args.get("tags")
        search_terms = args.get("search_terms")
        limit = args.get("limit", 10)
        show_all = args.get("show_all", False)
        include_fields = args.get("include_fields")

        # Determine mode based on inputs
        # Mode: Get by Slugs
        if slugs is not None:
            entities = store.search_entities_by_slugs(slugs, worker_id=worker_id)
            if not entities:
                return _err("No entities found for the given slugs.")
            results = [_entity_to_dict(e, ontology) for e in entities]
            return _ok(json.dumps(results, indent=2))

        # Mode: Get by file_path
        if file_path is not None:
            entities = store.search_entities_by_file_path(
                file_path, worker_id=worker_id
            )
            if not entities:
                return _err(f"No entities found with file_path={file_path!r}.")
            results = [_entity_to_dict(e, ontology) for e in entities]
            return _ok(json.dumps(results, indent=2))

        # All remaining modes require entity_type
        if entity_type is None:
            return _err("Must provide entity_type, slugs, or file_path.")

        # Mode: Type Definition (no tags, no search_terms)
        if tags is None and search_terms is None:
            type_def = ontology.get_entity_type(entity_type)
            if type_def is None:
                return _err(f"Unknown entity type: {entity_type!r}.")
            # Get instance count from store
            _, instance_count = store.search_entities_by_type(
                entity_type, worker_id=worker_id, limit=0
            )
            result = {
                "entity_type": type_def.type,
                "instance_count": instance_count,
                "description": type_def.description,
                "tier": str(type_def.tier),
                "required_properties": type_def.required_properties,
                "optional_properties": type_def.optional_properties,
                "property_definitions": type_def.property_definitions,
                "property_defaults": type_def.property_defaults,
                "tag_definitions": type_def.tag_definitions,
            }
            return _ok(json.dumps(result, indent=2))

        # Effective limit for capped modes
        effective_limit = 999999999 if show_all else limit

        # Mode: Filter by Tags
        if tags is not None:
            # Warn on tags not in tag_definitions
            type_def = ontology.get_entity_type(entity_type)
            warnings: list[str] = []
            if type_def and type_def.tag_definitions:
                allowed = set(type_def.tag_definitions.keys())
                for t in tags:
                    if t not in allowed:
                        warnings.append(f"Tag {t!r} not in tag_definitions.")

            entities_list, total = store.search_entities_by_tag(
                entity_type,
                tags,
                worker_id=worker_id,
                limit=effective_limit,
            )

            results = _format_summary_results(entities_list, include_fields)
            output: dict[str, Any] = {"results": results, "total": total}
            if not show_all and total > len(entities_list):
                output["warning"] = (
                    f"Showing {len(entities_list)} of {total} results. "
                    f"Use show_all=true or increase limit to see more."
                )
            if warnings:
                output["tag_warnings"] = warnings
            return _ok(json.dumps(output, indent=2))

        # Mode: Search by Text
        if search_terms is not None:
            entities_list, total = store.search_entities_by_text(
                entity_type,
                search_terms,
                worker_id=worker_id,
                limit=effective_limit,
            )

            results = _format_summary_results(entities_list, include_fields)
            output = {"results": results, "total": total}
            if not show_all and total > len(entities_list):
                output["warning"] = (
                    f"Showing {len(entities_list)} of {total} results. "
                    f"Use show_all=true or increase limit to see more."
                )
            return _ok(json.dumps(output, indent=2))

        return _err("Invalid search_entities input combination.")

    # ------------------------------------------------------------------ #
    # 2. search_relationships
    # ------------------------------------------------------------------ #

    @tool(
        "search_relationships",
        "Search relationship instances or get type definitions from the ontology.",
        SearchRelationshipsInput,
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def search_relationships(args: dict[str, Any]) -> dict[str, Any]:
        relationship_type = args.get("relationship_type")
        slug = args.get("slug")
        second_slug = args.get("second_slug")
        list_instances = args.get("list_instances", False)
        limit = args.get("limit", 10)
        show_all = args.get("show_all", False)

        if relationship_type is None:
            return _err("Must provide relationship_type.")

        # Resolve composite key(s) from forward type or composite key
        composite_keys = _resolve_composite_keys(ontology, relationship_type)
        if not composite_keys:
            return _err(f"No relationship type found matching {relationship_type!r}.")

        effective_limit = 999999999 if show_all else limit

        # Mode: Type Definition (no slug, not requesting instances)
        if slug is None and not list_instances:
            results = []
            for ck in composite_keys:
                rel_type_def = ontology.get_relationship_type(ck)
                if rel_type_def is None:
                    continue
                _, instance_count = store.search_relationships_by_type(
                    ck, worker_id=worker_id, limit=0
                )
                results.append(
                    {
                        "composite_key": ck,
                        "relationship_type": rel_type_def.forward_relationship.type,
                        "forward_type": rel_type_def.forward_relationship.type,
                        "instance_count": instance_count,
                        "description": rel_type_def.forward_relationship.description,
                        "source_entity_type": rel_type_def.source_entity_type,
                        "target_entity_type": rel_type_def.target_entity_type,
                        "required_parameters": rel_type_def.required_parameters,
                        "optional_parameters": rel_type_def.optional_parameters,
                        "property_definitions": rel_type_def.property_definitions,
                    }
                )
            return _ok(json.dumps(results, indent=2))

        # Mode: List All (list_instances=True, no slug)
        if slug is None and list_instances:
            all_rels: list[RelationshipInstance] = []
            for ck in composite_keys:
                rels, _ = store.search_relationships_by_type(
                    ck, worker_id=worker_id, limit=999999999
                )
                all_rels.extend(rels)
            total = len(all_rels)
            capped = all_rels[:effective_limit]
            results = [_relationship_to_dict(r) for r in capped]
            output: dict[str, Any] = {"results": results, "total": total}
            if not show_all and total > len(capped):
                output["warning"] = (
                    f"Showing {len(capped)} of {total} results. "
                    f"Use show_all=true or increase limit to see more."
                )
            return _ok(json.dumps(output, indent=2))

        # Mode: List by Slug (one or two slugs)
        # Search across ALL matching composite keys
        if second_slug is not None:
            # Two slugs: specific relationship between that pair
            matches: list[RelationshipInstance] = []
            for ck in composite_keys:
                rels, _ = store.search_relationships_by_type(
                    ck, worker_id=worker_id, limit=999999999
                )
                for r in rels:
                    if (r.source_slug == slug and r.target_slug == second_slug) or (
                        r.source_slug == second_slug and r.target_slug == slug
                    ):
                        matches.append(r)
            results = [_relationship_to_dict(r) for r in matches]
            return _ok(json.dumps(results, indent=2))

        # One slug: relationships involving this slug across all composite keys
        assert slug is not None  # guarded by slug is None branches above
        all_matches: list[RelationshipInstance] = []
        for ck in composite_keys:
            rels, _ = store.search_relationships_by_slug(
                ck, slug, worker_id=worker_id, limit=999999999
            )
            all_matches.extend(rels)
        total = len(all_matches)
        capped = all_matches[:effective_limit]
        results = [_relationship_to_dict(r) for r in capped]
        output = {"results": results, "total": total}
        if not show_all and total > len(capped):
            output["warning"] = (
                f"Showing {len(capped)} of {total} results. "
                f"Use show_all=true or increase limit to see more."
            )
        return _ok(json.dumps(output, indent=2))

    # ------------------------------------------------------------------ #
    # 3. manage_entity
    # ------------------------------------------------------------------ #

    @tool(
        "manage_entity",
        "Edit properties on an existing entity instance. Stages the update.",
        ManageEntityInput,
    )
    async def manage_entity(args: dict[str, Any]) -> dict[str, Any]:
        entity_type = args.get("entity_type", "")
        slug = args.get("slug", "")
        properties = args.get("properties")
        mode = args.get("mode", "")

        if mode != "edit":
            return _err("Mode must be 'edit'.")

        # Validate entity type is editable
        type_def = ontology.get_entity_type(entity_type)
        if type_def is None:
            return _err(f"Unknown entity type: {entity_type!r}.")
        if type_def.is_structural:
            return _err(
                f"Entity type {entity_type!r} is structural and cannot be edited."
            )

        # Validate properties is a non-empty dict
        if not isinstance(properties, dict) or not properties:
            return _err("Properties must be a non-empty object.")

        # Load current instance from virtual ontology
        current = store.get_entity_by_slug(slug, worker_id=worker_id)
        if current is None:
            return _err(f"Entity not found: {slug!r}.")

        # Verify entity type matches
        expected_prefix = _pascal_to_kebab(entity_type)
        if current.entity_type != expected_prefix:
            return _err(
                f"Entity {slug!r} is of type {current.entity_type!r}, "
                f"not {expected_prefix!r}."
            )

        # Validate property types against schema
        for prop_name, prop_value in properties.items():
            if not _is_valid_property_value(prop_value):
                return _err(
                    f"Invalid property type for {prop_name!r}: "
                    f"must be str, bool, int, or list[str]."
                )

        # Validate tags if present
        if "tags" in properties:
            tags = properties["tags"]
            if not isinstance(tags, list):
                return _err("Tags must be an array of strings.")
            if type_def.tag_definitions:
                allowed = set(type_def.tag_definitions.keys())
                for tag in tags:
                    if not isinstance(tag, str):
                        return _err("Tags must be an array of strings.")
                    if tag not in allowed:
                        return _err(f"Invalid tag {tag!r}. Allowed: {sorted(allowed)}.")

        # Deep-copy current, merge properties
        merged_props = copy.deepcopy(dict(current.properties))
        merged_props.update(properties)

        updated = EntityInstance(slug=slug, properties=merged_props)

        # Stage the update
        store.stage_entity(worker_id, updated)

        return _ok(
            json.dumps(
                {"status": "staged", "entity": _entity_to_dict(updated)},
                indent=2,
            )
        )

    # ------------------------------------------------------------------ #
    # 4. manage_relationship
    # ------------------------------------------------------------------ #

    @tool(
        "manage_relationship",
        "Create or edit a relationship instance. Stages the update.",
        ManageRelationshipInput,
    )
    async def manage_relationship(args: dict[str, Any]) -> dict[str, Any]:
        rel_type_name = args.get("relationship_type", "")
        source_slug = args.get("source_slug", "")
        target_slug = args.get("target_slug", "")
        mode = args.get("mode", "")
        properties = args.get("properties", {})

        if mode not in ("create", "edit"):
            return _err("Mode must be 'create' or 'edit'.")

        # Resolve source and target entity types from slugs
        source_entity = store.get_entity_by_slug(source_slug, worker_id=worker_id)
        if source_entity is None:
            return _err(f"Source entity not found: {source_slug!r}.")

        target_entity = store.get_entity_by_slug(target_slug, worker_id=worker_id)
        if target_entity is None:
            return _err(f"Target entity not found: {target_slug!r}.")

        # Resolve PascalCase entity types from kebab-case prefixes
        source_type_def = ontology.find_entity_type_for_slug(source_slug)
        if source_type_def is None:
            return _err(f"Cannot resolve entity type for source slug {source_slug!r}.")
        target_type_def = ontology.find_entity_type_for_slug(target_slug)
        if target_type_def is None:
            return _err(f"Cannot resolve entity type for target slug {target_slug!r}.")

        source_entity_type = source_type_def.type
        target_entity_type = target_type_def.type

        # Construct composite key
        composite_key = f"{source_entity_type}|{rel_type_name}|{target_entity_type}"

        # Validate composite key exists in ontology
        rel_type_def = ontology.get_relationship_type(composite_key)
        if rel_type_def is None:
            return _err(
                f"Unknown relationship type: {composite_key!r}. "
                f"No relationship definition found for this combination."
            )

        # Structural protection
        if rel_type_def.is_structural:
            return _err(
                f"Relationship type {composite_key!r} is structural "
                f"and cannot be modified."
            )

        if mode == "create":
            # Check for duplicate
            existing_rels, _ = store.search_relationships_by_type(
                composite_key, worker_id=worker_id, limit=999999999
            )
            for r in existing_rels:
                if r.source_slug == source_slug and r.target_slug == target_slug:
                    return _err(
                        f"Relationship already exists: {composite_key!r} "
                        f"from {source_slug!r} to {target_slug!r}. "
                        f"Use mode='edit' to modify."
                    )

            rel = RelationshipInstance(
                source_entity_type=source_entity_type,
                source_slug=source_slug,
                target_entity_type=target_entity_type,
                target_slug=target_slug,
                relationship_type=rel_type_name,
                properties=dict(properties) if properties else {},
            )
            store.stage_relationship(worker_id, rel)
            return _ok(
                json.dumps(
                    {
                        "status": "staged",
                        "mode": "create",
                        "relationship": _relationship_to_dict(rel),
                    },
                    indent=2,
                )
            )

        # Edit mode
        existing_rels, _ = store.search_relationships_by_type(
            composite_key, worker_id=worker_id, limit=999999999
        )
        existing = None
        for r in existing_rels:
            if r.source_slug == source_slug and r.target_slug == target_slug:
                existing = r
                break

        if existing is None:
            return _err(
                f"Relationship not found: {composite_key!r} "
                f"from {source_slug!r} to {target_slug!r}."
            )

        # Deep-copy and merge properties
        merged_props = copy.deepcopy(dict(existing.properties))
        merged_props.update(properties)

        updated = RelationshipInstance(
            source_entity_type=source_entity_type,
            source_slug=source_slug,
            target_entity_type=target_entity_type,
            target_slug=target_slug,
            relationship_type=rel_type_name,
            properties=merged_props,
        )
        store.stage_relationship(worker_id, updated)
        return _ok(
            json.dumps(
                {
                    "status": "staged",
                    "mode": "edit",
                    "relationship": _relationship_to_dict(updated),
                },
                indent=2,
            )
        )

    # ------------------------------------------------------------------ #
    # 5. validate_and_commit
    # ------------------------------------------------------------------ #

    @tool(
        "validate_and_commit",
        "Validate all staged edits and commit to the shared ontology.",
        ValidateAndCommitInput,
    )
    async def validate_and_commit_tool(args: dict[str, Any]) -> dict[str, Any]:
        job_files = args.get("job_files")

        errors = store.validate_and_commit(worker_id, job_files=job_files)
        if errors:
            return _err(
                json.dumps(
                    {"status": "validation_failed", "errors": errors},
                    indent=2,
                )
            )

        return _ok(json.dumps({"status": "committed"}, indent=2))

    return [
        search_entities,
        search_relationships,
        manage_entity,
        manage_relationship,
        validate_and_commit_tool,
    ]


def create_tool_server(
    worker_id: str,
    store: OntologyStore,
    ontology: Ontology,
) -> Any:
    """Create an MCP server with extraction tools bound to a worker.

    Returns a McpSdkServerConfig for use with ClaudeAgentOptions.mcp_servers.
    """
    tools = create_extraction_tools(worker_id, store, ontology)
    return create_sdk_mcp_server(
        name="extraction-tools",
        tools=tools,
    )


# ---------------------------------------------------------------------- #
# Internal helpers
# ---------------------------------------------------------------------- #


def _resolve_composite_keys(ontology: Ontology, type_or_key: str) -> list[str]:
    """Resolve a forward type or composite key to composite key(s).

    If the input contains '|', treat it as a composite key.
    Otherwise, search for relationship types matching the forward type.
    """
    if "|" in type_or_key:
        if ontology.get_relationship_type(type_or_key) is not None:
            return [type_or_key]
        return []

    # Search by forward type
    matches = []
    for ck, rel_type_def in ontology.relationship_types.items():
        if rel_type_def.forward_relationship.type == type_or_key:
            matches.append(ck)
    return matches


def _format_summary_results(
    entities: list[EntityInstance],
    include_fields: list[str] | None,
) -> list[dict[str, Any]]:
    """Format entity results as summary dicts (slug + title or custom fields)."""
    results = []
    for e in entities:
        d: dict[str, Any] = {"slug": e.slug}
        if include_fields:
            for field in include_fields:
                d[field] = e.properties.get(field)
        else:
            d["title"] = e.properties.get("title")
        results.append(d)
    return results
