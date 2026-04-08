"""Agent instantiation, message loop, and usage tracking.

Implements the agent infrastructure from specs/agent/agent-architecture.md:
- Agent instantiation with Claude Agent SDK
- Async message loop handling all message types and terminal states
- Usage tracking (4 token types, cost, per-job and cumulative)
- Conversation logging to per-worker JSONL files (opt-in)
- Error handling for SDK exceptions, error subtypes, and tool failures
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
    ToolUseBlock,
)

from k_extract.extraction.hooks import create_hooks
from k_extract.extraction.logging import ConversationLogger, get_logger


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

    def accumulate_message(
        self, usage: dict[str, Any] | None, message_id: str | None
    ) -> None:
        """Accumulate usage from an AssistantMessage.

        Deduplicates by message_id to avoid double-counting from
        parallel tool uses that share the same message.

        Args:
            usage: Usage dict from message.usage (may be None).
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
        conv_logger = ConversationLogger(conversation_log_dir, worker_id)

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(initial_message)

            async for message in client.receive_messages():
                if isinstance(message, AssistantMessage):
                    _handle_assistant_message(
                        message, usage=usage, conv_logger=conv_logger
                    )

                elif isinstance(message, ResultMessage):
                    usage.apply_final(message)

                    if conv_logger is not None:
                        conv_logger.log_message(
                            "result",
                            {
                                "subtype": message.subtype,
                                "is_error": message.is_error,
                                "duration_ms": message.duration_ms,
                            },
                        )

                    if message.subtype == "success":
                        log.info(
                            "extraction.agent_completed",
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            cost_usd=usage.cost_usd,
                        )
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
            log.info(
                "extraction.agent_completed",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                note="loop_exit_no_subtype",
            )
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


def _get_int(usage: dict[str, Any], key: str) -> int:
    """Safely extract an integer from a usage dict.

    Args:
        usage: The usage dict (may contain ints or None values).
        key: The key to look up.

    Returns:
        The integer value, or 0 if missing or None.
    """
    val = usage.get(key)
    if isinstance(val, int):
        return val
    return 0
