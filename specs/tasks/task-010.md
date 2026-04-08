# Task 010: Agent Architecture — Instantiation, Message Loop, and Observability

**Status:** `ready-for-review`
**Spec Reference:** specs/agent/agent-architecture.md, specs/decisions/technology-choices.md (Logging)
**Branch:** task-010
**PR:** #10
**Review:** specs/reviews/task-010.md

## Description

Implement the agent infrastructure: how agents are instantiated with the Claude Agent SDK, the async message loop, usage/cost tracking, SDK hooks for observability, and structlog integration.

Reference: specs/agent/agent-architecture.md, specs/decisions/technology-choices.md (Logging & Observability).

### What to build

1. **Agent instantiation:**
   - Configure Claude Agent SDK client with: system prompt, tool restrictions (read-only built-ins only), permission mode (bypass), MCP server (in-process tools from Task 007)
   - Worker identity: zero-padded numeric strings ("01", "02", ...)
   - Bind tool functions to worker's staging area at instantiation time (closure/factory)

2. **Async message loop:**
   - `client.query(initial_message)` → iterate `client.receive_messages()`
   - Handle AssistantMessage content blocks (TextBlock, ToolUseBlock)
   - Detect completion via `message.subtype`: "success" → done, "error"/"cancelled" → failure
   - Loop exit without subtype → treat as success

3. **Usage tracking:**
   - Track 4 token types: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`
   - Per-message accumulation (deduplicate by message ID)
   - Final result message `.usage` overrides per-message sums
   - Cost from `ResultMessage.total_cost_usd` only (no manual cost calculation)
   - Aggregate per-job and cumulative

4. **SDK hooks for observability:**
   - `PreToolUse` (matcher: `^mcp__`): log tool invocations with arguments
   - `PostToolUse` (matcher: `^mcp__`): log tool results, duration, success/failure
   - `PostToolUseFailure`: log tool errors with context
   - `Stop`: log agent completion, total usage, cost
   - All hooks emit to structlog with domain-oriented events
   - Per-instance hooks bound to worker_id, job_id, data_source

5. **structlog configuration:**
   - Color output for terminal (human-readable)
   - JSON output support (machine-consumable)
   - Domain-oriented events only (job_claimed, entity_extracted, validation_failed — not lock_acquired, file_opened)

6. **Conversation logging** (`--log-conversations`):
   - Stream full agent conversation per worker to JSONL file
   - One line per message (streaming, crash-safe)
   - Off by default

7. **Error handling:**
   - SDK client exceptions → failure with error message
   - Error/cancelled subtypes → failure
   - Tool validation errors → is_error=True (agent can retry)

### File layout

- `src/k_extract/extraction/agent.py` — Agent instantiation, message loop, usage tracking
- `src/k_extract/extraction/hooks.py` — SDK hook implementations
- `src/k_extract/extraction/logging.py` — structlog configuration, conversation logger
- `tests/extraction/test_agent.py` — Agent tests (may need mocking of SDK)

## Acceptance Criteria

- [ ] Agent instantiation with Claude Agent SDK (system prompt, tools, permissions, MCP)
- [ ] Async message loop handling all message types and terminal states
- [ ] Usage tracking (4 token types, cost, per-job and cumulative)
- [ ] SDK hooks emitting domain-oriented structlog events
- [ ] structlog configured with color + JSON output modes
- [ ] Conversation logging to per-worker JSONL files (opt-in)
- [ ] Error handling for SDK exceptions, error subtypes, and tool failures
- [ ] Tests for message loop, usage accumulation, and hook behavior

## Relevant Commits

- `861e5c7` — feat(task-010): agent architecture — instantiation, message loop, observability
