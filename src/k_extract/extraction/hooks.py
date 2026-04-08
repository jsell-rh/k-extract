"""SDK hook implementations for agent observability.

Implements domain-oriented observability via Claude Agent SDK hooks.
Tool functions contain zero logging — all observability is injected
at the orchestrator level via these hook callbacks.

Each hook factory binds worker_id, job_id, and data_source at
instantiation time, producing structured log events like:

    extraction.tool_invoked | worker_id=03 | job_id=batch_0042 | tool=manage_entity
"""

from __future__ import annotations

import time

import structlog
from claude_agent_sdk import (
    HookCallback,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
)

from k_extract.extraction.logging import get_logger

# Module-level storage for tool invocation start times, keyed by tool_use_id
_tool_start_times: dict[str, float] = {}


def create_hooks(
    *,
    worker_id: str,
    job_id: str,
    data_source: str,
) -> dict[str, list[HookMatcher]]:
    """Create SDK hooks bound to a specific worker instance.

    Returns a hooks dict suitable for ClaudeAgentOptions.hooks.

    Args:
        worker_id: The worker's zero-padded identifier.
        job_id: The job identifier this worker is processing.
        data_source: The data source name.
    """
    log: structlog.stdlib.BoundLogger = get_logger(
        worker_id=worker_id,
        job_id=job_id,
        data_source=data_source,
    )

    async def pre_tool_use(
        input_data: HookInput,
        session_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        tool_use_id = input_data["tool_use_id"]  # type: ignore[typeddict-item]
        _tool_start_times[tool_use_id] = time.monotonic()
        log.info(
            "extraction.tool_invoked",
            tool=input_data["tool_name"],  # type: ignore[typeddict-item]
            tool_use_id=tool_use_id,
            args=input_data["tool_input"],  # type: ignore[typeddict-item]
        )
        return {}

    async def post_tool_use(
        input_data: HookInput,
        session_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        tool_use_id = input_data["tool_use_id"]  # type: ignore[typeddict-item]
        start = _tool_start_times.pop(tool_use_id, None)
        duration_ms: float | None = None
        if start is not None:
            duration_ms = (time.monotonic() - start) * 1000

        response = input_data.get("tool_response")  # type: ignore[attr-defined]
        is_error = False
        if isinstance(response, dict):
            is_error = response.get("is_error", False)

        log.info(
            "extraction.tool_completed",
            tool=input_data["tool_name"],  # type: ignore[typeddict-item]
            tool_use_id=tool_use_id,
            duration_ms=(round(duration_ms, 1) if duration_ms is not None else None),
            is_error=is_error,
        )
        return {}

    async def post_tool_use_failure(
        input_data: HookInput,
        session_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        tool_use_id = input_data["tool_use_id"]  # type: ignore[typeddict-item]
        _tool_start_times.pop(tool_use_id, None)
        log.error(
            "extraction.tool_failed",
            tool=input_data["tool_name"],  # type: ignore[typeddict-item]
            tool_use_id=tool_use_id,
            error=input_data["error"],  # type: ignore[typeddict-item]
        )
        return {}

    async def stop(
        input_data: HookInput,
        session_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        log.info(
            "extraction.agent_stopped",
            stop_hook_active=input_data["stop_hook_active"],  # type: ignore[typeddict-item]
        )
        return {}

    pre_cb: HookCallback = pre_tool_use
    post_cb: HookCallback = post_tool_use
    fail_cb: HookCallback = post_tool_use_failure
    stop_cb: HookCallback = stop

    return {
        "PreToolUse": [
            HookMatcher(matcher="^mcp__", hooks=[pre_cb]),
        ],
        "PostToolUse": [
            HookMatcher(matcher="^mcp__", hooks=[post_cb]),
        ],
        "PostToolUseFailure": [
            HookMatcher(hooks=[fail_cb]),
        ],
        "Stop": [
            HookMatcher(hooks=[stop_cb]),
        ],
    }
