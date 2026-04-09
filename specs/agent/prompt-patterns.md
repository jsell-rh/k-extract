# Prompt Patterns Spec

Distilled from `kartograph-extraction` codebase. Captures the structure and composition patterns of prompts for reimplementation with dynamically generated prompts based on user-defined ontologies.

---

## 1. Two-Layer Prompt Architecture

The system uses a **two-layer prompt architecture**:

1. **System prompt** — Minimal, static. Sets identity, access permissions, available tools, efficiency rules, and completion instructions. Delivered via the SDK's system prompt configuration.
2. **Job description** — Detailed, generated per-instance. Written to the agent's workspace. The agent reads this file as its first action (directed by the initial user message).

The system prompt explicitly tells the agent to read the job description. The initial user message reinforces this by directing the agent to the job description and telling it to begin processing.

**Why two layers:** The system prompt is constrained by SDK configuration and is the same across similar agent types. The job description is generated dynamically per instance with job-specific content (file lists, data source details, workflow steps).

**Generalizable requirement:** Keep system prompts minimal and role-defining. Put job-specific instructions in a readable file the agent accesses at runtime.

---

## 2. System Prompt Structure

### Sections (Worker)

| Section | Content | Generalizable? |
|---------|---------|----------------|
| **Identity header** | Task name + agent instance identity | Yes — name the task and establish agent identity |
| **Task** | Read and execute the job description | Yes — always point to the job description |
| **Access Permissions** | READ paths listed, NO WRITE access explained, rationale given | Yes — enumerate what agent can/cannot touch |
| **Available CLI Tools** | Numbered list of tools with one-line descriptions | Yes — enumerate tools briefly |
| **Efficiency Rules** | "Don't narrate", "don't explain reasoning", "just execute" | Yes — universal for autonomous agents |
| **Completion** | How to signal completion (run the commit tool) | Yes — always specify termination protocol |

### Efficiency Rules (Universal)

These behavioral constraints appear in all system prompts and are not domain-specific:

- "You're working autonomously — no human is watching"
- "Don't narrate what you're doing"
- "Don't explain your reasoning in messages"
- "Don't summarize when done"
- "Just execute the job"
- "Think of it like batch processing: read instructions, execute quietly, signal completion"

**Generalizable requirement:** Autonomous agents waste tokens on narration unless explicitly told not to. These rules should be standard in all agent system prompts.

### V1 vs V2 Differences

V1 system prompts were terser ("Keep thoughts brief, 1 sentence max per action") and included access constraints inline. V2 expanded these into structured sections and moved detailed instructions to the job description.

---

## 3. Job Description Structure

The job description is the primary instruction document. It is generated programmatically and written to the instance workspace before the agent starts.

### Sections

| Section | Purpose | Data-source-specific? |
|---------|---------|----------------------|
| **# Title** | Task identity | No |
| **## Your Mission** | High-level context: what the knowledge graph is, multi-agent setup | Partially (mentions specific data sources) |
| **### Our Data Sources** | Lists all data sources with file counts | **Yes** (domain-specific) |
| **### Predetermined Ontology Schema** | Entity types organized by tier (structural vs file-based) with purposes and counts | **Yes** (specific entity types) |
| **### Intended Use** | How downstream consumers will use the knowledge graph | Partially |
| **## Your Assignment** | Instance-specific metadata table (job ID, instance, data source, file count, character count) | No (structure is universal) |
| **### About This Data Source** | Description of the specific data source this agent is processing | **Yes** |
| **### Files to Process** | Bullet list of file paths assigned to this instance | No |
| **## Your Scripts** | Lists tool scripts with brief descriptions + "do not write custom scripts" constraint | No |
| **## Your Workflow** | Step-by-step instructions (the most important section, emphasized repeatedly) | Partially |
| **## Completion Checklist** | Checkbox list summarizing all required steps | No |

### Workflow Steps (Pattern)

The workflow section follows a consistent pattern that is generalizable:

| Step | Pattern | Notes |
|------|---------|-------|
| **Step 1: Read instruction docs** | Read supplementary instruction documents | Universal: agent reads supplementary docs first |
| **Step 2a: Learn the entity type** | Run the search tool in type-definition mode to get schema | Universal: agent discovers schema dynamically |
| **Step 2b: Read the source file** | Read source file content | Universal: agent reads source material |
| **Step 2c: Process file** | Fill in properties using type definition + extraction guidance | Universal: agent enriches entity based on source |
| **Step 3: Repeat** | Loop for all files in job | Universal |
| **Step 4: Commit** | Run the commit tool | Universal |

**Key behavioral constraints embedded in workflow:**
- Process files **one at a time** (complete all steps for each file before moving to next)
- Set `processed_by_agent=true` **only when file is completely done**
- If a tool blocks on a lock, wait and retry

---

## 4. Ontology Schema Communication

### Dynamic Schema Discovery

The agent learns entity schemas **at runtime** by calling the search tool in type-definition mode. This returns:

- `description` — What this entity type represents
- `required_properties` — Properties the agent must set
- `optional_properties` — Properties the agent may set
- `property_definitions` — Per-property type, description, and constraints
- `property_defaults` — Default values for required properties
- `tag_definitions` — Allowed tag values with descriptions

**This is the core pattern for dynamic ontologies.** The agent does not need hardcoded knowledge of entity schemas. The job description tells the agent which entity type to query, and the search tool returns the full schema.

