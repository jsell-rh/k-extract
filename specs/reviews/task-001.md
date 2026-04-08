# Review: Task 001

## Round 1

- [process-revision-complete] **CI workflow fails on GitHub Actions.** `.github/workflows/ci.yml:20` — `uv pip install -e ".[dev]" --system` fails because Ubuntu's system Python is externally managed (PEP 668: "externally-managed-environment"). The `--system` flag bypasses virtual environments, but modern Ubuntu runners reject system-wide installs. This means the CI pipeline never reaches lint, type-check, or test steps. Replace with `uv sync --dev` or remove the install step entirely and let `uv run` handle dependency resolution. **Spec reference:** Task-001 acceptance criterion 5 (`.github/workflows/ci.yml` exists and **runs** lint, type-check, and test); `specs/decisions/technology-choices.md` CI/CD section ("GitHub Actions pipeline for test, lint, type-check on PRs").

- [process-revision-complete] **Tests directory does not mirror `src/k_extract/` structure.** Task-001 description step 3 says: "Create `tests/` directory mirroring `src/k_extract/` structure with a placeholder test." `src/k_extract/` has subdirectories `cli/`, `config/`, `domain/`, `extraction/`, `pipeline/`, but `tests/` contains only `__init__.py` and `test_cli.py` at the root — no mirroring subdirectories. **Spec reference:** Task-001 description step 3.
