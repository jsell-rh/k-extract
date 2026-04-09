# Extraction Pipeline

Describes the end-to-end flow from data sources through to a populated ontology, including orchestration, coordination, and failure handling.

---

## End-to-End Pipeline

The extraction pipeline has two major steps:

```
Data Sources --> [Step 1: Partition] --> Partitions --> [Step 2: Process] --> Ontology
```

### Step 1: Partition Data Sources

For each data source, an agent creates partition files ("file subsets") that divide the data source's files into logical groups. Partitions are validated (complete coverage, no overlaps, no duplicates) with a retry loop (up to 3 attempts). Multiple data sources are partitioned in parallel (one agent per data source).

This step can be skipped if partitions already exist from a previous run.

### Step 2: Generate All Jobs (upfront)

Jobs are generated for **all data sources** before any worker starts. The system iterates every configured data source, enumerates its files, and batches them into jobs. All jobs are written to the database before extraction begins.

This gives the system (and the user) a complete picture of the total work scope before committing to the extraction run.

### Step 3: Process Jobs (global execution)

Workers claim jobs from a **single global queue** spanning all data sources. Workers do not process one data source at a time — they pick up the next available pending job regardless of source. This maximizes worker utilization and avoids idle workers while jobs remain in other sources.

The pipeline:

1. **Initialize ontologies** based on initialization mode
2. **Prepare worker workspaces** with clean staging areas for each worker
3. **Launch workers** — each claims jobs from the global queue until none remain
4. **Record completion** per job

---

## Orchestrator Role

The orchestrator manages the overall extraction run:

1. **Generate all jobs upfront** — iterate all data sources, enumerate files, batch into jobs, write to database. This happens before any worker launches.
2. **Reset stale jobs** — any in_progress jobs from a prior interrupted run are reset to pending.
3. **Launch N worker instances** concurrently via `asyncio.gather`.
4. Each worker runs a loop: claim the next pending job from the **global queue** (any data source), run the agent, record the result.
5. Workers continue until no pending jobs remain or the max_jobs cap is reached.
6. A shared counter tracks total jobs processed across all workers for enforcing global job limits.

Workers claim from a single global queue — they do not process data sources sequentially. A worker that finishes a job from `hypershift` may next pick up a job from `rosa` or `ocm-api-model`. This maximizes worker utilization.

The `claim_next_job` function no longer filters by `data_source` — it claims the next pending job by global order number. The `cwd` for the agent is determined from the job's `data_source` field (mapped to the corresponding path from the config).

---

## The Staged-Edit, Validate, Commit Pattern

This is the core coordination pattern for how multiple workers safely update a shared ontology.

### How it works

1. **Stage locally**: Each worker stages its changes in a private workspace. The workspace contains proposed entity and relationship edits, keyed by type. Workers only modify their private workspace during processing — they never directly modify the shared ontology.

2. **Validate**: When a worker finishes its job, the system:
   - Acquires exclusive access to the shared ontology.
   - Loads the current shared ontology.
   - Merges the worker's staged edits into the shared state (in memory) to create a combined view.
   - Validates the merged result: slug uniqueness across entity types, required properties, tag validity, relationship reference integrity, and scenario consistency.
   - Validates that all job files have been marked as processed.

3. **Commit atomically**: If validation passes:
   - Writes the merged ontology back to the shared store while still holding exclusive access.
   - Records an execution log.
   - Releases exclusive access.
   - Signals job completion.

4. **Reject on failure**: If validation fails, the system reports errors and exits without modifying the shared state. The agent is expected to fix issues and retry.

### Why this pattern

- Workers can operate independently without blocking each other during the long processing phase.
- Exclusive access is only held for the brief validate+commit window.
- Validation catches conflicts that would arise from concurrent modifications (e.g., duplicate slugs created by different workers).

---

## Multi-Worker Coordination

### Ontology Access During Processing

