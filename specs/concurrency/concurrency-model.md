# Concurrency Model Spec

Describes concurrency requirements in terms of what must be atomic, what can be parallel, and what isolation guarantees are needed. The new system will use SQLite rather than file-based locking.

## 1. Resources Requiring Serialized Access

### 1.1 Shared Ontology Store (Entity + Relationship)

The shared ontology store is the knowledge graph that all workers contribute to. It consists of two logical stores:
- **Entity store** -- entity type definitions and all entity instances
- **Relationship store** -- relationship type definitions and all relationship instances

**Requirement:** These two stores must be treated as a single atomic unit for writes. Reading one while the other is being modified can produce inconsistent cross-references (e.g., a relationship referencing an entity that was just deleted or renamed).

### 1.2 Job Queue State

Jobs transition through states: `pending` -> `in_progress` -> `completed` | `failed`.

**Requirement:** Claiming a job (pending -> in_progress) must be atomic. Two workers must never claim the same job.

### 1.3 Private Staging Area (Per-Worker)

Each worker has its own private staging area where it accumulates changes before committing them to the shared ontology store.

**Requirement:** The staging area is worker-private. No serialization needed between workers for their own staging areas.

## 2. Access Semantics

### 2.1 Ontology Access: Shared vs. Exclusive

The system implements two access modes for the ontology store:

| Operation | Access Type | Allows Concurrent... |
|---|---|---|
| Reading ontology (search, validation planning) | Shared (read) | Other shared readers |
| Writing ontology (committing edits) | Exclusive (write) | Nothing -- blocks all readers and writers |

**Requirement for new system:**
- Multiple workers must be able to read the ontology simultaneously
- Only one worker at a time may modify the ontology
- A write must block all reads (to prevent reading partially-applied changes)
- Both entity and relationship stores must be locked together (see section 5)

### 2.2 Job Queue Access: Exclusive Only

All job queue mutations (claim, complete, fail) require exclusive access. There is no shared-read mode for job operations.

**Requirement for new system:**
- `claim_next_job()` must be atomic: read the next pending job, mark it in_progress, and assign the worker ID in a single transaction
- `mark_completed()` and `mark_failed()` must be atomic
- Job state reads (statistics, listing) do not need locking if the database provides snapshot isolation

## 3. Atomic Job Claiming

The `get_next_job(worker_id)` operation must:

1. Acquire exclusive access to the job queue
2. Find the first job in `pending` state (ordered by `order` field)
3. Update the job's status to `in_progress`
4. Set `started_at` timestamp
5. Set `worker_id` to the claiming worker
6. Increment `attempt` counter
7. Release exclusive access

**What must be atomic:** Steps 2-6 must happen within a single transaction. If the process crashes between finding and claiming, no job should be lost (the job stays pending).

**SQLite mapping:** This maps directly to a single UPDATE statement with a WHERE clause:
```sql
UPDATE jobs SET status='in_progress', started_at=NOW(), worker_id=?, attempt=attempt+1
WHERE id = (SELECT id FROM jobs WHERE status='pending' ORDER BY "order" LIMIT 1)
RETURNING *;
```

## 4. Staging Area Isolation Model

### The pattern

Each worker operates in isolation using a "stage then commit" model:

1. **Read phase:** Worker reads the shared ontology store (under shared access) to understand current state
2. **Work phase:** Worker processes its assigned files and accumulates changes in its private staging area. This contains entity instances and relationship instances to create or update.
3. **Commit phase:** Worker runs a validate-and-commit operation which:
   a. Acquires exclusive access to both ontology stores
   b. Re-reads the shared ontology (it may have changed since the read phase)
   c. Merges staged edits into a "virtual" copy of the shared state
   d. Validates the merged result (structural integrity, slug uniqueness, cross-references)
   e. If valid: writes the merged result and releases exclusive access
   f. If invalid: releases exclusive access and reports errors (worker must fix and retry)

### Merge semantics

- **Upsert by slug:** If a staged entity has the same slug as an existing entity of the same type, the staged version replaces it
- **Upsert by (source_slug, target_slug):** Relationships are matched by their endpoint pair within a composite key
- **No deletes:** The staging system only supports creates and updates, not deletions

### What must be atomic

The validate-and-commit operation must hold exclusive access from the moment it reads the shared state through the moment it writes the updated state. If a concurrent writer modifies the shared state between read and write, the validation would be stale and the commit could corrupt data.

### What can be parallel

- Multiple workers can read the shared ontology simultaneously (for search, planning)
- Multiple workers can write to their own private staging areas independently
- The work phase (file analysis, entity extraction) is fully parallelizable
- Only the commit phase requires serialization

## 5. The Validate-and-Commit Serialization Point

This is the critical section. The validate-and-commit operation performs:

1. **Acquire exclusive access to both ontology stores** -- entity and relationship are treated as a single critical section to prevent partial reads during cross-validation
2. **Read current shared state**
3. **Load worker's staged edits** from the worker's private staging area
4. **Build virtual merged view** -- create deep copies and merge
5. **Validate the merged result:**
   - Slug uniqueness across entity types (a slug cannot appear in two different entity types)
   - Required properties present on all entities
   - Tag values match allowed tag definitions
   - Relationship endpoints reference existing entities (cross-store validation)
   - Property-to-relationship consistency checks
   - All job files have been processed
   - Structural relationships not modified
6. **If valid:** Write the merged result to the shared store
7. **Release exclusive access**

