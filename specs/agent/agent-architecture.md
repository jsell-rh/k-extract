# Agent Architecture Spec

Distilled from `kartograph-extraction` codebase. Captures patterns for reimplementation with dynamic ontologies.

---

## 1. Agent Instantiation

Agents are instantiated with: a system prompt, tool restrictions, a permission mode, and environment-based identity.

**Options configured per agent type:**

| Option | Worker | Aggregator |
|--------|--------|------------|
| Allowed tools | Read, Bash, Glob, Grep (built-in, read-only) + custom extraction tools | Read, Bash, Glob, Grep (built-in, read-only) + custom extraction tools |
| Disallowed tools | Write, Edit, and other mutating built-in tools | Write, Edit, and other mutating built-in tools |
| Permission mode | Bypass (custom tools enforce safety) | Bypass (custom tools enforce safety) |
| System prompt | Generated from job + ontology config | Generated from aggregation config |
| Tools server | In-process MCP server with extraction tools | In-process MCP server with extraction tools |

**Key patterns:**

- **Agents can read and explore the filesystem directly** using built-in tools (Read, Bash, Glob, Grep). This is how they access source files, list directories, search content, etc. Bash is allowed for read-only operations (ls, tree, grep, cat, etc.).
- **All knowledge graph mutations use custom Python function tools** registered via the Claude Agent SDK's `@tool` decorator and `create_sdk_mcp_server`. Built-in Write and Edit tools are not exposed — agents cannot modify files directly.
- **Custom tool functions run in-process** — no subprocess spawning, no CLI argument parsing, no shell escaping. Tools receive typed arguments and return structured results directly.
- **Tool restrictions are enforced at the SDK level** — the `tools` option lists only the read-only built-ins, removing mutating tools from the agent's context.

### Tool Safety Model

Since tools are Python functions running in-process, safety is enforced by the tool implementations themselves:
- Read tools query the shared store and the agent's staging area.
- Write tools only modify the agent's private staging area.
- The commit tool is the only path to mutate shared state, and it validates before applying.

---

## 2. Message Loop Pattern

All agent types use the same async message loop:

```
1. client.query(initial_message)        # Send initial user message
2. async for message in client.receive_messages():  # Iterate SDK messages
   a. Increment message_count
   b. Accumulate usage from message.usage (if present)
   c. Process message content:
      - AssistantMessage: iterate content blocks (TextBlock, ToolUseBlock)
      - Log text blocks, tool calls (command for Bash, file_path for Read)
   d. Check for completion via message.subtype:
      - "success" -> return (True, None, usage)
      - "error" or "cancelled" -> return (False, error_message, None)
3. If loop exits without subtype -> treat as success
```

**Message types consumed from SDK:**

| Type | How Used |
|------|----------|
| `AssistantMessage` | Contains `content` blocks (`TextBlock`, `ToolUseBlock`). Has `usage` and `id` fields. |
| `TextBlock` | Agent's reasoning text. Logged and optionally printed (truncated for display). |
| `ToolUseBlock` | Tool invocation. Has `name`, `input`, and `id`. Logged with arguments. |
| `ToolResultBlock` | Tool execution result. Has `content` and `is_error`. |
| Result message (via `message.subtype`) | Terminal message. Has `subtype` ("success"/"error"/"cancelled"), optional `error_message`, `usage`, `total_cost_usd`. |

**Initial user message pattern:** The initial message directs the agent to read its job description and begin processing.

**Generalizable requirement:** The message loop must handle streaming SDK responses, accumulate usage metrics, detect terminal states, and support logging/observability hooks.

---

## 3. Usage Tracking

### Token Counting

Four token types are tracked per agent session:

| Token Type | Description |
|------------|-------------|
| `input_tokens` | Prompt tokens |
| `output_tokens` | Completion tokens |
| `cache_creation_input_tokens` | Tokens written to cache |
| `cache_read_input_tokens` | Tokens read from cache |

**Accumulation strategy:**

- **Per-message accumulation:** Each `AssistantMessage` with `.usage` contributes to running totals (deduplicate by message ID to avoid double-counting parallel tool uses).
- **Final result override:** The terminal result message's `.usage` is treated as authoritative cumulative usage, replacing per-message sums.
- **Usage can be dict or object:** Code handles both dict-style and attribute-style access patterns.

### Cost Tracking

- **SDK-provided cost only.** Cost comes from `ResultMessage.total_cost_usd`. If not available, cost is not accumulated — no fallback computation from token rates.
- **The system tracks cost per job and cumulative cost.**

**Generalizable requirement:** Track all four token types and SDK-reported cost. Aggregate costs across jobs for budget monitoring. Do not implement manual cost calculation.

---

## 4. Agent Instance Isolation

### Workspace Isolation

Each agent gets a dedicated workspace containing: job instructions, a staging area for edits, and logging output.

**Instance ID conventions:**
- Worker instances: zero-padded numeric strings (`"01"`, `"02"`, ...).
- Aggregator instance: a well-known string identifier.

### Environment-Based Identity

Instance identity is set in the process environment before agent launch. All tool scripts read this to locate the correct staging area for the agent.

### Data Isolation via Virtual Ontology

Tool functions use a "virtual ontology" pattern: when reading, they merge the shared ontology with the agent's staged edits to present a consistent view. When writing, changes go only to the agent's staging area. The shared ontology is only mutated during the commit step under an exclusive transaction.

