# Multi-Source Extraction

k-extract accepts multiple data source paths at init time:

```
k-extract init /path/to/repo-a /path/to/repo-b /path/to/repo-c
```

## How This Differs from the Original

The original system had 3 hardcoded data sources (openshift-docs, rosa-kcs, ops-sop), each with its own YAML context file defining fetch method, entity type mapping, and partition strategy. Data sources were processed sequentially with processing order defined in configuration.

In k-extract, data sources are just paths provided by the user. The system treats them uniformly — same ontology, same extraction instructions, same output format. The `data_source_id` field on each JSONL output line identifies which source an entity came from.

## Cross-Source Relationships

The user's problem statement often spans multiple sources. From the original developer's experience:

> "I need to know if the gaps are tested by the ROSA repo's tests"

This requires the extraction agents to create relationships between entities discovered in different data sources. The ontology is shared across all sources — an entity type like `TestCase` applies regardless of which repo it was found in.

The extraction pipeline must support this:
- All sources share a single ontology definition
- Entities from earlier sources are visible (via search tools) when processing later sources
- Cross-source relationships are first-class (same JSONL CREATE operations)

## Per-Source Configuration

The config file produced by `init` records each data source path. The system may need per-source metadata:
- A human-readable name or identifier (used as `data_source_id` in JSONL output)
- File type patterns to include/exclude

This is derived during the data inventory step of the guided session, not manually configured.

## Partitioning Across Sources

Each data source is partitioned independently into jobs. The original system's V2 character-based batching (see [job-lifecycle.md](../process/job-lifecycle.md)) applies per-source. Jobs from different sources can potentially be processed in parallel, but cross-source relationship creation requires that referenced entities already exist — this implies source processing order may matter, or that relationship creation is deferred.
