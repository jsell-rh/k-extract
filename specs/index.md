# k-extract Specs Index

Specification documents extracted from the [kartograph-extraction](https://github.com/aredenba-rh/kartograph-extraction) codebase (2026-04-07). These specs capture the essential complexity of the system — domain knowledge, process design, and lessons learned — without prescribing implementation details.

**Scope:** V2 codebase is canonical. V1 is referenced only for lessons learned. All specs reflect what was actually found in the codebase — no assumptions or gap-filling.

**Intent:** These specs, combined with the technology decisions, will guide a cleaner reimplementation as a general-purpose knowledge graph extraction framework (`k-extract`).

---

## Domain Model

- [Domain Model](domain/domain-model.md) — Ontology structure, entity/relationship models, validation rules, and CRUD operations. Captures the patterns (type definitions, slug identification, property validation, pending-edits staging) independent of any specific ontology content.

## Process

- [Guided Session](process/guided-session.md) — The `k-extract init` interactive flow: problem statement, data inventory, AI-assisted ontology proposal, config file output. The design phase that precedes extraction.
- [Job Lifecycle](process/job-lifecycle.md) — Job states and transitions, job data model, generation from partitions, atomic claiming, completion/failure recording, and stale job recovery. Documents the V1→V2 shift from file-count to character-based batching.
- [Extraction Pipeline](process/extraction-pipeline.md) — End-to-end flow from data source through partitioning, job processing, and ontology assembly. Orchestrator roles, the pending-edits/validate/commit coordination pattern, and failure handling.
- [Config Schema](process/config-schema.md) — Complete schema for `extraction.yaml`: problem statement, data sources, ontology definition, prompts, and output configuration. The single artifact bridging `init` and `run`.
- [Output Format](process/output-format.md) — JSONL mutation format consumed by kartograph. Defines DEFINE, CREATE, UPDATE, DELETE operations, ID format, system properties, and execution order. This is a contract — k-extract must match what kartograph expects.

## Agent

- [Agent Architecture](agent/agent-architecture.md) — Agent instantiation via Claude Agent SDK, the async message loop, usage/cost tracking, instance isolation, tool registration, and error handling.
- [Agent Tools](agent/agent-tools.md) — Contracts for the 5 agent tools: search_entities, search_relationships, manage_entity, manage_relationship, validate_and_commit. Inputs, outputs, side effects, and locking requirements.
- [Prompt Patterns](agent/prompt-patterns.md) — How agent prompts are structured (system prompt + job description), what sections they contain, behavioral constraints, and what a dynamic prompt generator must produce. OpenShift-specific content is flagged separately from universal patterns.
- [Prompt Generation](agent/prompt-generation.md) — How the config file from `k-extract init` is transformed into agent instructions. System-level, ontology-specific, and job-specific instruction layers. The problem statement shapes extraction behavior.

## Data Sources

- [Data Source Configuration](data-sources/data-source-config.md) — YAML config schema, fetch mechanisms (sparse git clone), partitioning strategies (V1 thematic vs V2 character-budget), and configuration hierarchy. Identifies which aspects are domain-specific vs generalizable.
- [Multi-Source Extraction](data-sources/multi-source.md) — How k-extract handles multiple data source paths, shared ontology across sources, cross-source relationships, and per-source partitioning.

## Concurrency

- [Concurrency Model](concurrency/concurrency-model.md) — What must be serialized (ontology writes, job claims), what can be parallel (job processing), the pending-edits isolation model, lock semantics, timeout/retry behavior, and known gaps to address in the reimplementation.

## Lessons Learned

- [V1 to V2 Evolution](lessons-learned/v1-to-v2-evolution.md) — What changed between V1 and V2 and why, with evidence from code and git history. Covers batching strategy, locking evolution, Scenario removal, aggregator pattern, agent instruction tightening, and 7 anti-patterns discovered.

## Decisions

- [Technology Choices](decisions/technology-choices.md) — Implementation decisions for the reimplementation: SQLite+SQLAlchemy, Click/Typer, uv, pytest, Pydantic Settings, structlog, GitHub Actions, pre-commit. Rationale for each choice relative to what it replaces.

---

## Spec Origin

Specs in domain/, process/job-lifecycle.md, process/extraction-pipeline.md, agent/agent-architecture.md, agent/agent-tools.md, agent/prompt-patterns.md, data-sources/data-source-config.md, concurrency/, and lessons-learned/ were extracted directly from the [kartograph-extraction](https://github.com/aredenba-rh/kartograph-extraction) codebase.

Specs in process/guided-session.md, process/output-format.md, agent/prompt-generation.md, data-sources/multi-source.md, and decisions/ were designed collaboratively to define the generalization vision.
