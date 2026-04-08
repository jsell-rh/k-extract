# Task 009: Data Source Handling — File Discovery, Inventory, and Fingerprinting

**Status:** `in-progress`
**Spec Reference:** specs/data-sources/data-source-config.md, specs/data-sources/multi-source.md, specs/process/extraction-pipeline.md (Resumability)
**Branch:** task-009
**PR:** #9
**Review:** (none)

## Description

Implement data source file scanning, the data inventory report (for `init` Step 2), and the environment fingerprinting system (for `run` resume logic).

Reference: specs/data-sources/data-source-config.md sections 1–2, specs/data-sources/multi-source.md, specs/process/extraction-pipeline.md section on Resumability.

### What to build

1. **File discovery:**
   - Scan a data source path recursively
   - Collect file metadata: path, size, character count, file type
   - Group by parent directory (for folder-aware batching downstream)
   - Support multiple data source paths

2. **Data inventory report** (for `init` Step 2):
   - File types and counts per data source
   - Directory structure summary
   - Total volume (file count, total size/characters)
   - Pattern recognition (Python package, markdown docs, etc.)
   - Return as structured data (for display and for AI consumption)

3. **Environment fingerprinting:**
   - Hash every source file (SHA256) in parallel (files are independent I/O)
   - Sort file hashes by filepath for deterministic ordering
   - Compute final SHA256 over: config file contents + prompt templates + model ID + sorted file hashes
   - Store fingerprint in the database (uses EnvironmentFingerprint model from Task 005)

4. **Resume logic:**
   - Compute current fingerprint
   - No previous run → start fresh
   - Previous run + matching fingerprint → resume (skip completed jobs)
   - Previous run + mismatched fingerprint → hard stop with error
   - `--force` flag → discard previous state, start fresh

### File layout

- `src/k_extract/pipeline/sources.py` — File discovery, inventory, character counting
- `src/k_extract/pipeline/fingerprint.py` — SHA256 hashing, fingerprint computation, resume logic
- `tests/pipeline/test_sources.py` — File discovery tests
- `tests/pipeline/test_fingerprint.py` — Fingerprint and resume logic tests

## Acceptance Criteria

- [ ] Recursive file discovery with metadata (path, size, characters, type)
- [ ] Folder-aware grouping of files
- [ ] Data inventory report generation (structured data)
- [ ] Parallel SHA256 hashing of source files
- [ ] Deterministic fingerprint computation (config + prompts + model + files)
- [ ] Resume logic: fresh start, resume, hard stop, and --force behaviors
- [ ] Unit tests for discovery, fingerprinting, and resume decisions

## Relevant Commits

(none yet)
