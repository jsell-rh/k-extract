# Task 021: Respect .gitignore in File Discovery

**Status:** `in-progress`
**Spec Reference:** specs/process/guided-session.md, specs/process/extraction-pipeline.md
**Branch:** task-021
**PR:** (none)
**Review:** (none)

## Description

The file discovery function `discover_files()` in `src/k_extract/pipeline/sources.py` must respect `.gitignore` patterns when scanning data sources. Currently it only skips hidden files (those starting with `.`), but two specs require `.gitignore` handling:

- **guided-session.md** (Step 2): "Files matched by `.gitignore` (if present in a data source) are excluded from the inventory. This prevents build artifacts, vendored dependencies, and generated files from polluting the scan."
- **extraction-pipeline.md** (Environment Fingerprinting): "Enumerate all source files, respecting `.gitignore` in each data source (if present). Files matched by `.gitignore` are excluded from fingerprinting, batching, and extraction."

Since `discover_files()` is the single entry point used by both the guided session inventory (init Step 2) and the pipeline orchestrator (fingerprinting + job generation), fixing it in one place covers all three downstream uses: inventory display, environment fingerprinting, and job batching/extraction.

### Implementation Approach

Add `.gitignore` pattern matching to `discover_files()`. When a `.gitignore` file exists at the root of a data source, parse its patterns and exclude matching files from the results. Use the `pathspec` library (PyPI) which implements the full gitignore spec including negation patterns, directory-only patterns, and nested `.gitignore` files.

If no `.gitignore` is present in a data source, behavior is unchanged (all non-hidden files are included).

## Acceptance Criteria

- [ ] `discover_files()` checks for `.gitignore` at the data source root
- [ ] Files matching `.gitignore` patterns are excluded from results
- [ ] Standard gitignore pattern syntax is supported (globs, directory patterns, negation with `!`, comments with `#`)
- [ ] When no `.gitignore` is present, all non-hidden files are still returned (no behavior change)
- [ ] `pathspec` (or equivalent) is added to project dependencies in `pyproject.toml`
- [ ] Unit tests cover: gitignore present with patterns, gitignore absent, negation patterns, nested directory patterns
- [ ] Existing tests continue to pass (no regressions)

## Relevant Commits

(none yet)
