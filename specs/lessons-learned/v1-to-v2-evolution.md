# Lessons Learned: V1 to V2 Evolution

Evidence-based analysis of what changed, what broke, and what the next implementation should learn.

---

## 1. Batching Strategy

### V1: Fixed file-count batching

V1 used a constant of 5 files per job. Jobs were created by slicing partition file lists into fixed-size chunks:

```python
for i in range(0, len(files_in_partition), FILES_PER_JOB):
    job_files = files_in_partition[i:i + FILES_PER_JOB]
```

This is simple but ignores file size variance. A job with 5 tiny files and a job with 5 enormous files would receive the same time/token budget despite dramatically different workloads.

### V2: Character-based batching

V2 calculates a per-source character threshold:

```python
threshold = int(avg_size * 6.8)
```

Files are accumulated into a batch until adding the next file would exceed the threshold. The threshold must also be >= the largest file size to ensure no single file is unbatchable. Jobs track `total_characters` and `character_threshold` in their metadata.

### Evidence for why

The V2 job data shows consistent character counts across jobs (roughly 18,000-22,000 chars per batch in one data source), which suggests the goal was **uniform token cost per job**. Since LLM cost scales with input tokens (not file count), character-based batching directly targets cost predictability. The multiplier appears empirically tuned -- no rationale is documented.

### Trade-offs

- **Pro**: More predictable per-job cost and completion time; prevents pathological cases where one agent gets a much larger workload than another.
- **Con**: Jobs within the same folder can have wildly different file counts (1 large file vs 12 small files), which may affect extraction quality if the agent's approach varies by file count.
- **Lesson for next implementation**: Character-based batching is clearly superior for cost control. Consider also tracking estimated token count (chars / ~4) directly. The multiplier should be documented with its derivation.

---

## 2. Locking Evolution

### Phase 1: No locking

Before commit `f9530c3` ("instance_0{i} + acquire lock on master_ontology.json", 2025-12-22), there was no file-level locking. Multiple agent instances could write to the same ontology files concurrently. Commit `65b0f63` ("concurrency overwrite issue fixed", 2026-01-08) explicitly names the problem in its message.

### Phase 2: Exclusive-only locking (f9530c3)

The first lock implementation used **exclusive locks only**. Every access -- read or write -- took an exclusive lock. This created serialization: agents that only needed to read the ontology (for search/validation) blocked each other and blocked writers.

### Phase 3: The FIX? sequence (locking was still broken)

A sequence of four commits over ~4 hours tells the story of a painful debugging session:

1. **`1a94400`** "FIX?" (2026-01-12 22:02) -- Removed 111 lines, stripping out partial-apply logic, suggesting the "apply valid items, retry errors" pattern was itself causing corruption or deadlocks.

2. **`29d20b7`** "FIX??" (2026-01-12 23:11) -- More changes. Still not confident it was fixed.

3. **`b493d09`** "FIX???? Locks no longer cause problems?" (2026-01-13 01:42) -- Added validation improvements. The escalating question marks in the commit messages show genuine uncertainty.

4. **`48d3099`** "actual read/write lock fix?" (2026-01-13 02:33) -- This is the commit that introduced the shared/exclusive lock distinction. 93 files changed, 815 insertions. This was the real fix.

### Phase 4: Current state (shared/exclusive model)

The final implementation supports:
- Shared read locks (allow concurrent reads)
- Exclusive write locks (block all other access)

The V2 architecture further mitigated locking pressure by introducing a per-instance private staging area -- agents write to their own local staging area and only touch the shared ontology at commit time via validate-and-commit.

### Lessons for next implementation

- **Never ship concurrent writes without locking from day one.** The "concurrency overwrite issue" was entirely predictable.
- **Shared/exclusive (reader-writer) locks are the minimum viable concurrency model** for a shared data store. Exclusive-only locks create unnecessary serialization.
- **The "apply valid items, retry errors" pattern was abandoned** (removed in `1a94400`). Partial application to a shared store under concurrent access is inherently fragile. V2's approach of staging everything locally and committing atomically is much safer.
- **The private staging area architecture is the real solution** -- it eliminates most lock contention by deferring shared-state writes to a single atomic commit per job.

---

## 3. Scenario Entity Removal

### What was Scenario?

Scenario was a 7th EntityType (Tier 3) that mapped operational questions to sets of files an SRE would need to read. It had `must_inspect` and `may_inspect` lists of file slugs, plus tags from a fixed set (alert, procedure, troubleshooting, etc.).

