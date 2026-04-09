# Guided Session (`k-extract init`)

The guided session is the interactive design phase that produces a configuration file. This config file captures all decisions needed to drive extraction: the problem statement, data source inventory, ontology definition, and extraction instructions.

## CLI Surface

```
k-extract init /path/to/data [/path/to/more/data ...]  →  produces config file
k-extract run --config extraction.yaml                  →  produces graph.jsonl
```

- `init` is interactive by default (guided session)
- All interactive prompts can be overridden as CLI args for headless use
- `run` is headless-capable, uses the config file produced by `init`

## Guided Session Flow

### Step 1: Problem Statement

The session starts by asking the user to describe their problem domain — not what they want to extract, but what they're trying to understand.

**Prompt:** "What problems are you trying to solve with this knowledge graph?"

**Example input:**
> "I have 3 repos for openshift-hyperfleet, and I don't understand my testing inventory. I need to understand what's being tested and what my gaps are. And I need to know if the gaps are tested by the ROSA repo's tests."

This is free-form text. The system does not parse it into structured queries. It serves as the north star for all downstream decisions — the same data pointed at by a different problem statement should produce a different ontology.

**Key insight from V1/V2:** The intended use-case of the knowledge graph is critical in determining how extraction should be done. The same data source can produce wildly different graphs depending on how the user wants to use it. This was learned through the original developer's manual ontology design process but was never captured in the system.

### Step 2: Data Inventory

The system scans all provided paths and reports what it found:
- File types and counts
- Directory structure
- Rough volume (file count, total size)
- Any recognizable patterns (e.g., "this looks like a Python package", "this contains markdown documentation")

Files matched by `.gitignore` (if present in a data source) are excluded from the inventory. This prevents build artifacts, vendored dependencies, and generated files from polluting the scan.

This gives the user and the system a shared understanding of the raw material before proposing an ontology.

### Step 3: Ontology Proposal

An AI agent reads a representative sample of the data **in light of the problem statement** and produces a single, coherent ontology proposal:

- Proposed entity types (node labels) with descriptions and suggested required properties
- Proposed relationship types (edge labels) with descriptions and suggested required properties
- Reasoning for each: why this type supports the stated problem

This is a **one-shot proposal** — entities and relationships together, so the user can evaluate the design as a system.

### Step 3b: Iterative Refinement Loop

After the initial proposal, the user enters a refinement loop. On each iteration:

1. The system displays the current ontology
2. The user provides feedback or presses Enter to accept
3. If feedback is given, the system updates the ontology and loops back to step 1

The user has **unlimited revision opportunities**. The loop continues until the user presses Enter with no input, signaling acceptance. Examples of feedback:
- Add types the AI missed ("I also want CodeModule as an entity type")
- Remove types that aren't useful ("I don't need TestSuite")
- Rename or adjust descriptions
- Adjust properties
- Reconsider a previous decision ("Actually, drop COVERAGE_GAP — I can derive that from the graph")

The system should not ask the user for granular property-level decisions unless the user raises them. Sensible defaults should be derived from the problem statement and data. The target user does not have deep knowledge graph experience.

### Step 4: Config File Output

The guided session produces a config file that captures:
- Problem statement (verbatim)
- Data source paths
- Confirmed ontology definition (entity types, relationship types, properties)
- Any user-specified constraints or preferences

This file is the input to `k-extract run`. It can be edited directly for iteration — no need to re-run the interactive session. The user can ctrl-c at any point during `run`, edit the config, and re-run.

## Iteration Model

There is no interactive preview-and-adjust step during extraction. Instead:
- `run` streams JSONL output (lines or batches of lines)
- User can interrupt (ctrl-c) at any time; intermediate results are always valid output
- User edits the config file directly to adjust
- User re-runs `run`

This is more composable and respects the user's time better than a wizard loop.

## Headless Use

Every interactive prompt in `init` can be provided as a CLI arg:

```
k-extract init /path/to/data \
  --problem "I need to understand my testing inventory..." \
  --output extraction.yaml
```

This supports automation, CI pipelines, and scripted workflows.
