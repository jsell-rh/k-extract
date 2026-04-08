# Technology Choices

Decisions made during the spec extraction process (2026-04-07) for the k-extract reimplementation.

## State Management

**Decision:** SQLite + SQLAlchemy

**Rationale:** The original system used filesystem directories (pending/, in_progress/, completed/) for job state, which caused race conditions and required manual file locking with `fcntl`. SQLite provides proper transactions and locking built-in. SQLAlchemy allows swapping backends later if needed.

**Replaces:** Directory-based state with JSON files and `fcntl` locks.

## CLI Framework

**Decision:** Click or Typer (Python CLI framework)

**Rationale:** The original used a 400-line Makefile as the primary user interface. A Python CLI framework provides proper argument parsing, help text, subcommands, and interactive prompts — the latter being essential for the guided ontology-building workflow.

**Replaces:** Makefile with 40+ targets.

## Packaging

**Decision:** uv + pyproject.toml

**Rationale:** The original had no pyproject.toml, used `sys.path.insert(0, ...)` hacks for imports, and had unpinned dependencies in a bare requirements.txt. uv provides fast dependency resolution, lockfile support, and virtual env management.

**Replaces:** requirements.txt with unpinned deps, no packaging metadata.

## Testing

**Decision:** pytest

Testing practices and conventions will be defined in a separate file outside the specs. The specs themselves do not prescribe testing methodology.

**Replaces:** Zero test coverage.

## Orchestration

**Decision:** Keep async task model (asyncio.gather with N workers)

**Rationale:** The existing model — a single process spawning N async workers that pull from a shared job queue — is appropriate for the scale. No need for distributed task queue infrastructure.

**Replaces:** Same model, cleaner implementation.

## Configuration

**Decision:** Pydantic Settings

**Rationale:** The original used a mix of config.json, .env files, YAML context files, and Makefile variables with no validation. Pydantic Settings provides typed config with env var support, validation, and defaults in a single source of truth.

**Replaces:** config.json + .env + YAML + Makefile variables.

## Logging & Observability

**Decision:** structlog with domain-oriented observability, powered by Claude Agent SDK hooks

- Color output for human readability
- JSON output support for machine consumption
- Strict domain-oriented observability: log domain events (job claimed, entity extracted, validation failed) not implementation details (file opened, lock acquired)
- Per-agent-instance observability uses SDK hooks (`PreToolUse`, `PostToolUse`, `Stop`, etc.) — tool functions contain zero logging; all observability is injected at the orchestrator level via hook callbacks
- Hooks emit structured domain probes (e.g., `extraction.tool_completed`, `extraction.entity_staged`) to structlog

**Replaces:** Print statements with emoji prefixes, no log levels, debug prints left in production code.

## CI/CD

**Decision:** GitHub Actions + pre-commit

- pre-commit hooks for lint, format, type-check on every commit
- GitHub Actions pipeline for test, lint, type-check on PRs

**Replaces:** No CI, no pre-commit, no automated quality gates.

## Ontology Design

**Decision:** Fresh design (generalized)

**Rationale:** The existing ontology schema was specific to OpenShift/ROSA documentation extraction. The reimplementation targets a general-purpose extraction framework where ontology schemas are user-defined through an interactive guided process.

**Replaces:** Hardcoded OpenShift-specific entity/relationship type definitions.

## Model Configuration

**Decision:** Model ID via environment variable, context window from SDK at runtime

The model used by extraction agents is configured via environment variable. At runtime, the system obtains `contextWindow` and `maxOutputTokens` from the Claude Agent SDK's `ResultMessage.model_usage` — this works through any inference provider (Vertex AI, Bedrock, direct API). These values drive the batching algorithm. No hardcoded context window sizes or magic multiplier constants.

**Replaces:** Implicit model selection inside the Claude Agent SDK setup, hardcoded `avg_size * 6.8` batching threshold.

## Scope

**Decision:** V2 codebase is canonical. V1 is treated as historical context.

V1 lessons are captured in specs/lessons-learned/ but the reimplementation is based on V2 patterns and improvements.