Commits `1f6da8a` ("Scenario EntityType"), `f1a9dc2` ("Scenario added to entity sp"), `09e4bf2` ("scenario_entitytype"), and `4a47692` ("Scenarioproperty -- validation requires these to match by slug") show it was actively developed.

### Evidence it was removed from the worker path

The V2 job description generation has an explicit comment:

```python
# Build the content using format() to avoid f-string escaping issues (6 entity types: 2 structural, 4 file-based; no Scenario)
```

The generated job description states:

> "No Scenario entity; discovery is via structure, metadata, and REFERENCES."

The git diff shows the old text referenced "7 EntityTypes" with a Tier 3 section for Scenario. The new text says "6 EntityTypes" with no Tier 3.

### What replaced it

File-to-file **REFERENCES** relationships replaced Scenario as the primary discovery mechanism. The V2 job description instructs agents to create REFERENCES relationships for every in-source URL, and to populate rich metadata (content_outline, tags, alert_names, cli_commands) that enables direct search without a Scenario intermediary.

The V2 agent instructions confirm this: search uses `REFERENCES` / `REFERENCED_BY` relationships, folder structure (`CONTAINS`), and metadata filtering (tags, alert_names, cli_commands, content_outline) rather than Scenario lookup.

### Scenario still exists in the aggregator path

Critically, Scenario was **not fully removed**. The aggregator job description template still references Scenario alignment as the aggregator's main job. The aggregator prompt still says "Scenario alignment and refinement." This is dead code because `USE_AGGREGATOR = False`.

### Why was it removed? (inference from evidence)

1. **Complexity**: Scenario required cross-file reasoning (which files answer a given question), which is hard for a single-job agent that only sees 5-9 files.
2. **Validation overhead**: Validation required `must_inspect`/`may_inspect` slug lists to match the actual MUST_INSPECT/MAY_INSPECT relationships -- dual bookkeeping that was error-prone.
3. **Aggregator dependency**: Scenario coherence required the aggregator pass (to align Scenarios across workers). With the aggregator disabled, Scenario became unworkable.
4. **REFERENCES proved sufficient**: For the downstream use case (SRE question answering), rich metadata + direct file-to-file links may have been enough.

### Lessons for next implementation

- **Scenario-like constructs (question-to-document mappings) are valuable but hard to build incrementally** in a parallel extraction pipeline. They require global knowledge that no single worker has.
- **If you need cross-document reasoning, do it as a separate post-processing pass**, not inline with per-file extraction.
- **The replacement (REFERENCES + rich metadata + folder structure) is simpler** and may be sufficient. Evaluate whether the query-time agent can assemble "which files to read" dynamically rather than pre-computing it.

---

## 4. Aggregator Pattern

### What it was supposed to do

The aggregator was designed to run after each worker round:

1. Read all worker workspaces (staged edits, agent transcripts)
2. Detect conflicts (slugs edited by multiple workers, entities split across instances)
3. Ensure Scenario coverage is complete and coherent
4. Stage its own fixes via the same CLI tools, then validate-and-commit

The orchestrator builds a report summarizing slugs edited per instance and overlaps.

### Why it is disabled

The aggregator was never fully implemented. Remaining tasks included:
- Worker job_description layout alignment
- Fixed pool instance assignment
- "Run aggregator after all N complete" wiring
- Pre-processing step to build the report
- Documenting "when to create a Scenario"
- Creating the aggregator instance setup

### What remains incomplete

- Aggregator job description generation exists but still references Scenario alignment as the core task
- Aggregator invocation and workspace cleanup functions exist
- Report generation for overlap analysis exists
- **But**: the worker path no longer creates Scenarios, so the aggregator has nothing to aggregate/align for its stated purpose

### Lessons for next implementation

- **Build the aggregator only after the worker path is stable.** The remaining TODOs show it was designed in parallel with the worker path but never reached completion.
- **If the aggregator's primary purpose (Scenario alignment) is removed, re-evaluate whether you need one at all.** The current codebase has dead aggregator code that references a removed entity type.
- **Conflict detection (overlaps) is still valuable** even without Scenarios. If workers can edit the same entities, an aggregator or merge step is needed. The V2 architecture partially sidesteps this by having each worker commit atomically, but doesn't handle semantic conflicts (two workers extracting different metadata for the same entity).