**Requirement:** Steps 1-7 must be serialized. No other reader or writer may access the shared ontology during this window.

**Important cross-store access note:** The entity and relationship stores must be treated as a single critical section. If exclusive access to the entity store is released before acquiring access to the relationship store, another worker could modify the entity store, making validation data stale and causing data loss. The new system must treat entity+relationship validation as a single critical section, not two independent ones.

## 6. Stale Lock / Stale Job Detection and Recovery

### Stale jobs

Jobs stuck in `in_progress` are detected by timestamp:
- A stale job scanner checks all in-progress jobs against a configurable timeout (default: 60 minutes)
- If `started_at` is older than the timeout, the job is moved back to `pending`
- The job's worker assignment and `started_at` are cleared
- The `attempt` counter is preserved (it was already incremented when claimed)

**Requirement:** The new system needs:
- A configurable timeout for stale job detection (default: 60 minutes)
- Automatic reset of stale jobs at the start of each extraction run
- A CLI command to reset a specific job

### Stale locks

The current system uses advisory locks that are automatically released when the process holding them exits or crashes. There is no explicit stale lock detection.

**Requirement for SQLite:** If using WAL mode, SQLite handles this natively. If implementing application-level locks (e.g., a locks table), the system must handle:
- Process crash: lock must be released (use database-level mechanisms, not application-level timeouts)
- Graceful timeout: configurable wait period before giving up on lock acquisition (current defaults: 30s for ontology, 10s for job operations)

## 7. Gaps to Address in New System

### 7.1 Read-then-commit race window

During the work phase, a worker reads the shared ontology to understand current entities and relationships. It then works for an extended period (minutes) before committing. During that time, other workers may have committed changes that make the worker's assumptions stale. The current system handles this by re-reading at commit time and validating, but it does not detect logical conflicts (e.g., two workers both deciding to create a relationship to the same target based on the same source data).

**Gap:** No conflict detection beyond structural validation. Two workers could create semantically redundant entities if they both process related content independently.

### 7.2 No retry on commit validation failure

When validate-and-commit fails, it exits with an error. The worker must manually fix the staged edits and retry. There is no automatic retry or conflict resolution.

**Gap:** The new system should consider automatic retry with re-read of shared state, at least for transient conflicts.

### 7.3 Per-operation locking overhead

In the original system, every individual operation (each entity edit, each search query) acquires and releases exclusive access to the shared ontology. This is because the virtual ontology view needs to read the shared state to overlay staged edits.

**Gap:** This creates a lock-per-operation pattern that serializes all operations, even searches. The new system should consider:
- Caching the shared state within a worker's session
- Only reading shared state at session start and at commit time
- Or using SQLite's snapshot isolation so reads don't block writes

### 7.4 Non-atomic state transitions (original system)

In the original system, job state transitions were implemented by writing a new file to a target directory and then deleting from the source directory. If the process crashed between write and delete, a job could exist in two states.

**Gap:** SQLite's transactional updates eliminate this. A single UPDATE statement atomically changes the status column.

### 7.5 No enforced lock ordering

In the original system, different operations acquired exclusive access to entity and relationship stores in different patterns. While the codebase appeared consistent (entity before relationship), there was no enforced ordering.

**Gap:** The new system should use a single database transaction rather than multiple ordered locks, eliminating the possibility of deadlock entirely.

## 8. Timeout and Retry Behavior

### Timeouts

| Resource | Default Timeout |
|---|---|
| Ontology (shared or exclusive) | 30 seconds |
| Job operations | 10 seconds |

On timeout, the operation fails. The caller logs a warning and typically returns `None` (for job claiming) or exits with an error (for ontology operations).

### Job-level retry

- On agent processing failure, the job is marked as `failed` with an `error_message`
- The `attempt` counter tracks how many times the job has been tried
- There is no automatic retry of failed jobs -- they must be manually reset via a CLI command
- Stale jobs (stuck in_progress) are automatically reset to pending at the start of each extraction run

### Partition creation retry

- The partition creation agent retries up to 3 times per data source
- On validation failure, error feedback is passed back to the agent for the next attempt
- Each data source's partition agent runs independently in parallel

## 9. Worker Coordination Model (V2)

### Round-based execution

V2 processes jobs in rounds:
1. Load all pending jobs, cap at `max_jobs`
2. Calculate number of rounds: `ceil(total_jobs / num_worker_instances)`
3. Per round:
   a. Prepare worker instance directories (reset private staging area to empty)
   b. Assign each job in the batch to a worker from a fixed pool
   c. Launch all N workers as parallel subprocesses
   d. Wait for all workers to complete
   e. Optionally run an aggregator to detect overlapping edits across workers

### Worker isolation

Each worker:
- Has its own workspace directory
- Writes to its own private staging area
- Has its own job description and transcript log
- Commits to the shared ontology via validate-and-commit (which serializes via exclusive access)

### Aggregator pattern (currently disabled)

The codebase includes an aggregator concept:
- After each round, a report is generated summarizing slugs edited per instance and overlaps
- An aggregator instance would review and resolve conflicts
- This is currently disabled ("Queue-only mode: no aggregator instance")

**Lesson for new system:** The overlap detection infrastructure exists but was not put into active use, suggesting that in practice, the per-job partitioning is sufficient to avoid most conflicts. The new system should still track which worker modified which entities, to enable conflict detection if needed.