### Supplementary Extraction Guidance

In addition to the machine-readable schema, the system provides human-readable extraction guidance in supplementary instruction documents:

- **Common properties:** Table with guidance for shared properties (title, brief_summary, content_outline, tags, external_links, alert_names, cli_commands)
- **Property design:** Progressive detail system (title -> brief_summary -> content_outline -> full document)
- **Content_outline specification:** "Aim for ~50% of document length, keyword-rich, covering every substantive part"
- **Per-entity-type sections:** What to extract for each type, pre-populated vs agent-populated properties, example commands

**Generalizable requirement:** The prompt generator must produce:
1. A machine-readable schema (returned by search tools)
2. Human-readable extraction guidance (in the job description or supplementary docs)
3. Per-property guidance explaining what good extraction looks like

---

## 5. Source Document References

### How Files Are Referenced

- File paths in the job description point to the source data directory for reading.
- File paths in the ontology use a relative form without the data directory prefix.
- The job description lists all files to process as a bullet list.
- The agent reads source files using the `Read` tool.

### Cross-Reference Resolution

The job description and supplementary instructions contain detailed instructions for resolving URLs to entity slugs:

- **Pattern matching:** URL patterns are mapped to entity search strategies.
- **Creative resolution:** The agent is told that URL paths may not align literally with file paths and must use creativity to map them.
- **Search tip:** "Before finalizing a file, search the source for URL patterns to ensure every link is captured."

**This section is highly domain-specific.** The URL patterns, data source mappings, and resolution strategies are all domain-specific. However, the **pattern** is generalizable:

**Generalizable requirement:** The prompt generator must:
1. Define which URL patterns correspond to which data sources/entity types
2. Provide resolution strategies (how to map external URLs to internal entity slugs)
3. Instruct agents to systematically find and resolve all cross-references in source documents

---

## 6. Behavioral Constraints

### Universal Constraints (apply to all ontologies)

| Constraint | Where Specified | Purpose |
|------------|----------------|---------|
| Process files one at a time | Job description workflow | Ensures completeness before moving on |
| Do not create custom scripts | Job description "Your Scripts" section | Agents must use provided tools only |
| Do not create or modify files directly | System prompt, disallowed_tools | All mutations through tool scripts |
| Set processed_by_agent=true only when done | Job description, supplementary docs | Prevents premature completion marking |
| Search for existing entities before creating | System prompt + tool behavior | **Critical for deduplication.** Agents must use the search tool to check if an entity already exists before calling the create tool. The create tool also enforces this at the tool level (returns existing entity if slug matches), but the agent should search first to avoid unnecessary tool calls and to decide whether to create or update. The prompt must instruct: "Before creating any entity, search for it by slug. If it exists, use it. If it doesn't, create it." |
| Fix validation errors and re-run | Job description, commit tool docs | Retry-friendly completion |
| If tools block, wait and retry | Job description, system prompt | Handle lock contention |
| Don't narrate, don't summarize | System prompt efficiency rules | Token efficiency |

### Domain-Specific Constraints (do not carry forward)

| Constraint | Notes |
|------------|-------|
| File extension determines entity type | Data-source-specific mapping |
| Frontmatter URLs should not be added to external_links | Source-format-specific rule |
| URL pattern -> data source mapping rules | Domain-specific cross-reference resolution |
| Specific entity type names | Ontology-specific |
| Specific tag values and their meanings | Ontology-specific |

---

## 7. Aggregator Prompt Pattern

The aggregator receives a different job description generated from the round report:

### Sections

| Section | Content |
|---------|---------|
| **Your role** | Post-worker coherence review |
| **Pre-processed report** | Slugs edited per worker instance; overlapping slugs |
| **What to do** | 1. Read worker workspaces, 2. Alignment, 3. Refine via tools, 4. Commit |

### Aggregator System Prompt

Similar to worker but with:
- Identity: "You are the Aggregator instance"
- Access: Can read ALL worker workspaces (not just its own)
- Completion: Same commit flow but without the "all job files processed" check

**Generalizable requirement:** When using multiple parallel workers, an aggregator pass may be needed for cross-instance coherence. The aggregator prompt should include: what each worker did, where conflicts might exist, and what to check for.

---

## 8. What the Prompt Generator Must Produce

For dynamic ontologies, the prompt generator needs to produce these components:

### System Prompt (Template, Mostly Static)

1. Task identity and instance ID
2. Path to job description file
3. Access permissions (read paths, write restrictions)
4. Tool list (brief)
5. Efficiency rules (no narration, no summarization)
6. Completion protocol

### Job Description (Generated Per-Instance)

1. **Mission context:** What the knowledge graph is for, multi-agent setup
2. **Ontology overview:** Entity types by tier (structural vs agent-editable), relationship types, counts
3. **Assignment metadata:** Instance ID, job ID, data source, file list with count and size
4. **Data source description:** What this data source contains (generated from ontology metadata)
5. **Workflow steps:** Schema discovery -> read source -> extract and enrich -> repeat -> commit
6. **Extraction guidance:** Per-property extraction instructions, cross-reference resolution rules
7. **Completion checklist:** Summary of all required steps

### Supplementary Docs (Referenced by Job Description)

1. **What to extract:** Per-entity-type extraction guidance, common property definitions, relationship rules, cross-reference patterns
2. **How to use tools:** Tool documentation with modes, arguments, examples, and caps/warnings
