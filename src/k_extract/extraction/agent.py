"""Agent instantiation, message loop, and usage tracking.

Implements the agent infrastructure from specs/agent/agent-architecture.md:
- Agent instantiation with Claude Agent SDK
- Async message loop handling all message types and terminal states
- Usage tracking (4 token types, cost, per-job and cumulative)
- Conversation logging to per-worker JSONL files (opt-in)
- Error handling for SDK exceptions, error subtypes, and tool failures
- Runtime model capability discovery (contextWindow, maxOutputTokens)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from k_extract.extraction.hooks import create_hooks
from k_extract.extraction.logging import ConversationLogger, get_logger

# Default fallback values if discovery fails
DEFAULT_CONTEXT_WINDOW = 200_000
DEFAULT_MAX_OUTPUT_TOKENS = 50_000

# Module-level cache for discovered model capabilities
_cached_capabilities: ModelCapabilities | None = None


@dataclass
class ModelCapabilities:
    """Context window and output token limits discovered from the Claude Agent SDK."""

    context_window: int
    max_output_tokens: int


async def discover_model_capabilities(
    *,
    model: str | None = None,
) -> ModelCapabilities:
    """Discover model context window and max output tokens via a lightweight SDK query.

    Makes a minimal agent query ("Respond with OK") to obtain
    ``ResultMessage.model_usage``, which contains ``contextWindow`` and
    ``maxOutputTokens`` for the active model.

    Results are cached at module level so repeated calls reuse the first
    successful discovery without additional API roundtrips.

    Falls back to default values if discovery fails (SDK unavailable,
    no model_usage in response, etc.).

    Args:
        model: Optional model ID override.

    Returns:
        ModelCapabilities with discovered or default values.
    """
    global _cached_capabilities

    if _cached_capabilities is not None:
        return _cached_capabilities

    log = get_logger()
    try:
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            max_turns=1,
        )
        if model is not None:
            options.model = model

        async for message in query(prompt="Respond with OK", options=options):
            if isinstance(message, ResultMessage) and message.model_usage:
                # model_usage is dict[str, dict] keyed by model name
                for _model_name, caps in message.model_usage.items():
                    context_window = (
                        caps.get("contextWindow") if isinstance(caps, dict) else None
                    )
                    max_output = (
                        caps.get("maxOutputTokens") if isinstance(caps, dict) else None
                    )
                    if isinstance(context_window, int) and isinstance(max_output, int):
                        log.info(
                            "extraction.model_discovered",
                            context_window=context_window,
                            max_output_tokens=max_output,
                        )
                        result = ModelCapabilities(
                            context_window=context_window,
                            max_output_tokens=max_output,
                        )
                        _cached_capabilities = result
                        return result

        log.warning(
            "extraction.model_discovery_no_usage",
            msg="No model_usage in response, using defaults",
        )
    except Exception as exc:
        log.warning(
            "extraction.model_discovery_failed",
            error=str(exc),
            msg="Using default context window parameters",
        )

    return ModelCapabilities(
        context_window=DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )


@dataclass
class UsageStats:
    """Tracks the four token types and cost for an agent session.

    Per-message accumulation with deduplication by message ID.
    Final result message usage overrides per-message sums.
    Cost comes from ResultMessage.total_cost_usd only.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float | None = None
    _seen_message_ids: set[str] = field(default_factory=set)

    def accumulate_message(self, usage: Any, message_id: str | None) -> None:
        """Accumulate usage from an AssistantMessage.

        Deduplicates by message_id to avoid double-counting from
        parallel tool uses that share the same message. Handles both
        dict-style and attribute-style usage access patterns.

        Args:
            usage: Usage dict or object from message.usage (may be None).
            message_id: The message's unique ID for deduplication.
        """
        if usage is None:
            return
        if message_id is not None and message_id in self._seen_message_ids:
            return
        if message_id is not None:
            self._seen_message_ids.add(message_id)

        self.input_tokens += _get_int(usage, "input_tokens")
        self.output_tokens += _get_int(usage, "output_tokens")
        self.cache_creation_input_tokens += _get_int(
            usage, "cache_creation_input_tokens"
        )
        self.cache_read_input_tokens += _get_int(usage, "cache_read_input_tokens")

    def apply_final(self, result: ResultMessage) -> None:
        """Apply final result message usage, overriding per-message sums.

        Args:
            result: The terminal ResultMessage from the SDK.
        """
        if result.usage is not None:
            self.input_tokens = _get_int(result.usage, "input_tokens")
            self.output_tokens = _get_int(result.usage, "output_tokens")
            self.cache_creation_input_tokens = _get_int(
                result.usage, "cache_creation_input_tokens"
            )
            self.cache_read_input_tokens = _get_int(
                result.usage, "cache_read_input_tokens"
            )
        if result.total_cost_usd is not None:
            self.cost_usd = result.total_cost_usd