Workers need to read the shared ontology during processing (to search for existing entities, check for duplicates, etc.). Access to the shared ontology is mediated through a locking mechanism that supports both shared (read) and exclusive (write) access with configurable timeouts.

Workers use a "virtual ontology" concept: agent tools merge the worker's staged edits with the current shared ontology to provide a view that includes the worker's uncommitted changes. This means a worker sees its own staged changes when searching, without those changes being visible to other workers until committed.

### Lock Contention Guidance

The job description explicitly tells agents: if a search or edit operation blocks or reports a lock error, another worker may be committing. Wait and retry. The lock contention window is short (validate+commit is fast).

### Aggregator (explored but not adopted)

An aggregator concept was explored for cross-worker reconciliation: after all workers in a round complete, an aggregator agent would review staged edits from all workers, resolve conflicts, ensure coverage, and produce a reconciled commit. This was designed but ultimately disabled — the simpler approach of per-worker validation at commit time proved sufficient.

---

## Resumability and Environment Fingerprinting

`k-extract run` resumes by default. If a previous run was interrupted, completed jobs are skipped and only pending/failed jobs are processed. JSONL output is appended (CREATE operations use MERGE semantics in kartograph, so duplicates from re-processed jobs are harmless).

### Environment Fingerprint

Resuming is only safe if the extraction environment hasn't changed since the previous run. If the config, prompts, source data, or model changed, results from completed jobs would be inconsistent with results from remaining jobs.

At the start of each run, the system computes a **cryptographic environment fingerprint**:

1. Enumerate all source files, respecting `.gitignore` in each data source (if present). Files matched by `.gitignore` are excluded from fingerprinting, batching, and extraction.
2. Hash every enumerated source file (SHA256) in parallel — files are independent I/O operations
3. Sort the file hashes by filepath for deterministic ordering
4. Compute a final SHA256 over the concatenation of:
   - Config file contents (problem statement, ontology definition, all settings)
   - Generated prompt templates (system prompt + job description template)
   - Model ID
   - Sorted file content hashes

The resulting fingerprint is stored in the run's database alongside job state.

### Resume Logic

On each `k-extract run`:

1. Compute the current environment fingerprint
2. If no previous run exists → start fresh
3. If previous run exists and fingerprints match → resume (skip completed jobs)
4. If previous run exists and fingerprints differ → **hard stop** with error explaining that the environment has changed
   - `--force` flag discards previous run state and starts fresh
   - Without `--force`, the run refuses to proceed — no silent resume on a dirty environment

### Why Hard Stop

Resuming with a changed environment produces an inconsistent graph: some entities extracted with old prompts, others with new. This is worse than re-extracting from scratch because the inconsistency is silent. A hard stop forces the user to make an explicit choice.

---

## Pipeline Configuration

The pipeline is configured with the following conceptual parameters:

| Concept | Purpose |
|---|---|
| Worker concurrency | Number of concurrent worker instances |
| Job limit | Cap on total jobs to process (useful for testing) |

---

## Worker Failure Handling

### Within a job (agent errors)

If a worker encounters an error during processing, the job is recorded as failed with the error details. The failure is isolated: other workers (whether concurrent tasks or parallel processes) are unaffected and continue processing their own jobs.

### Worker process crash

