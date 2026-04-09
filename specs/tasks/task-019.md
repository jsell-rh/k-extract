# Task 019: Rich Terminal UI — Streaming Feedback for `k-extract init`

**Status:** `ready-for-review`
**Spec Reference:** specs/process/guided-session.md
**Branch:** task-019
**PR:** #19
**Review:** (none)

## Description

The `k-extract init` command currently prints a static status line (e.g., "Generating ontology proposal...") and then goes completely silent for 30-120 seconds during LLM calls. Users see a blinking cursor with no indication of progress, whether the call is still running, or what the system is doing. This task adds Rich-based terminal UI to the init flow: animated spinners during all LLM calls, and incremental streaming of the LLM's thinking as dim/overwriting status lines so the user can see the system is alive and working without cluttering the terminal.

### What to build

1. **Add `rich` dependency to `pyproject.toml`:**
   - Add `rich>=13.0` to the project dependencies list.

2. **Create `src/k_extract/cli/display.py` — Rich display layer:**
   - A `Console` singleton (or factory) for consistent Rich output across all CLI commands.
   - A context manager `spinner(message: str)` that wraps `rich.console.Console.status()` — shows an animated spinner with the given message. On exit, replaces the spinner line with a static completion message (e.g., checkmark + message + "done"). Must work correctly with `asyncio` (the spinner animates in a background thread managed by Rich, so no special async handling is needed — just ensure the context manager is used around `await` calls).
   - A function `stream_thinking(console: Console, text: str)` that prints a dim, overwriting status line showing a truncated snippet of the LLM's latest output. This gives the user a sense of "the AI is thinking about X" without dumping full output. Lines should be truncated to terminal width, printed in dim style (`style="dim"`), and each new line should overwrite the previous one (use `\r` + `console.print(..., end="")` or `rich.live.Live`). When the LLM call completes, the thinking lines are cleared.
   - Migrate all existing `click.echo()` calls in `init.py` to use the Rich console for output. The inventory display, ontology display, and reasoning display should all use Rich formatting (panels, tables, or styled text as appropriate). The goal is a cohesive, polished terminal experience — not a mix of plain `click.echo` and Rich output.

3. **Create a streaming LLM caller in `init.py`:**
   - Replace `_create_default_llm_caller()` with a version that accepts a `Console` and streams incremental text to `stream_thinking()` as `TextBlock` chunks arrive via the `async for message` loop. The caller already iterates `AssistantMessage` blocks — instead of silently accumulating them, pass each `TextBlock.text` chunk to the display layer.
   - The streaming display is purely cosmetic — the full accumulated response is still returned as a string for parsing. The dim thinking lines are cleared/overwritten once the call completes.
   - The `llm_call` signature for testing (`Callable[[str], Awaitable[str]]`) must remain compatible. The streaming display is only active when using the default (real) LLM caller. Tests that inject a mock `llm_call` should see no change in behavior.

4. **Wire spinners into all three LLM call sites in the init flow:**
   - **Ontology proposal** (line ~119): Wrap the `_propose_ontology` call in a spinner. While the spinner is active, stream thinking lines from the LLM response.
   - **Refinement loop iterations** (line ~435): Each refinement LLM call gets a spinner ("Updating ontology...") with streaming thinking.
   - **Prompt composition** (line ~137): Wrap the `_build_config` / `generate_extraction_guidance` call in a spinner ("Composing extraction prompts...") with streaming thinking.

5. **Enhance the inventory display:**
   - Replace the plain-text `_display_inventory()` with Rich-formatted output: use a `rich.panel.Panel` or `rich.table.Table` to display file counts, sizes, file types, directories, and patterns in a visually structured way. The content should be the same information, just better presented.

6. **Enhance the ontology display:**
   - Replace the plain-text `_display_ontology()` with Rich-formatted output. Entity types and relationship types should be visually distinct (use panels, trees, or styled sections). Properties should be clearly delineated.

7. **Tests in `tests/cli/test_display.py`:**
   - Test that `spinner()` context manager enters and exits without error (mock the Rich console to avoid real terminal output in tests).
   - Test that `stream_thinking()` truncates text to a given width and outputs dim-styled content.
   - Test that the streaming LLM caller still returns the complete accumulated text.
   - Test that existing `test_init.py` tests continue to pass unchanged (the mock `llm_call` path must not invoke any Rich display logic).

### File layout

- `pyproject.toml` — Add `rich>=13.0` dependency
- `src/k_extract/cli/display.py` — Rich console, spinner, streaming display utilities
- `src/k_extract/cli/init.py` — Updated to use Rich display layer + streaming LLM caller
- `tests/cli/test_display.py` — Tests for display utilities

### Dependencies

- All prior tasks (1-18) must be complete

## Acceptance Criteria

- [ ] `rich>=13.0` is in project dependencies
- [ ] `src/k_extract/cli/display.py` exists with `spinner()` context manager and `stream_thinking()` function
- [ ] All three LLM call sites in `init.py` show an animated spinner during the call
- [ ] LLM response text streams incrementally as dim, overwriting status lines during the call
- [ ] Thinking lines are cleared when the LLM call completes
- [ ] Inventory display uses Rich formatting (panels/tables)
- [ ] Ontology display uses Rich formatting
- [ ] No `click.echo()` remains in `init.py` — all output goes through Rich console
- [ ] Mock `llm_call` tests in `test_init.py` continue to pass without modification
- [ ] New tests in `tests/cli/test_display.py` cover spinner, streaming, and truncation behavior
- [ ] `uv run pytest` passes, `uv run pyright` clean, `uv run ruff check` clean

## Relevant Commits

- `pending` — feat(task-019): Rich terminal UI with streaming feedback for k-extract init