@dataclass
class CumulativeUsage:
    """Aggregates usage across multiple jobs."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, stats: UsageStats) -> None:
        """Add a completed job's usage to the cumulative total.

        Args:
            stats: The usage stats from a completed agent session.
        """
        self.input_tokens += stats.input_tokens
        self.output_tokens += stats.output_tokens
        self.cache_creation_input_tokens += stats.cache_creation_input_tokens
        self.cache_read_input_tokens += stats.cache_read_input_tokens
        if stats.cost_usd is not None:
            self.cost_usd += stats.cost_usd


@dataclass
class AgentResult:
    """Result of an agent session."""

    success: bool
    error_message: str | None
    usage: UsageStats


def format_worker_id(index: int) -> str:
    """Format a worker index as a zero-padded two-digit string.

    Args:
        index: The worker index (1-based).

    Returns:
        Zero-padded string like "01", "02", etc.
    """
    return f"{index:02d}"


async def run_agent(
    *,
    worker_id: str,
    system_prompt: str,
    initial_message: str,
    mcp_server: Any,
    job_id: str,
    data_source: str,
    cwd: str | Path,
    conversation_log_dir: Path | None = None,
    model: str | None = None,
) -> AgentResult:
    """Run an extraction agent through its complete message loop.

    Instantiates a ClaudeSDKClient with the provided configuration,
    sends the initial message, and iterates through all responses
    until a terminal state is reached.

    Args:
        worker_id: Zero-padded worker identifier (e.g., "01").
        system_prompt: The complete system prompt for the agent.
        initial_message: The job description message to send.
        mcp_server: McpSdkServerConfig from create_tool_server.
        job_id: Job identifier for observability.
        data_source: Data source name for observability.
        cwd: Working directory for the agent.
        conversation_log_dir: If provided, stream conversation to JSONL.
        model: Optional model ID override.

    Returns:
        AgentResult with success/failure, optional error, and usage stats.
    """
    log = get_logger(worker_id=worker_id, job_id=job_id, data_source=data_source)
    usage = UsageStats()

    hooks_dict = create_hooks(
        worker_id=worker_id,
        job_id=job_id,
        data_source=data_source,
        usage_stats=usage,
    )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Bash", "Glob", "Grep"],
        permission_mode="bypassPermissions",
        mcp_servers={"extraction-tools": mcp_server},
        hooks=hooks_dict,  # type: ignore[arg-type]
        cwd=str(cwd),
    )
    if model is not None:
        options.model = model

    conv_logger: ConversationLogger | None = None
    if conversation_log_dir is not None:
        conv_logger = ConversationLogger(conversation_log_dir, worker_id, job_id)

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(initial_message)

            async for message in client.receive_messages():
                if isinstance(message, AssistantMessage):
                    _handle_assistant_message(
                        message, usage=usage, conv_logger=conv_logger
                    )

                elif isinstance(message, UserMessage):
                    _handle_user_message(message, conv_logger=conv_logger)

                elif isinstance(message, ResultMessage):
                    usage.apply_final(message)

                    if conv_logger is not None:
                        result_data: dict[str, Any] = {
                            "subtype": message.subtype,
                            "is_error": message.is_error,
                            "duration_ms": message.duration_ms,
                        }
                        if message.errors:
                            result_data["errors"] = message.errors
                        if message.result:
                            result_data["result"] = message.result
                        conv_logger.log_message("result", result_data)

                    if message.subtype == "success":
                        return AgentResult(
                            success=True, error_message=None, usage=usage
                        )

                    # error or cancelled
                    error_msg = None
                    if message.errors:
                        error_msg = "; ".join(message.errors)
                    elif message.result:
                        error_msg = message.result
                    log.error(
                        "extraction.agent_failed",
                        subtype=message.subtype,
                        error=error_msg,
                    )
                    return AgentResult(
                        success=False, error_message=error_msg, usage=usage
                    )

            # Loop exited without a result subtype — treat as success
            return AgentResult(success=True, error_message=None, usage=usage)

    except Exception as exc:
        log.error("extraction.agent_exception", error=str(exc))
        return AgentResult(success=False, error_message=str(exc), usage=usage)

    finally:
        if conv_logger is not None:
            conv_logger.close()

    # Unreachable — try/except covers all paths, satisfies pyright
    return AgentResult(  # pragma: no cover
        success=False, error_message="unexpected", usage=usage
    )


def _handle_assistant_message(
    message: AssistantMessage,
    *,
    usage: UsageStats,
    conv_logger: ConversationLogger | None,
) -> None:
    """Process an AssistantMessage: accumulate usage and log content blocks.

    Args:
        message: The AssistantMessage from the SDK.
        usage: UsageStats to accumulate into.
        conv_logger: Optional conversation logger for JSONL output.
    """
    usage.accumulate_message(message.usage, message.message_id)

    if conv_logger is None:
        return

    for block in message.content:
        if isinstance(block, TextBlock):
            conv_logger.log_message(
                "assistant_text",
                {"text": block.text, "message_id": message.message_id},
            )
        elif isinstance(block, ToolUseBlock):
            conv_logger.log_message(
                "tool_use",
                {
                    "tool": block.name,
                    "tool_use_id": block.id,
                    "input": block.input,
                    "message_id": message.message_id,
                },
            )


def _handle_user_message(
    message: UserMessage,
    *,
    conv_logger: ConversationLogger | None,
) -> None:
    """Process a UserMessage: log tool result content blocks.

    UserMessage objects carry ToolResultBlock content blocks containing
    the results of tool executions. These are logged to the conversation
    JSONL to provide a complete record of tool invocations and their results.

    Args:
        message: The UserMessage from the SDK.
        conv_logger: Optional conversation logger for JSONL output.
    """
    if conv_logger is None:
        return

    content = message.content
    if isinstance(content, str):
        conv_logger.log_message("user_text", {"text": content})
        return

    for block in content:
        if isinstance(block, ToolResultBlock):
            result_data: dict[str, Any] = {
                "tool_use_id": block.tool_use_id,
            }
            if block.content is not None:
                result_data["content"] = block.content
            if block.is_error is not None:
                result_data["is_error"] = block.is_error
            conv_logger.log_message("tool_result", result_data)
        elif isinstance(block, TextBlock):
            conv_logger.log_message("user_text", {"text": block.text})


def _get_int(usage: Any, key: str) -> int:
    """Safely extract an integer from a usage dict or object.

    Handles both dict-style (.get()) and attribute-style (getattr()) access,
    as the SDK may return usage in either form.

    Args:
        usage: The usage dict or object (may contain ints or None values).
        key: The key to look up.

    Returns:
        The integer value, or 0 if missing or None.
    """
    val = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    if isinstance(val, int):
        return val
    return 0