If a worker crashes, its job remains in in_progress. Stale job detection (timeout-based or unconditional reset at startup) recovers the job by resetting it to pending on the next run. See [Job Lifecycle — Stale Job Detection](job-lifecycle.md#stale-job-detection-and-recovery).

### Ontology corruption protection

- Workers never write directly to the shared ontology.
- The staged-edit, validate, commit pattern ensures only validated changes are applied.
- Exclusive access via database transactions prevents concurrent writes and eliminates the corruption risk that existed in the original file-based system.

### Agent permission boundaries

Agents are sandboxed to prevent direct modification of shared state. They can only interact with the ontology through provided tools (search, manage entities, manage relationships, validate and commit). Direct file-writing capabilities are restricted.

---

## Progress Dashboard

The live progress dashboard shows **n+1 progress bars**: one per data source plus one for the overall total.

```
  k-extract  elapsed: 1h 08m  cost: $31.35

  Total                ━━━━━━━━━━━━━╺━━━━━━━━━━━━━━━━  93/714 jobs
  hypershift           ━━━━━━━━━━━━━━━━━━━━╺━━━━━━━━━━  87/654 jobs
  ocm-api-model        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━╺━━  32/34 jobs
  cluster-api-prov...  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╺━   3/26 jobs
  rosa                 ╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   0/42 jobs
  ...

  worker-01:  processing 47bffb3abbcc474c (1793s)
  worker-02:  processing a1b2c3d4e5f67890 (423s)
  worker-03:  idle
  ...

  completed: 93  failed: 29  pending: 592
```

Since all jobs are generated upfront and workers claim from a global queue, the dashboard can show accurate totals from the start. Per-source bars update as their jobs complete regardless of order.

---

## User-Facing Error Reporting

### During extraction

Failed jobs are reported in real time via structlog:

```
extraction.job_failed | worker=03 | job=hyperfleet-core_batch_0012 | error="Validation failed: slug 'component:auth-manager' already exists in entity type Component"
```

### On completion or interruption

The CLI prints a summary:

```
Extraction complete. 107/110 jobs completed, 3 failed.
Output: graph.jsonl (3,247 lines)
Total cost: $14.23

Failed jobs:
  hyperfleet-core_batch_0012: Validation failed: duplicate slug
  rosa-tests_batch_0041: Agent exceeded max turns
  rosa-tests_batch_0048: Commit failed: referential integrity violation

Re-run to retry failed jobs, or use `k-extract jobs --config extraction.yaml` to inspect.
```

### Job inspection

Users can query job state from the database:

```
$ k-extract jobs --config extraction.yaml
$ k-extract jobs --config extraction.yaml --status failed
$ k-extract jobs --config extraction.yaml --job rosa-tests_batch_0041
```

---

## Conversation Logging

### Debug mode (`--log-conversations`)

When the `--log-conversations` flag is set, the system streams the full agent conversation for each worker to a file. This captures every message (system prompt, assistant responses, tool calls, tool results) as they occur — streaming ensures no data is lost even on crash.

```
$ k-extract run --config extraction.yaml --log-conversations

Conversation logs: ./logs/conversations/
  worker_01_batch_0001.jsonl
  worker_01_batch_0002.jsonl
  worker_02_batch_0001.jsonl
  ...
```

Each file is JSONL — one line per message — streamed as the conversation progresses. This supports:
- Post-hoc debugging of extraction quality issues
- Understanding why an agent made a particular extraction decision
- Reproducing agent behavior for prompt tuning

Conversation logging is **off by default** (it generates substantial data). The flag is intended for debugging and prompt development, not production use.

---

## V1 to V2 Evolution

Key process changes and the reasons behind them (historical context, superseded by k-extract design):

1. **Competing queue to round-based assignment**: The shared queue with locking worked but added complexity. The round model is simpler and avoids contention, at the cost of potentially uneven utilization if some jobs finish faster.

2. **Fixed file count to content-based batching**: Fixed files-per-job created wildly uneven workloads. Content-size-based batching normalizes the amount of material per job.

3. **In-process tasks to subprocess workers**: Running agents as async tasks in the same process meant a crash in one could affect others. Separate processes provide better isolation.

4. **Staged-edit pattern refined**: The original system used a complex multi-step workflow within each agent (plan, validate plan, generate staging, fill details, batch stage). This was simplified to: use CLI tools to stage edits, then validate and commit. The tools handle validation incrementally.

5. **Aggregator concept explored and shelved**: A cross-worker reconciliation agent was designed but ultimately disabled. Per-worker validation at commit time proved sufficient.
