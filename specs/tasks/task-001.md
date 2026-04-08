# Task 001: Project Scaffolding and CI Setup

**Status:** `in-progress`
**Spec Reference:** specs/decisions/technology-choices.md
**Branch:** task-001
**PR:** (none)
**Review:** (none)

## Description

Set up the foundational project structure, packaging, pre-commit hooks, and CI pipeline. This must be the first task completed before any feature work begins.

### What to do

1. **Update `pyproject.toml`:**
   - Add `[build-system]` (hatchling or setuptools)
   - Add `[project.scripts]` entry point: `k-extract = "k_extract.cli:main"`
   - Add all runtime dependencies: `claude-agent-sdk`, `click` (or `typer`), `sqlalchemy`, `pydantic-settings`, `structlog`, `pyyaml`
   - Add `[project.optional-dependencies]` dev group: `pytest`, `ruff`, `pyright`
   - Configure `[tool.ruff]` and `[tool.pyright]` sections

2. **Create `src/k_extract/` package structure:**
   ```
   src/
   └── k_extract/
       ├── __init__.py
       ├── cli/
       │   └── __init__.py      # Contains `main()` entry point (Click/Typer group)
       ├── domain/
       │   └── __init__.py
       ├── extraction/
       │   └── __init__.py
       ├── pipeline/
       │   └── __init__.py
       └── config/
           └── __init__.py
   ```

3. **Create `tests/` directory** mirroring `src/k_extract/` structure with a placeholder test.

4. **Create `.pre-commit-config.yaml`** with hooks for:
   - ruff (lint + format)
   - pyright (type checking)

5. **Create `.github/workflows/ci.yml`** that runs on PRs:
   - Lint (`ruff check`)
   - Format check (`ruff format --check`)
   - Type check (`pyright`)
   - Tests (`pytest`)

6. **Install in development mode:** `uv pip install -e ".[dev]"`

7. **Verify:**
   - `k-extract --help` prints CLI help
   - `pre-commit run --all-files` passes
   - `pytest` discovers and runs the placeholder test

## Acceptance Criteria

- [ ] `src/k_extract/` directory structure exists with all subpackages
- [ ] `tests/` directory exists with at least one passing test
- [ ] `pyproject.toml` has build system, entry point, all dependencies, and tool configs
- [ ] `.pre-commit-config.yaml` exists with ruff and pyright hooks
- [ ] `.github/workflows/ci.yml` exists and runs lint, type-check, and test
- [ ] `k-extract --help` works after `uv pip install -e ".[dev]"`
- [ ] `pre-commit run --all-files` passes cleanly
- [ ] `pytest` passes

## Relevant Commits

(none yet)