Since tools are in-process Python functions, instance identity can be captured in a closure or passed as state to the tool factory — no environment variables needed. Each agent's tool functions are bound to its specific staging area at instantiation time.

---

## 5. Tool Registration Pattern

Tools are Python functions decorated with `@tool` from `claude_agent_sdk`, bundled into an in-process MCP server via `create_sdk_mcp_server`, and passed to `query()` via the `mcp_servers` option. All built-in tools are removed by passing `tools=[]`.

| Category | Tool | Access | Annotations |
|----------|------|--------|-------------|
| Search entities | `search_entities` | Read (shared + staged) | `readOnlyHint=True` |
| Search relationships | `search_relationships` | Read (shared + staged) | `readOnlyHint=True` |
| Manage entity | `manage_entity` | Write (staging area only) | |
| Manage relationship | `manage_relationship` | Write (staging area only) | |
| Validate and commit | `validate_and_commit` | Read+Write (shared, under transaction) | |

Read-only tools are annotated with `readOnlyHint=True`, enabling the SDK to batch them in parallel.

### Tool Function Pattern

Each tool is an async Python function that:
1. Receives a `dict[str, Any]` of validated arguments
2. Performs its operation (query, stage, or commit)
3. Returns `{"content": [{"type": "text", "text": "..."}]}` on success
4. Returns `{"content": [...], "is_error": True}` on validation failure

The tool's input schema is defined in the `@tool` decorator — either as a simple dict (`{"slug": str}`) or as full JSON Schema for complex types. The SDK converts this to the schema the agent sees.

### Tool Documentation

The system prompt includes brief descriptions of each tool. The `@tool` decorator's description string serves as the primary documentation the agent sees. Detailed usage guidance (e.g., search modes, upsert semantics) is embedded in the description or in the system prompt.

---

## 6. Error Handling

### SDK-Level Errors

- **Exception in SDK client:** Caught by try/except around the client context manager. Returns failure with error message. Exception is printed and logged.
- **Error subtype from SDK:** Error or cancelled subtypes trigger failure return with the error message from the result.
- **Session ends without result:** If the message loop exits without a subtype, it is treated as success (graceful completion).

### Tool Function Errors

- **Validation errors in tools:** Tool functions return `is_error=True` with a descriptive message. The agent sees the error and can retry with corrected input.
- **Lock contention:** If a tool blocks on a transaction lock (another instance running the commit step), it can either retry internally or return an error prompting the agent to wait and retry.
- **Commit failures:** Validation errors are returned as structured error results. The agent is instructed to fix issues and re-run until success.

### Observability via Hooks

Agent observability uses the Claude Agent SDK's **hooks** mechanism for domain-oriented observability. Hooks are callback functions that fire on agent events without polluting tool implementations or the message loop.

**Hook-based observability pattern:**

| Hook Event | Domain Probe | Purpose |
|------------|-------------|---------|
| `PreToolUse` (matcher: `^mcp__`) | `extraction.tool_invoked` | Log which tool the agent is calling, with what arguments, for which job |
| `PostToolUse` (matcher: `^mcp__`) | `extraction.tool_completed` | Log tool result, duration, success/failure |
| `PostToolUseFailure` | `extraction.tool_failed` | Log tool errors with context for debugging |
| `Stop` | `extraction.agent_stopped` | Log agent completion, total usage, cost |

Per-worker completion is logged by the orchestrator itself (which `await`s each `query()` call), not via hooks — workers are independent `query()` sessions, not SDK subagents.

Hooks receive `agent_id` and `tool_use_id` in their input, enabling correlation across the full lifecycle of a tool call. `PostToolUse` hooks can be made async (`async_=True`) to avoid blocking the agent while logging.

**Key principle:** Tool functions contain zero logging. All observability is injected via hooks at the orchestrator level. This keeps tools focused on their domain logic and makes observability configurable — different runs can use different hook configurations (verbose for debugging, minimal for production, webhook-based for monitoring).

**Per-instance tracking:** Each agent instance gets hooks bound to its instance ID, job ID, and data source at instantiation time. This enables structured log events like:

```
extraction.tool_completed | instance=03 | job=batch_0042 | tool=manage_entity | slug=repo:my-repo | duration_ms=45
```

All logging uses structlog with domain-oriented observability — log domain events (entity extracted, relationship created, validation failed) not implementation details (lock acquired, file opened).

---

## 7. Orchestration Patterns

### Worker + Aggregator Pattern

1. **Orchestrator** generates jobs (file assignments) and spawns N worker agent instances in parallel.
2. Each **worker** processes its assigned files, stages edits, and runs the commit step.
3. After all workers complete, the **aggregator** runs with a report of what each worker edited and any overlapping slugs.
4. The aggregator reviews for coherence, resolves conflicts, and runs its own commit step.

### Job Description Generation

Job descriptions are generated programmatically with template variables:
- Instance ID, job ID, data source, file list, file count, character count
- Data-source-specific entity type mappings and descriptions
- Workflow steps referencing the tool scripts

The aggregator gets a different template with:
- Round number
- Slugs edited per worker instance
- Overlapping slugs (conflict candidates)

**Generalizable requirement:** The orchestrator must generate per-instance job descriptions from templates, support parallel worker execution, and optionally run an aggregator pass for cross-instance coherence.
