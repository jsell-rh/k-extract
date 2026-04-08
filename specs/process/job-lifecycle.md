# Job Lifecycle

Spec extracted from kartograph-extraction codebase. Describes how extraction work is divided into jobs, how jobs move through states, and how workers claim and complete them.

---

## Job States and Transitions

A job has four states, forming a state machine:

```
pending --> in_progress --> completed
                       \-> failed
```

- **pending**: Job is generated and waiting to be claimed by a worker.
- **in_progress**: A worker has claimed the job and is actively processing it.
- **completed**: The worker finished processing and committed its results.
- **failed**: The worker encountered an error. The job records an error message and increments an attempt counter.

There is no automatic retry from failed state. Stale job recovery (see below) only moves in_progress jobs back to pending.

---

## Job Data Model

Each job contains:

| Field | Purpose |
|---|---|
| `job_id` | Unique identifier (must be unique across all jobs) |
| `order` | Global ordering number across all data sources (determines processing sequence) |
| `data_source` | Which data source this job belongs to (e.g., `rosa-kcs`, `ops-sop`, `openshift-docs-md`) |
| `files` | List of file paths to process (stored as `data_source/relative_path`) |
| `file_count` | Number of files in the job |
| `total_characters` | Total character count of all files in the job (used for batching decisions) |
| `status` | Current state: pending, in_progress, completed, failed |
| `created_at` | ISO timestamp of job creation |
| `started_at` | ISO timestamp when a worker claimed the job (null when pending) |
| `completed_at` | ISO timestamp of completion or failure (null while in progress) |
| `agent_instance_id` | ID of the worker that claimed this job (null when pending) |
| `attempt` | Number of times this job has been attempted (incremented on each claim) |
| `error_message` | Error details if failed (null otherwise) |

---

## Job Generation

### Batching Strategy (k-extract)

Batching is a **runtime concern** — determined at `k-extract run` time based on the actual files and their sizes, not at `init` time.

**Context-window-based batching:** The available budget for source material in each job is derived from the model's actual context window:

```
available_tokens = context_window - prompt_overhead - output_reservation - safety_margin
```

Where:
- `context_window` — obtained at runtime from the Claude Agent SDK's `ResultMessage.model_usage[model_name]["contextWindow"]`. This works regardless of inference provider (direct API, Vertex AI, Bedrock). Example: `{'claude-sonnet-4-6@default': {'contextWindow': 200000, 'maxOutputTokens': 32000, ...}}`
- `prompt_overhead` — estimated from the actual system prompt + ontology schema + job instructions (known at runtime from the config file)
- `output_reservation` — derived from `model_usage[model_name]["maxOutputTokens"]`
- `safety_margin` — buffer to avoid hitting limits

Source file token count is estimated from character count (chars / ~4 for English text).

The `contextWindow` and `maxOutputTokens` values can be captured from a lightweight initial agent query (e.g., during the data inventory step) and reused for all subsequent batching calculations.

Jobs are filled with source files until cumulative estimated tokens approach `available_tokens`.

**Oversized files:** If a single file exceeds the available token budget, it gets its own job. The model can manage large files — it knows how to do partial reads, navigate by section, etc. No truncation or skipping.

**Folder-aware grouping:** Files are grouped by parent directory before batching. Files in the same directory are likely related, giving the agent better local context for discovering relationships between them.

**No magic numbers:** The batching adapts automatically to whatever model the user configures and whatever data they point it at. No empirically-tuned multipliers.

### Historical context (superseded)

The original system went through two batching strategies:

- **V1: Fixed file count** — Every job contained exactly 5 files. This created wildly uneven workloads because file sizes vary dramatically.
- **V2: Character-based threshold** — Used `average_file_size * 6.8` as a threshold. Better, but the 6.8 multiplier was an undocumented magic number tuned for OpenShift docs. Also added folder-aware grouping (retained in k-extract).

The context-window-based approach generalizes the V2 insight (normalize by content size, not file count) while grounding it in an actual constraint (the model's context window) instead of an empirical constant.

---

## Job Claiming (Atomic Claim Mechanism)

Job claiming must be atomic. A worker requests the next available pending job; the system atomically transitions it to in_progress and assigns the worker ID. If no jobs are available, the claim returns nothing.

Two approaches were used in the original system:

1. **Lock-based claiming**: Workers compete for jobs from a shared queue. A mutual-exclusion lock is acquired before reading the pending job list. While holding the lock, the system selects the next job by sort order, transitions it to in_progress with the worker's ID and a timestamp, and releases the lock. If the lock cannot be acquired within a timeout, the claim fails gracefully and the worker can retry. The same lock protects completion and failure recording.

2. **Round-based pre-assignment**: The orchestrator pre-assigns jobs to workers in rounds. All pending jobs are sorted by order and divided into groups sized to the worker count. Each job in a round is assigned to a specific worker and transitioned to in_progress before the worker launches. This eliminates contention but means workers cannot pick up extra work if they finish early.

---

## Job Completion and Failure Recording

On success, the job transitions to completed with a `completed_at` timestamp. The worker's staged changes are validated and committed to the shared store before the job is marked complete.

On failure, the job transitions to failed with error details in `error_message`, a `completed_at` timestamp, and an incremented `attempt` counter. Other workers are unaffected by a single job's failure.

In both cases, completion and failure metadata (timestamps, error messages, attempt counts) is persisted in the job record itself.

---

## Stale Job Detection and Recovery

Jobs stuck in in_progress (due to crashes, timeouts, or interrupted runs) must be detected and recovered:

- **Timeout-based detection**: Jobs whose `started_at` timestamp is older than a configurable timeout (e.g., 60 minutes) are reset to pending, clearing their worker assignment and start time.
- **Unconditional reset at startup**: All in_progress jobs are reset to pending at the start of each extraction run. This is appropriate when each run is expected to start fresh; any job still marked in_progress from a previous run is considered stale.

Both approaches reset the job's status to pending and clear the `started_at` and `agent_instance_id` fields.

### Resumability

Completed jobs are skipped on resume. When `k-extract run` is invoked against a previous run with a matching environment fingerprint (see [Extraction Pipeline — Resumability](extraction-pipeline.md#resumability-and-environment-fingerprinting)), only pending and failed jobs are processed. JSONL output is appended to the existing output file.
