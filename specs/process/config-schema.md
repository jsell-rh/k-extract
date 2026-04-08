# Config File Schema

The config file (`extraction.yaml`) is the single artifact that bridges `k-extract init` and `k-extract run`. It captures all decisions from the guided session and is the complete, portable specification for an extraction run.

## Full Schema

```yaml
# Problem statement — free-form text from the user
problem_statement: |
  I have 3 repos for openshift-hyperfleet, and I don't understand my testing
  inventory. I need to understand what's being tested and what my gaps are.
  And I need to know if the gaps are tested by the ROSA repo's tests.

# Data sources — paths provided to k-extract init
data_sources:
  - name: hyperfleet-core
    path: /path/to/hyperfleet-core
  - name: hyperfleet-operator
    path: /path/to/hyperfleet-operator
  - name: hyperfleet-cli
    path: /path/to/hyperfleet-cli
  - name: rosa-tests
    path: /path/to/rosa-tests

# Ontology definition — confirmed during the guided session
ontology:
  entity_types:
    - label: TestCase
      description: "Individual test function/method..."
      required_properties:
        - name
        - framework
        - file_path
      optional_properties: []
      tag_definitions: {}

    - label: Component
      description: "A software module, package, or subsystem..."
      required_properties:
        - name
        - kind
      optional_properties: []
      tag_definitions: {}

  relationship_types:
    - label: TESTS
      description: "A test exercises a component..."
      source_entity_type: TestCase
      target_entity_type: Component
      required_properties: []
      optional_properties: []

    - label: CONTAINS
      description: "A suite groups tests..."
      source_entity_type: TestSuite
      target_entity_type: TestCase
      required_properties: []
      optional_properties: []

# Prompts — composed during init (static template + LLM-generated guidance)
prompts:
  system_prompt: |
    You are a knowledge extraction agent...
    [composed from static template + LLM-generated extraction guidance]

  job_description_template: |
    ## Job {job_id}
    Process the following {file_count} files ({total_characters} characters):
    {file_list}

# Output configuration
output:
  file: graph.jsonl        # JSONL output path (appended on resume)
  database: extraction.db  # SQLite database for run state (jobs, fingerprint, etc.)
```

## Field Reference

### `problem_statement`
- **Type:** string (multiline)
- **Source:** User input during guided session step 1
- **Purpose:** Anchors all extraction decisions. Included in the environment fingerprint.

### `data_sources`
- **Type:** list of objects
- **Fields:**
  - `name` (string, required): Human-readable identifier. Used as `data_source_id` in JSONL output.
  - `path` (string, required): Absolute path to the data source directory.
- **Source:** CLI arguments to `k-extract init`

### `ontology`
- **Type:** object with `entity_types` and `relationship_types` lists
- **Source:** AI-proposed during init, refined iteratively by user

#### `ontology.entity_types[]`
| Field | Type | Required | Description |
|---|---|---|---|
| `label` | string | yes | PascalCase entity type name |
| `description` | string | yes | What this type represents and when to create one |
| `required_properties` | string[] | yes | Properties every instance must have |
| `optional_properties` | string[] | yes | Properties instances may have (can be empty) |
| `tag_definitions` | object | no | Map of allowed tag name to description |

#### `ontology.relationship_types[]`
| Field | Type | Required | Description |
|---|---|---|---|
| `label` | string | yes | UPPER_SNAKE_CASE relationship type name |
| `description` | string | yes | What this relationship represents |
| `source_entity_type` | string | yes | Entity type for the source end |
| `target_entity_type` | string | yes | Entity type for the target end |
| `required_properties` | string[] | yes | Properties every instance must have |
| `optional_properties` | string[] | yes | Properties instances may have (can be empty) |

### `prompts`
- **Type:** object with two string fields
- **Source:** Composed during init (static template + LLM generation)

| Field | Description |
|---|---|
| `system_prompt` | Complete system prompt passed to every agent instance. Includes workflow instructions, tool documentation, and extraction guidance. |
| `job_description_template` | Template with `{variable}` placeholders substituted per-job at run time. Variables: `{job_id}`, `{file_count}`, `{total_characters}`, `{file_list}`. |

### `output`
- **Type:** object
- **Fields:**
  - `file` (string, required): Path for the JSONL output file. Appended on resume.
  - `database` (string, optional): Path for the SQLite database storing run state (jobs, environment fingerprint, worker assignments). Defaults to `extraction.db` in the current directory. Can be overridden via `--db` CLI arg.

## Editability

The config file is human-readable YAML. Users can edit any field between `init` and `run`:
- Edit `prompts.system_prompt` to tweak extraction guidance
- Edit `ontology` to add/remove/rename types
- Edit `output.file` to change the output location

Any edit changes the environment fingerprint, which correctly prevents resuming a previous run with mixed parameters.

## Relationship to Other Specs

- The ontology fields map to the domain model in [domain-model.md](../domain/domain-model.md)
- The prompts fields are composed as described in [prompt-generation.md](../agent/prompt-generation.md)
- The output format must conform to [output-format.md](output-format.md)
- The file is part of the environment fingerprint described in [extraction-pipeline.md](extraction-pipeline.md#resumability-and-environment-fingerprinting)
