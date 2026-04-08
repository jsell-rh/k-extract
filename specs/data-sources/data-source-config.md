# Data Source Configuration Spec

Describes the pattern of how data sources are configured, fetched, partitioned, and turned into processing jobs.

## 1. Data Source Configuration Schema

Each data source is defined by a configuration record with the following attributes:

| Attribute | Type | Description |
|---|---|---|
| name | string | Unique identifier for the data source |
| description | string | Human-readable description |
| source location | string | URL or path to the source repository |
| authentication method | string or null | How to authenticate (e.g., environment variable name for a token). Null for public sources. |
| branch | string | Branch to fetch |
| subset selection | list of strings | Paths within the source to include. Supports directory-level and file-level granularity. |

### Example (generalizable pattern)

A data source configuration specifies:
- Where to find the data (repository URL)
- How to authenticate (token via environment variable, or none for public sources)
- Which subset of the repository to fetch (specific directories, files, or the entire repo)

### Generalizable pattern

The configuration schema is domain-agnostic. Any git-hosted data source can be described this way. The subset selection mechanism allows fetching portions of large repositories without cloning everything.

## 2. Data Source Fetching

Data sources are fetched via shallow sparse git clone with token-based auth.

### Fetch method: sparse shallow git clone

The fetch performs:
1. Initialize a local repository
2. Configure sparse checkout with the subset selection patterns
3. Add the remote origin (with token injected into the URL if authentication is configured)
4. Shallow fetch (depth=1) of the target branch
5. Checkout the branch

### Token authentication pattern

When authentication is configured:
- Read the token from the configured environment variable
- Inject into the HTTPS URL for authentication
- If the variable is not set, attempt clone without auth (with warning)

**Generalizable:** This pattern works for any HTTPS git repo. The auth injection is standard git HTTPS token auth. A new system could support additional fetch methods (HTTP download, S3, local filesystem) while keeping the same config schema.

### Fetch invocation

Each data source can be fetched individually or all sources can be fetched sequentially via CLI commands.

## 3. Data Partitioning

Partitions split a data source's files into manageable subsets for parallel processing. There are two partitioning strategies.

### 3.1 V1 Partitioning: Agent-created, thematic grouping

In V1, an AI agent analyzes the data source tree and creates partitions by grouping files thematically. One agent per data source runs in parallel.

Each partition includes:
- A partition identifier
- A title and description explaining the thematic grouping
- A list of file and directory paths belonging to this partition
- Placeholders for ontology data

Key constraints:
- Every file must appear in exactly one partition (complete coverage, no duplicates)
- Directory references include all files within, recursively
- Paths are relative to the data source root
- A validation step enforces completeness and disjointness
- If validation fails, the agent retries up to 3 times with error feedback

### 3.2 V2 Partitioning: Context-window-based batching

V2 uses a deterministic algorithm (see [job-lifecycle.md](../process/job-lifecycle.md) for details):
1. Pull file paths from the entity ontology (only files that have entity instances)
2. Group files by parent folder
3. Calculate a character threshold based on average file size
4. Ensure threshold >= largest file size
5. Within each folder, batch files until total character count exceeds the threshold
6. Only relevant file types are included

**Generalizable:** The partitioning strategy (thematic grouping vs. context-window-based batching) is independent of the domain. The key requirement is: every source file ends up in exactly one partition/job.

## 4. Partition-to-Job Generation Pipeline

### V1 Pipeline

1. Workflow configuration controls which data sources to process and in what order
2. For each data source, partition files are loaded
3. Directory paths are expanded to actual files (filtered by relevant file types)
4. Files are grouped into jobs of a fixed file count per job
5. Each job is written to the pending queue

### V2 Pipeline

1. Files are read from the entity ontology
2. Grouped by folder, batched by character threshold
3. Jobs are written to the pending queue

### Job lifecycle

Jobs move through states representing progress:
```
pending --> in_progress --> completed
                       --> failed
```

State transitions are performed atomically (with locking -- see concurrency spec).

## 5. Configuration Hierarchy

The system uses a layered configuration approach:

### Workflow-level configuration

Controls the overall extraction workflow:

| Concept | Description |
|---|---|
| Partition reuse | Whether to skip partition creation and use existing partitions |
| Extraction reuse | Whether to skip all data source processing |
| Job reuse | Whether to continue existing jobs instead of regenerating |
| Ontology initialization | How to initialize the ontology (`continue`, `empty`, or `starting_point`) |
| Processing order | Which data sources to process and in what order. Order 0 = skip. |
| Concurrency | Number of concurrent worker instances |
| Job cap | Maximum total jobs to process (optional) |

### Per-source configuration

Each data source has its own configuration describing how to fetch it (schema in section 1).

### Processing order

Data sources are processed in ascending order of their configured priority. Sources with order 0 are excluded. Duplicate order values are rejected (each source must have a unique order).

### Hierarchy summary

```
Workflow configuration   -- workflow-level flags, processing order, concurrency settings
Per-source configuration -- per-source fetch configuration (repo URL, auth, subset selection)
Partition data           -- per-source file groupings (created by partitioning step)
Job queue                -- per-job work units (created by job generation step)
```

## 6. What Is Domain-Specific vs. Generalizable

| Aspect | Domain-Specific | Generalizable |
|---|---|---|
| Configuration schema | No | Yes -- any git repo can be described |
| Sparse checkout fetch | No | Yes -- standard git mechanism |
| Token auth pattern | No | Yes -- standard HTTPS token injection |
| Subset selection values | Yes -- tied to specific repo structure | Pattern is general |
| Partition validation (complete + disjoint) | No | Yes -- domain-independent invariant |
| File type filtering | Partially -- tied to specific entity types | Pattern is general (filter by relevant file types) |
| Entity type to data source mapping in V2 | Yes -- hardcoded | Should be configurable |
| Character budget multiplier | Tuned for original corpus | Should be configurable |
| Processing order values | Domain-specific | Pattern is general |
| Job ID format | No | Yes -- convention is reusable |
