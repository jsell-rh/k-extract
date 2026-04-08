# Review: Task 009

## Round 1

- [ ] **compute_fingerprint includes file paths in hash, deviating from spec.** extraction-pipeline.md specifies the final SHA256 should cover "Sorted file content hashes" (line 131). The implementation at fingerprint.py:92-93 also feeds each file's path string into the hash (`h.update(filepath.encode("utf-8"))`), which the spec does not list as an input. This causes fingerprint changes on file renames even when content is unchanged, contradicting the spec's enumeration of fingerprint inputs.

- [ ] **store_fingerprint crashes on duplicate fingerprints.** The EnvironmentFingerprint model uses `fingerprint` as its primary key. If the environment is unchanged between consecutive runs, calling `store_fingerprint` (fingerprint.py:186-193) with the same hash value would raise an IntegrityError (duplicate PK). The spec says the fingerprint should be "stored in the run's database alongside job state" (extraction-pipeline.md:133), but the function performs a plain INSERT with no upsert or existence check.

- [ ] **DataSourceInventory lacks directory structure summary.** The task requires the data inventory to include "Directory structure summary," but `DataSourceInventory` (sources.py:53) only stores `directory_count: int`. The actual directory paths are computed in `build_inventory` (sources.py:204) but immediately discarded. Downstream consumers (display and AI consumption per task description) have no way to inspect the directory layout — only the count of unique directories.

- [ ] **discover_files includes .git and other hidden directories.** `discover_files` (sources.py:144) uses `root.rglob("*")` with no filtering of hidden directories. Data sources fetched via sparse shallow git clone (data-source-config.md section 2) will contain `.git/` subdirectories. Including git internal objects inflates file counts, total size, and character counts in the inventory report, distorts pattern recognition, and — when these files are fed into fingerprint computation — makes fingerprints sensitive to git metadata changes unrelated to actual source content.
