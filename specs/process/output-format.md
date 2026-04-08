# Output Format

k-extract produces JSONL (JSON Lines) output — one JSON object per line. This format is streamed during extraction, meaning partial output from an interrupted run is always valid.

## Contract

The output JSONL **must** conform to the mutation format defined by [kartograph](https://github.com/aredenba-rh/kartograph). Kartograph is the consumer of this output. The JSONL spec is the contract between k-extract (the extraction/producer bounded context) and kartograph (the graph/consumer bounded context).

The canonical specification lives in the kartograph project:
- Documentation: `website/src/content/docs/guides/extraction-mutations.mdx`
- Pydantic model: `src/api/graph/domain/value_objects.py` (MutationOperation, lines 146-302)
- Service: `src/api/graph/application/services/graph_mutation_service.py`

## Operation Types

Four mutation operations: `DEFINE`, `CREATE`, `UPDATE`, `DELETE`

### DEFINE (Schema Declaration)

Must appear before any CREATE/UPDATE/DELETE. Declares node and edge types.

```jsonl
{"op": "DEFINE", "type": "node", "label": "Person", "description": "A person entity...", "required_properties": ["name"]}
{"op": "DEFINE", "type": "edge", "label": "KNOWS", "description": "A relationship...", "required_properties": ["since"]}
```

Required fields: `op`, `type` ("node"|"edge"), `label` (PascalCase), `description`, `required_properties`

### CREATE (Idempotent Entity Discovery)

Semantics: "I discovered this entity with these properties." Uses MERGE under the hood — accumulates properties, preserves existing unlisted properties.

```jsonl
{"op": "CREATE", "type": "node", "id": "person:1a2b3c4d5e6f7890", "label": "Person", "set_properties": {"slug": "alice-smith", "name": "Alice Smith", "data_source_id": "ds-123", "source_path": "people/alice.md"}}
{"op": "CREATE", "type": "edge", "id": "knows:9f8e7d6c5b4a3210", "label": "KNOWS", "start_id": "person:1a2b3c4d5e6f7890", "end_id": "person:abcdef0123456789", "set_properties": {"since": "2020", "data_source_id": "ds-123", "source_path": "people/alice.md"}}
```

Required fields: `op`, `type`, `id`, `label`, `set_properties` (must include `data_source_id`, `source_path`; nodes must include `slug`)
Edges additionally require: `start_id`, `end_id`

ID format: `{type_lowercase}:{16_hex_chars}` — regex: `^[0-9a-z_]+:[0-9a-f]{16}$`

### UPDATE (Explicit Property Changes)

Semantics: "Change this specific property value" or "Remove this property."

```jsonl
{"op": "UPDATE", "type": "node", "id": "person:1a2b3c4d5e6f7890", "set_properties": {"name": "Alice Smith-Jones"}}
{"op": "UPDATE", "type": "node", "id": "person:1a2b3c4d5e6f7890", "remove_properties": ["old_email"]}
```

Required fields: `op`, `type`, `id`, at least one of `set_properties` or `remove_properties`

### DELETE (Cascading Deletion)

Semantics: Entity no longer exists. DETACH DELETE for nodes (removes connected edges), DELETE for edges.

```jsonl
{"op": "DELETE", "type": "node", "id": "person:obsolete123456"}
```

Required fields: `op`, `type`, `id` — no other fields allowed.

## System Properties

Automatically managed, should not be tracked as optional properties:
- `data_source_id` (all entities)
- `source_path` (all entities)
- `slug` (nodes only)

## Execution Order

Operations in the JSONL do not need to be ordered. Kartograph executes them in this order:
1. DEFINE
2. DELETE edge
3. DELETE node
4. CREATE node
5. CREATE edge
6. UPDATE node
7. UPDATE edge

## Streaming

Output is JSONL specifically to support streaming. Lines (or batches of lines) can be written to the output file as extraction proceeds. An interrupted extraction produces valid, usable partial output.

## Relationship to k-extract

k-extract is responsible for:
1. Emitting DEFINE operations derived from the confirmed ontology in the config file
2. Emitting CREATE operations for each discovered entity and relationship during extraction
3. Generating deterministic IDs for entities (same entity discovered twice should get the same ID)
4. Populating `data_source_id` and `source_path` on every CREATE
5. Generating `slug` values for all nodes

k-extract does NOT currently use UPDATE or DELETE operations — those exist in the kartograph contract for other producers. k-extract's extraction model is discovery-based (CREATE with MERGE semantics).
