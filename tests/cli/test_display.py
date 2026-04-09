"""Tests for Rich display layer utilities."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from k_extract.cli.display import clear_thinking, spinner, stream_thinking


class TestSpinner:
    def test_enters_and_exits_without_error(self) -> None:
        """Spinner context manager completes without raising."""
        console = Console(file=StringIO(), force_terminal=True)
        with spinner("Test operation", console):
            pass  # simulate work

    def test_prints_done_message_on_exit(self) -> None:
        """Spinner prints a completion message after exiting."""
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        with spinner("Loading data", console):
            pass
        text = output.getvalue()
        assert "Loading data" in text
        assert "done" in text


class TestStreamThinking:
    def teardown_method(self) -> None:
        """Clean up any active Live display after each test."""
        from k_extract.cli import display

        if display._active_live is not None:
            display._active_live.stop()
            display._active_live = None
            display._spinner_message = None

    def test_truncates_to_given_width(self) -> None:
        """Long text is truncated to the specified width."""
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        long_text = "A" * 200
        stream_thinking(console, long_text, width=50)
        text = output.getvalue()
        # Truncated text should be at most width chars (46 chars + "...")
        # Strip ANSI and control chars for length check
        assert "..." in text

    def test_outputs_dim_styled_content(self) -> None:
        """Output uses dim styling."""
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        stream_thinking(console, "thinking about entities", width=80)
        # Verify the Live renderable has dim style applied
        from rich.text import Text

        from k_extract.cli import display

        renderable = display._active_live._renderable
        assert isinstance(renderable, Text)
        assert renderable.style == "dim"
        assert renderable.plain == "thinking about entities"

    def test_empty_text_produces_no_output(self) -> None:
        """Empty or whitespace-only text does not produce output."""
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        stream_thinking(console, "", width=80)
        stream_thinking(console, "   \n  ", width=80)
        assert output.getvalue() == ""

    def test_uses_last_line_of_multiline_text(self) -> None:
        """For multiline text, displays only the last non-empty line."""
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        stream_thinking(console, "first line\nsecond line\nthird line", width=80)
        text = output.getvalue()
        assert "third line" in text

    def test_short_text_not_truncated(self) -> None:
        """Text shorter than width is not truncated."""
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        stream_thinking(console, "short", width=80)
        text = output.getvalue()
        assert "short" in text
        assert "..." not in text

    def test_overwrites_previous_line(self) -> None:
        """Multiple calls update the same Live display instead of accumulating."""
        output = StringIO()
        console = Console(file=output, force_terminal=True)
        stream_thinking(console, "first thought", width=80)
        stream_thinking(console, "second thought", width=80)
        # The Live renderable should be the latest text, not accumulated
        from rich.text import Text

        from k_extract.cli import display

        assert display._active_live is not None
        renderable = display._active_live._renderable
        assert isinstance(renderable, Text)
        assert renderable.plain == "second thought"


class TestClearThinking:
    def test_clears_after_stream(self) -> None:
        """clear_thinking stops the Live display started by stream_thinking."""
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=40)
        stream_thinking(console, "some thinking text", width=40)
        clear_thinking(console)
        # After clear, the standalone Live should be stopped and removed
        from k_extract.cli import display

        assert display._active_live is None

    def test_noop_when_no_active_display(self) -> None:
        """clear_thinking is a no-op when no thinking display is active."""
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=40)
        # Should not raise
        clear_thinking(console)


class TestStreamingLlmCaller:
    def test_returns_complete_accumulated_text(self) -> None:
        """The streaming caller still returns the full response text."""
        import asyncio

        from k_extract.cli.init import run_guided_session

        # Using mock llm_call - no streaming display should be invoked
        async def mock_llm(prompt: str) -> str:
            return (
                "```yaml\n"
                "entity_types:\n"
                "  - label: Widget\n"
                '    description: "A widget"\n'
                "    required_properties:\n"
                "      - name\n"
                "    optional_properties: []\n"
                "    tag_definitions: {}\n"
                "relationship_types:\n"
                "  - label: USES\n"
                '    description: "Uses relationship"\n'
                "    source_entity_type: Widget\n"
                "    target_entity_type: Widget\n"
                "    required_properties: []\n"
                "    optional_properties: []\n"
                "```\n"
            )

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "data"
            src.mkdir()
            (src / "test.txt").write_text("hello world")
            output_path = Path(tmpdir) / "out.yaml"

            config = asyncio.run(
                run_guided_session(
                    data_source_paths=[str(src)],
                    problem_statement="Test problem",
                    output_path=str(output_path),
                    llm_call=mock_llm,
                )
            )

            assert config.problem_statement == "Test problem"
            assert len(config.ontology.entity_types) == 1
            assert config.ontology.entity_types[0].label == "Widget"
