# k-extract

Extract knowledge graphs from any codebase or documentation. Point it at your repos, describe what you're trying to understand, and get a graph out.

## Install

```bash
pip install k-extract
```

## Quick Start

### 1. Define what to extract

```bash
k-extract init ./my-repo ./another-repo
```

This walks you through:
- Describing your problem ("I need to understand my testing inventory and coverage gaps")
- Reviewing a proposed ontology (entity types, relationship types)
- Refining until you're satisfied

Produces `extraction.yaml` — your complete extraction config.

### 2. Run the extraction

```bash
k-extract run --config extraction.yaml
```

Outputs `graph.jsonl`. Ctrl-C anytime — re-run to resume where you left off.

### 3. Load into kartograph

The output is [kartograph](https://github.com/jsell-rh/kartograph)-compatible JSONL. Feed it to kartograph's mutation endpoint to query your graph.

## Requirements

- Python 3.12+
- An Anthropic API key (or Vertex AI credentials) — set via environment variables
- Model configured via environment (e.g., `ANTHROPIC_MODEL=claude-sonnet-4-6`)

## Configuration

`extraction.yaml` is human-readable and fully editable. It contains:

- **problem_statement** — what you're trying to understand
- **data_sources** — paths to your repos/data
- **ontology** — entity and relationship types to extract
- **prompts** — the exact instructions agents receive (generated, but editable)
- **output** — where results go (`graph.jsonl`, `extraction.db`)

Edit any field, re-run. Changing the config invalidates previous results — use `--force` to start fresh.

## CLI Reference

```
k-extract init <path> [<path> ...]   # Interactive ontology design
k-extract run --config <yaml>        # Run extraction (resumes by default)
k-extract run --config <yaml> --force  # Discard previous results, start fresh
k-extract jobs --config <yaml>       # Inspect job state
k-extract jobs --config <yaml> --status failed  # See failed jobs
```

## How It Works

1. `init` scans your data, proposes an ontology based on your problem statement, and generates agent prompts
2. `run` batches files into jobs sized to the model's context window, then launches parallel agents
3. Each agent reads source files, extracts entities/relationships via tool calls, and commits to a shared store
4. Results stream to `graph.jsonl` as jobs complete

## License

Apache-2.0