---

## 5. Agent Instruction Evolution

### V1 agent instructions

The V1 instructions target a **query-time agent** (not extraction). Key characteristics:
- Uses a Cypher graph database (Apache AGE)
- Has `query_graph` and `fetch_documentation_source` MCP tools
- References V1 entity types: `DocumentationModule`, `KCSArticle`, `SOPFile`
- Multi-step search workflow: Initial Discovery -> Deep Exploration -> Fetch Full Documentation -> Synthesize and Cite
- Prescriptive search priority: SOPFile -> KCSArticle -> DocumentationModule
- Ground rules: cite sources via `view_uri`, explore before answering, acknowledge gaps

### V2 agent instructions

The V2 instructions target the **same query-time use case** but with a different schema. Key changes:
- Entity types changed: `DocumentationModule` -> `ProductFile`, `KCSArticle` -> `KCSFile`, `SOPFile` -> `SREFile` + `SREScript`
- New `get_file_contents(identifier)` tool replaces `fetch_documentation_source`
- Identifiers can be slug or file_path (V1 only used view_uri)
- Added `REFERENCES` / `REFERENCED_BY` relationships (not present in V1)
- Added `CONTAINS` relationship (Folder -> File)
- Full folder tree included in instructions (with file counts per folder)
- Removed the prescriptive search priority order (SOPFile first)
- Removed the "SRE Tools Only" bias from response generation
- Added `tags`, `alert_names`, `cli_commands` as searchable filter properties
- Spectrum of detail concept: title -> brief_summary -> content_outline -> get_file_contents()

### V2 extraction prompts (system prompt for extraction agents)

The V2 extraction system prompt is notably minimal compared to V1's. V1's extraction prompt was 18 lines; V2's is 69 lines but most is formatting. The key addition in V2:

- **Efficiency rules**: "Don't narrate what you're doing / Don't explain your reasoning / Don't summarize when done / Just execute the job". This was not in V1.
- **No Write/Edit tools**: V2 explicitly disables Write and Edit tools, forcing agents to use only the CLI scripts. V1 allowed Write and Edit.
- **Detailed job description**: V1 put instructions in a general-purpose template (~540 lines). V2 generates a more focused job description (~225 lines) with clear per-file workflow and explicit REFERENCES extraction.

### V1 extraction job description

V1's job description was a general-purpose template with 7 steps:
1. Phase 1: List All Entities, Relationships & New Types
2. Validate
3. Generate Template (super_staging.json)
4. Phase 2: Fill Details
5. Validate Before Processing
6. Process Batch
7. Repeat or Complete

The agent was expected to **create new entity types and relationship types** on the fly ("EXHAUSTIVE extraction" mindset, "If concepts don't fit existing EntityTypes, CREATE NEW TYPES"). It used `super_staging.json` as an intermediate format.

### V2 extraction job description

V2's job description is focused and constrained:
1. Read instruction docs
2. Per file: Learn entity type, Read file, Process file (metadata + REFERENCES)
3. Repeat for all files
4. validate_and_commit

The agent **cannot create new entity types** -- the 6 types are predetermined. It enriches pre-populated instances with metadata. No intermediary staging file.

### Lessons for next implementation

- **Constrain the agent**: V1's "create new types freely" led to schema sprawl. V2's fixed schema with enrichment-only is much more controlled.
- **Disable file write tools**: Forcing agents through CLI scripts prevents them from corrupting data files directly.
- **Efficiency rules matter**: Telling the agent not to narrate reduces token waste significantly in autonomous operation.
- **Move complexity to the job description, not the system prompt**: Both versions do this, but V2 is more disciplined about it.

---

## 6. Job Generation Changes

### V1 job generation

- Input: Partition files (manually curated file subsets)
- Batching: Fixed 5 files per job
- Partitions were created by a separate partition step
- Jobs ordered by data source order (configurable)
- Job claiming: atomic state transition (pending -> in_progress)

### V2 job generation

- Input: Entity ontology instances (files that have corresponding entity instances)
- Batching: Character-based threshold (average size * multiplier)
- Files grouped by folder, then batched within each folder
- No partition step -- files come directly from the ontology
- Same job status lifecycle (pending -> in_progress -> completed/failed)
- Jobs include `total_characters` and `character_threshold` metadata

### Key differences

