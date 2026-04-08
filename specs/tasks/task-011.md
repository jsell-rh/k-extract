# Task 011: Guided Session — `k-extract init` Interactive Flow

**Status:** `not-started`
**Spec Reference:** specs/process/guided-session.md
**Branch:** (none)
**PR:** (none)
**Review:** (none)

## Description

Implement the `k-extract init` guided session that interactively produces an `extraction.yaml` config file. This is the design phase that precedes extraction.

Reference: specs/process/guided-session.md.

### What to build

1. **CLI command: `k-extract init`**
   - Arguments: one or more data source paths
   - Options: `--problem` (skip interactive problem statement), `--output` (config file path, default `extraction.yaml`)
   - Interactive by default, all prompts overridable via CLI args for headless use

2. **Step 1: Problem statement**
   - Prompt: "What problems are you trying to solve with this knowledge graph?"
   - Accept free-form text input
   - Store verbatim in config

3. **Step 2: Data inventory**
   - Scan all provided paths (uses file discovery from Task 009)
   - Display: file types and counts, directory structure, volume summary, recognizable patterns
   - Give user and AI a shared understanding of the raw material

4. **Step 3: Ontology proposal**
   - AI agent reads a representative sample of the data in light of the problem statement
   - Produces a one-shot proposal: entity types + relationship types with descriptions and properties
   - Reasoning for each type: why it supports the stated problem

5. **Step 3b: Iterative refinement loop**
   - Display current ontology
   - User provides feedback or presses Enter to accept
   - On feedback: AI updates the ontology, loop back
   - Unlimited iterations until user accepts

6. **Step 4: Config file output**
   - Compose prompts (uses prompt generation from Task 008): static template + LLM-generated guidance → system_prompt
   - Build job_description_template with variable placeholders
   - Write complete `extraction.yaml` using config schema from Task 003

7. **Headless mode:**
   - `k-extract init /path/to/data --problem "..." --output extraction.yaml`
   - Skips interactive prompts, uses provided args

### File layout

- `src/k_extract/cli/init.py` — CLI command and guided session orchestration
- `tests/cli/test_init.py` — Tests (may need mocking of AI calls)

## Acceptance Criteria

- [ ] `k-extract init /path/to/data` starts interactive session
- [ ] Step 1: problem statement prompt and capture
- [ ] Step 2: data inventory scan and display
- [ ] Step 3: AI ontology proposal from data sample + problem statement
- [ ] Step 3b: iterative refinement loop (unlimited revisions, Enter to accept)
- [ ] Step 4: config file output with composed prompts
- [ ] Headless mode: `--problem` and `--output` flags skip interaction
- [ ] Output config file passes validation from Task 003
- [ ] Tests for the session flow and headless mode

## Relevant Commits

(none yet)
