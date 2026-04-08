"""Tests for agent infrastructure: usage tracking, hooks, logging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from k_extract.extraction.agent import (
    CumulativeUsage,
    UsageStats,
    _get_int,
    _handle_assistant_message,
    format_worker_id,
    run_agent,
)
from k_extract.extraction.hooks import _tool_start_times, create_hooks
from k_extract.extraction.logging import (
    ConversationLogger,
    configure_logging,
    get_logger,
)

# ------------------------------------------------------------------ #
# UsageStats tests
# ------------------------------------------------------------------ #


class TestUsageStats:
    def test_initial_values(self) -> None:
        stats = UsageStats()
        assert stats.input_tokens == 0
        assert stats.output_tokens == 0
        assert stats.cache_creation_input_tokens == 0
        assert stats.cache_read_input_tokens == 0
        assert stats.cost_usd is None

    def test_accumulate_message(self) -> None:
        stats = UsageStats()
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
        stats.accumulate_message(usage, "msg-1")
        assert stats.input_tokens == 100
        assert stats.output_tokens == 50
        assert stats.cache_creation_input_tokens == 10
        assert stats.cache_read_input_tokens == 20

    def test_accumulate_multiple_messages(self) -> None:
        stats = UsageStats()
        stats.accumulate_message({"input_tokens": 100, "output_tokens": 50}, "msg-1")
        stats.accumulate_message({"input_tokens": 200, "output_tokens": 80}, "msg-2")
        assert stats.input_tokens == 300
        assert stats.output_tokens == 130

    def test_deduplicate_by_message_id(self) -> None:
        stats = UsageStats()
        usage = {"input_tokens": 100, "output_tokens": 50}
        stats.accumulate_message(usage, "msg-1")
        stats.accumulate_message(usage, "msg-1")  # duplicate
        assert stats.input_tokens == 100
        assert stats.output_tokens == 50

    def test_accumulate_none_usage(self) -> None:
        stats = UsageStats()
        stats.accumulate_message(None, "msg-1")
        assert stats.input_tokens == 0

    def test_accumulate_none_message_id(self) -> None:
        """Messages with None ID are always accumulated (no dedup)."""
        stats = UsageStats()
        stats.accumulate_message({"input_tokens": 100}, None)
        stats.accumulate_message({"input_tokens": 200}, None)
        assert stats.input_tokens == 300

    def test_apply_final_overrides(self) -> None:
        stats = UsageStats()
        stats.accumulate_message({"input_tokens": 100, "output_tokens": 50}, "msg-1")
        result = ResultMessage(
            subtype="success",
            duration_ms=1000,
            duration_api_ms=800,
            is_error=False,
            num_turns=5,
            session_id="sess-1",
            total_cost_usd=0.05,
            usage={
                "input_tokens": 500,
                "output_tokens": 200,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 40,
            },
        )
        stats.apply_final(result)
        assert stats.input_tokens == 500
        assert stats.output_tokens == 200
        assert stats.cache_creation_input_tokens == 30
        assert stats.cache_read_input_tokens == 40
        assert stats.cost_usd == 0.05

    def test_apply_final_no_usage(self) -> None:
        """If result has no usage, per-message sums are kept."""
        stats = UsageStats()
        stats.accumulate_message({"input_tokens": 100}, "msg-1")
        result = ResultMessage(
            subtype="success",
            duration_ms=1000,
            duration_api_ms=800,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            usage=None,
            total_cost_usd=None,
        )
        stats.apply_final(result)
        assert stats.input_tokens == 100
        assert stats.cost_usd is None

    def test_apply_final_cost_only(self) -> None:
        """Cost is set even when usage dict is absent."""
        stats = UsageStats()
        result = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=0.01,
            usage=None,
        )
        stats.apply_final(result)
        assert stats.cost_usd == 0.01


# ------------------------------------------------------------------ #
# CumulativeUsage tests
# ------------------------------------------------------------------ #


class TestCumulativeUsage:
    def test_add_single_job(self) -> None:
        cum = CumulativeUsage()
        stats = UsageStats(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=20,
            cost_usd=0.05,
        )
        cum.add(stats)
        assert cum.input_tokens == 100
        assert cum.output_tokens == 50
        assert cum.cost_usd == 0.05

    def test_add_multiple_jobs(self) -> None:
        cum = CumulativeUsage()
        cum.add(UsageStats(input_tokens=100, output_tokens=50, cost_usd=0.05))
        cum.add(UsageStats(input_tokens=200, output_tokens=80, cost_usd=0.03))
        assert cum.input_tokens == 300
        assert cum.output_tokens == 130
        assert cum.cost_usd == pytest.approx(0.08)

    def test_add_none_cost(self) -> None:
        """None cost does not affect cumulative total."""
        cum = CumulativeUsage()
        cum.add(UsageStats(input_tokens=100, cost_usd=None))
        assert cum.cost_usd == 0.0


# ------------------------------------------------------------------ #
# format_worker_id tests
# ------------------------------------------------------------------ #


class TestFormatWorkerId:
    def test_single_digit(self) -> None:
        assert format_worker_id(1) == "01"
        assert format_worker_id(9) == "09"

    def test_double_digit(self) -> None:
        assert format_worker_id(10) == "10"
        assert format_worker_id(99) == "99"

    def test_triple_digit(self) -> None:
        assert format_worker_id(100) == "100"


# ------------------------------------------------------------------ #
# _get_int tests
# ------------------------------------------------------------------ #


class TestGetInt:
    def test_present(self) -> None:
        assert _get_int({"input_tokens": 42}, "input_tokens") == 42

    def test_missing(self) -> None:
        assert _get_int({}, "input_tokens") == 0

    def test_none_value(self) -> None:
        assert _get_int({"input_tokens": None}, "input_tokens") == 0


# ------------------------------------------------------------------ #
# _handle_assistant_message tests
# ------------------------------------------------------------------ #


class TestHandleAssistantMessage:
    def test_accumulates_usage(self) -> None:
        usage = UsageStats()
        msg = AssistantMessage(
            content=[TextBlock(text="thinking...")],
            model="test",
            usage={"input_tokens": 100, "output_tokens": 50},
            message_id="msg-1",
        )
        _handle_assistant_message(msg, usage=usage, conv_logger=None)
        assert usage.input_tokens == 100

    def test_logs_text_block_to_conversation(self, tmp_path: Path) -> None:
        conv_logger = ConversationLogger(tmp_path, "01")
        usage = UsageStats()
        msg = AssistantMessage(
            content=[TextBlock(text="hello")],
            model="test",
            message_id="msg-1",
        )
        _handle_assistant_message(msg, usage=usage, conv_logger=conv_logger)
        conv_logger.close()

        lines = (tmp_path / "worker-01.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "assistant_text"
        assert entry["text"] == "hello"

    def test_logs_tool_use_block_to_conversation(self, tmp_path: Path) -> None:
        conv_logger = ConversationLogger(tmp_path, "02")
        usage = UsageStats()
        msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-1", name="manage_entity", input={"slug": "repo:test"}
                )
            ],
            model="test",
            message_id="msg-1",
        )
        _handle_assistant_message(msg, usage=usage, conv_logger=conv_logger)
        conv_logger.close()

        lines = (tmp_path / "worker-02.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["type"] == "tool_use"
        assert entry["tool"] == "manage_entity"
        assert entry["tool_use_id"] == "tu-1"


# ------------------------------------------------------------------ #
# ConversationLogger tests
# ------------------------------------------------------------------ #


class TestConversationLogger:
    def test_creates_file(self, tmp_path: Path) -> None:
        logger = ConversationLogger(tmp_path, "01")
        assert (tmp_path / "worker-01.jsonl").exists()
        logger.close()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "logs" / "conv"
        logger = ConversationLogger(nested, "01")
        assert (nested / "worker-01.jsonl").exists()
        logger.close()

    def test_writes_jsonl(self, tmp_path: Path) -> None:
        logger = ConversationLogger(tmp_path, "01")
        logger.log_message("test", {"key": "value"})
        logger.log_message("test2", {"key2": "value2"})
        logger.close()

        lines = (tmp_path / "worker-01.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "test"
        assert json.loads(lines[1])["type"] == "test2"

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        logger1 = ConversationLogger(tmp_path, "01")
        logger1.log_message("first", {})
        logger1.close()

        logger2 = ConversationLogger(tmp_path, "01")
        logger2.log_message("second", {})
        logger2.close()

        lines = (tmp_path / "worker-01.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2


# ------------------------------------------------------------------ #
# structlog configuration tests
# ------------------------------------------------------------------ #


class TestStructlogConfig:
    def test_configure_json_output(self) -> None:
        configure_logging(json_output=True)
        log = get_logger(test="value")
        # Should not raise
        assert log is not None

    def test_configure_color_output(self) -> None:
        configure_logging(json_output=False)
        log = get_logger(test="value")
        assert log is not None

    def test_get_logger_binds_context(self) -> None:
        configure_logging(json_output=True)
        log = get_logger(worker_id="01", job_id="job-1")
        assert log is not None


# ------------------------------------------------------------------ #
# Hook tests
# ------------------------------------------------------------------ #


class TestHooks:
    def setup_method(self) -> None:
        _tool_start_times.clear()

    @pytest.mark.asyncio
    async def test_pre_tool_use_hook(self) -> None:
        configure_logging(json_output=True)
        hooks = create_hooks(worker_id="01", job_id="job-1", data_source="test-source")
        assert "PreToolUse" in hooks
        matcher = hooks["PreToolUse"][0]
        assert matcher.matcher == "^mcp__"

        hook_fn = matcher.hooks[0]
        input_data: dict[str, Any] = {
            "hook_event_name": "PreToolUse",
            "session_id": "sess-1",
            "transcript_path": "/tmp/test",
            "cwd": "/tmp",
            "tool_name": "mcp__extraction-tools__manage_entity",
            "tool_input": {"slug": "repo:test"},
            "tool_use_id": "tu-1",
        }
        result = await hook_fn(input_data, "sess-1", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_post_tool_use_hook(self) -> None:
        configure_logging(json_output=True)
        hooks = create_hooks(worker_id="01", job_id="job-1", data_source="test-source")
        matcher = hooks["PostToolUse"][0]
        assert matcher.matcher == "^mcp__"

        hook_fn = matcher.hooks[0]
        input_data: dict[str, Any] = {
            "hook_event_name": "PostToolUse",
            "session_id": "sess-1",
            "transcript_path": "/tmp/test",
            "cwd": "/tmp",
            "tool_name": "mcp__extraction-tools__manage_entity",
            "tool_input": {"slug": "repo:test"},
            "tool_response": {"content": [{"type": "text", "text": "ok"}]},
            "tool_use_id": "tu-1",
        }
        result = await hook_fn(input_data, "sess-1", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_post_tool_use_failure_hook(self) -> None:
        configure_logging(json_output=True)
        hooks = create_hooks(worker_id="01", job_id="job-1", data_source="test-source")
        assert "PostToolUseFailure" in hooks
        matcher = hooks["PostToolUseFailure"][0]
        # PostToolUseFailure has no matcher filter (catches all)
        assert matcher.matcher is None

        hook_fn = matcher.hooks[0]
        input_data: dict[str, Any] = {
            "hook_event_name": "PostToolUseFailure",
            "session_id": "sess-1",
            "transcript_path": "/tmp/test",
            "cwd": "/tmp",
            "tool_name": "mcp__extraction-tools__manage_entity",
            "tool_input": {"slug": "repo:test"},
            "tool_use_id": "tu-1",
            "error": "Connection refused",
        }
        result = await hook_fn(input_data, "sess-1", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_stop_hook(self) -> None:
        configure_logging(json_output=True)
        hooks = create_hooks(worker_id="01", job_id="job-1", data_source="test-source")
        assert "Stop" in hooks
        hook_fn = hooks["Stop"][0].hooks[0]
        input_data: dict[str, Any] = {
            "hook_event_name": "Stop",
            "session_id": "sess-1",
            "transcript_path": "/tmp/test",
            "cwd": "/tmp",
            "stop_hook_active": True,
        }
        result = await hook_fn(input_data, "sess-1", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_pre_post_tool_duration_tracking(self) -> None:
        """PreToolUse starts timing, PostToolUse reports duration."""
        configure_logging(json_output=True)
        hooks = create_hooks(worker_id="01", job_id="job-1", data_source="test-source")
        pre_fn = hooks["PreToolUse"][0].hooks[0]
        post_fn = hooks["PostToolUse"][0].hooks[0]

        pre_input: dict[str, Any] = {
            "hook_event_name": "PreToolUse",
            "session_id": "sess-1",
            "transcript_path": "/tmp/test",
            "cwd": "/tmp",
            "tool_name": "mcp__extraction-tools__search_entities",
            "tool_input": {},
            "tool_use_id": "tu-timing",
        }
        await pre_fn(pre_input, "sess-1", {})

        post_input: dict[str, Any] = {
            "hook_event_name": "PostToolUse",
            "session_id": "sess-1",
            "transcript_path": "/tmp/test",
            "cwd": "/tmp",
            "tool_name": "mcp__extraction-tools__search_entities",
            "tool_input": {},
            "tool_response": {"content": [{"type": "text", "text": "ok"}]},
            "tool_use_id": "tu-timing",
        }
        result = await post_fn(post_input, "sess-1", {})
        assert result == {}

    def test_hooks_structure(self) -> None:
        configure_logging(json_output=True)
        hooks = create_hooks(worker_id="01", job_id="job-1", data_source="test-source")
        assert set(hooks.keys()) == {
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "Stop",
        }
        # Each event has exactly one HookMatcher
        for _event, matchers in hooks.items():
            assert len(matchers) == 1
            assert len(matchers[0].hooks) == 1


# ------------------------------------------------------------------ #
# run_agent integration test (mocked SDK)
# ------------------------------------------------------------------ #


class TestRunAgent:
    @pytest.mark.asyncio
    async def test_success_flow(self, tmp_path: Path) -> None:
        """Test successful agent run with mocked SDK client."""
        configure_logging(json_output=True)

        assistant_msg = AssistantMessage(
            content=[TextBlock(text="Processing files...")],
            model="test-model",
            usage={"input_tokens": 100, "output_tokens": 50},
            message_id="msg-1",
        )
        result_msg = ResultMessage(
            subtype="success",
            duration_ms=5000,
            duration_api_ms=4000,
            is_error=False,
            num_turns=3,
            session_id="sess-1",
            total_cost_usd=0.05,
            usage={
                "input_tokens": 500,
                "output_tokens": 200,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 40,
            },
        )

        async def mock_receive_messages():
            yield assistant_msg
            yield result_msg

        mock_client = AsyncMock()
        mock_client.receive_messages = mock_receive_messages

        with patch("k_extract.extraction.agent.ClaudeSDKClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_agent(
                worker_id="01",
                system_prompt="You are an extraction agent.",
                initial_message="Process these files.",
                mcp_server=MagicMock(),
                job_id="job-1",
                data_source="test-source",
                cwd=tmp_path,
                conversation_log_dir=tmp_path / "conv",
            )

        assert result.success is True
        assert result.error_message is None
        assert result.usage.input_tokens == 500
        assert result.usage.output_tokens == 200
        assert result.usage.cache_creation_input_tokens == 30
        assert result.usage.cache_read_input_tokens == 40
        assert result.usage.cost_usd == 0.05

        # Conversation log should exist
        conv_file = tmp_path / "conv" / "worker-01.jsonl"
        assert conv_file.exists()
        lines = conv_file.read_text().strip().split("\n")
        assert len(lines) == 2  # assistant_text + result

    @pytest.mark.asyncio
    async def test_error_flow(self, tmp_path: Path) -> None:
        """Test agent error with error subtype."""
        configure_logging(json_output=True)

        result_msg = ResultMessage(
            subtype="error",
            duration_ms=1000,
            duration_api_ms=800,
            is_error=True,
            num_turns=1,
            session_id="sess-1",
            errors=["Rate limit exceeded"],
        )

        async def mock_receive_messages():
            yield result_msg

        mock_client = AsyncMock()
        mock_client.receive_messages = mock_receive_messages

        with patch("k_extract.extraction.agent.ClaudeSDKClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_agent(
                worker_id="01",
                system_prompt="test",
                initial_message="test",
                mcp_server=MagicMock(),
                job_id="job-1",
                data_source="test-source",
                cwd=tmp_path,
            )

        assert result.success is False
        assert result.error_message == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_cancelled_flow(self, tmp_path: Path) -> None:
        """Test agent cancellation."""
        configure_logging(json_output=True)

        result_msg = ResultMessage(
            subtype="cancelled",
            duration_ms=500,
            duration_api_ms=400,
            is_error=True,
            num_turns=1,
            session_id="sess-1",
            result="User cancelled",
        )

        async def mock_receive_messages():
            yield result_msg

        mock_client = AsyncMock()
        mock_client.receive_messages = mock_receive_messages

        with patch("k_extract.extraction.agent.ClaudeSDKClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_agent(
                worker_id="01",
                system_prompt="test",
                initial_message="test",
                mcp_server=MagicMock(),
                job_id="job-1",
                data_source="test-source",
                cwd=tmp_path,
            )

        assert result.success is False
        assert result.error_message == "User cancelled"

    @pytest.mark.asyncio
    async def test_loop_exit_no_subtype(self, tmp_path: Path) -> None:
        """Loop exit without subtype is treated as success."""
        configure_logging(json_output=True)

        assistant_msg = AssistantMessage(
            content=[TextBlock(text="done")],
            model="test",
            message_id="msg-1",
        )

        async def mock_receive_messages():
            yield assistant_msg

        mock_client = AsyncMock()
        mock_client.receive_messages = mock_receive_messages

        with patch("k_extract.extraction.agent.ClaudeSDKClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_agent(
                worker_id="01",
                system_prompt="test",
                initial_message="test",
                mcp_server=MagicMock(),
                job_id="job-1",
                data_source="test-source",
                cwd=tmp_path,
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_sdk_exception(self, tmp_path: Path) -> None:
        """SDK exceptions are caught and returned as failure."""
        configure_logging(json_output=True)

        with patch("k_extract.extraction.agent.ClaudeSDKClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("Connection refused")
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_agent(
                worker_id="01",
                system_prompt="test",
                initial_message="test",
                mcp_server=MagicMock(),
                job_id="job-1",
                data_source="test-source",
                cwd=tmp_path,
            )

        assert result.success is False
        assert "Connection refused" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_no_conversation_log_when_disabled(self, tmp_path: Path) -> None:
        """Conversation logging is off when conversation_log_dir is None."""
        configure_logging(json_output=True)

        assistant_msg = AssistantMessage(
            content=[TextBlock(text="done")],
            model="test",
            message_id="msg-1",
        )
        result_msg = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
        )

        async def mock_receive_messages():
            yield assistant_msg
            yield result_msg

        mock_client = AsyncMock()
        mock_client.receive_messages = mock_receive_messages

        with patch("k_extract.extraction.agent.ClaudeSDKClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await run_agent(
                worker_id="01",
                system_prompt="test",
                initial_message="test",
                mcp_server=MagicMock(),
                job_id="job-1",
                data_source="test-source",
                cwd=tmp_path,
                conversation_log_dir=None,
            )

        assert result.success is True
        # No JSONL files should be created in tmp_path
        assert list(tmp_path.glob("*.jsonl")) == []

    @pytest.mark.asyncio
    async def test_tool_use_blocks_in_message(self, tmp_path: Path) -> None:
        """ToolUseBlocks in AssistantMessage are logged to conversation."""
        configure_logging(json_output=True)

        msg = AssistantMessage(
            content=[
                TextBlock(text="Let me search..."),
                ToolUseBlock(
                    id="tu-1",
                    name="mcp__extraction-tools__search_entities",
                    input={"entity_type": "TestCase"},
                ),
            ],
            model="test",
            usage={"input_tokens": 100, "output_tokens": 50},
            message_id="msg-1",
        )
        result_msg = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
        )

        async def mock_receive_messages():
            yield msg
            yield result_msg

        mock_client = AsyncMock()
        mock_client.receive_messages = mock_receive_messages

        with patch("k_extract.extraction.agent.ClaudeSDKClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            conv_dir = tmp_path / "conv"
            result = await run_agent(
                worker_id="01",
                system_prompt="test",
                initial_message="test",
                mcp_server=MagicMock(),
                job_id="job-1",
                data_source="test-source",
                cwd=tmp_path,
                conversation_log_dir=conv_dir,
            )

        assert result.success is True
        lines = (conv_dir / "worker-01.jsonl").read_text().strip().split("\n")
        # text block + tool_use block + result
        assert len(lines) == 3
        types = [json.loads(line)["type"] for line in lines]
        assert types == ["assistant_text", "tool_use", "result"]