1. **No partition step in V2**: V1 required a manual/semi-automated partition step to divide files into subsets. V2 derives the file list from the ontology (which has pre-populated instances for all files), eliminating a whole workflow step.
2. **Character-based vs file-count batching**: As discussed in section 1.
3. **Folder awareness**: V2 groups by folder before batching, so files in the same directory tend to land in the same job. V1 batched within partition subsets, which might or might not correlate with folder structure.
4. **Evidence of scale**: 319 completed jobs are visible in the repository, all using V2 format (with `total_characters`, `character_threshold`).

### Lessons for next implementation

- **Derive job inputs from the ontology, not manual partitions.** This eliminates a fragile manual step and ensures only files with ontology instances are processed.
- **Include content size metadata in jobs.** `total_characters` and `character_threshold` enable cost estimation and workload balancing.
- **Group by folder for locality.** Files in the same directory are likely related, which helps agents build context.

---

## 7. Anti-patterns Discovered

### 7a. Partial application of batch edits (abandoned)

V1 had logic that tried to apply valid items from a batch even when some items had errors. This was removed in commit `1a94400` ("FIX?"). The pattern is dangerous because:
- Partial writes under concurrent access can leave the ontology in an inconsistent state
- It's hard to reason about what was applied and what wasn't
- V2 replaced this with atomic validate-then-commit: all staged edits pass validation or none are applied

### 7b. Agent-created entity types (abandoned)

V1 encouraged agents to create new EntityTypes on the fly. This led to schema sprawl -- every agent could invent types. V2 uses a fixed 6-type schema. Commit `9c78554` ("remove ontology_design + smaller sys_prompt") shows the pivot away from this.

### 7c. Exclusive-only locking (replaced)

As detailed in section 2, the initial locking implementation used exclusive locks for all operations. This serialized reads unnecessarily and was replaced with shared/exclusive locks after a painful debugging session.

### 7d. Direct ontology file editing by agents (blocked)

V1 allowed agents Write and Edit tool access. V2 disables Write and Edit, forcing agents to use only the CLI scripts. This prevents agents from bypassing validation, locking, and the staging area architecture.

### 7e. Intermediary staging file (simplified away)

V1 had a multi-step process: write a plan -> validate -> generate an intermediary staging template -> fill in details -> validate -> batch process. V2 eliminated this pipeline: agents use CLI scripts that write directly to the private staging area, then validate-and-commit applies everything atomically. The intermediary file was a source of errors (JSON formatting issues, empty fields, forgotten relationship types).

### 7f. Growing instance IDs (replaced with fixed pool)

The TODO notes say: "Ensure the orchestrator assigns jobs to a fixed pool (e.g. instance_01..instance_10) per round (round-robin or one job per instance), not 'next available id' that grows." This was implemented: the orchestrator uses modular arithmetic to assign instance IDs from a fixed pool. Growing IDs would create unbounded workspace directories.

### 7g. Scenario as inline extraction task (deferred/removed)

As detailed in section 3, asking per-file extraction agents to maintain a global Scenario index was an anti-pattern: they lack the global view needed to do it well. The TODO and aggregator code show this was recognized and partially addressed (aggregator for Scenario alignment), but the aggregator was never completed, and Scenario was removed from the worker path.

---

## Summary: What the next implementation should learn

| # | Lesson | Evidence |
|---|--------|----------|
| 1 | Batch by content size, not file count | V2 character threshold vs V1 fixed file count of 5 |
| 2 | Use shared/exclusive locks from day one | FIX? commit sequence (`1a94400` -> `48d3099`) |
| 3 | Stage locally, commit atomically | V2 private staging area + validate-and-commit architecture |
| 4 | Fix the schema before extraction, not during | V1 allowed dynamic type creation; V2 uses 6 fixed types |
| 5 | Block direct file writes by agents | V2 disables Write and Edit tools |
| 6 | Cross-document constructs (Scenarios) need a dedicated pass | Scenario removed from worker path; aggregator never completed |
| 7 | Derive job inputs from the ontology, not manual partitions | V2 reads entity instances; V1 required manual partition step |
| 8 | Minimize agent verbosity in autonomous mode | V2 efficiency rules: "Don't narrate / Don't explain / Just execute" |
| 9 | Keep the intermediary format count low | V1 had plan -> staging template -> batch process; V2 has CLI -> staging area -> commit |
| 10 | Use a fixed pool of instance IDs | Implemented via modular arithmetic in the orchestrator |
