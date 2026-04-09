"""Rich display layer for k-extract CLI.

Provides console output, animated spinners for LLM calls, and
streaming thinking display for the init guided session.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

if TYPE_CHECKING:
    from collections.abc import Generator

_console: Console | None = None
_active_live: Live | None = None
_spinner_message: str | None = None


def get_console() -> Console:
    """Return a shared Console instance for consistent Rich output."""
    global _console  # noqa: PLW0603
    if _console is None:
        _console = Console()
    return _console


@contextmanager
def spinner(message: str, console: Console | None = None) -> Generator[None]:
    """Show an animated spinner with a message during a long operation.

    On exit, replaces the spinner line with a static completion message.
    Stores the Live instance in module state so stream_thinking() can
    update the display in-place during LLM calls.

    Args:
        message: Status message to display alongside the spinner.
        console: Console instance to use. Uses shared console if None.
    """
    global _active_live, _spinner_message  # noqa: PLW0603
    c = console or get_console()
    _spinner_message = message
    _active_live = Live(
        Spinner("dots", text=message),
        console=c,
        transient=True,
    )
    _active_live.start()
    try:
        yield
    finally:
        _active_live.stop()
        _active_live = None
        _spinner_message = None
    c.print(f"[green]✓[/green] {message} done")


def stream_thinking(console: Console, text: str, width: int | None = None) -> None:
    """Print a dim, overwriting status line showing LLM thinking.

    Uses rich.live.Live to overwrite the previous line in-place.
    When called inside a spinner() context, updates the spinner's Live
    display. When called standalone, creates a transient Live display.

    Args:
        console: Rich Console instance.
        text: Latest text chunk from the LLM response.
        width: Maximum width for truncation. Uses terminal width if None.
    """
    global _active_live  # noqa: PLW0603
    max_width = width or console.width
    # Take the last non-empty line of the text chunk
    line = text.rstrip().rsplit("\n", 1)[-1].strip()
    if not line:
        return
    if len(line) > max_width - 4:
        line = line[: max_width - 4] + "..."

    renderable = Text(line, style="dim")

    if _active_live is not None:
        _active_live.update(renderable)
    else:
        _active_live = Live(
            renderable, console=console, transient=True, auto_refresh=False
        )
        _active_live.start(refresh=True)


def clear_thinking(console: Console) -> None:
    """Clear the thinking status line after LLM call completes.

    If inside a spinner() context, restores the spinner display.
    If standalone, stops the Live display (transient=True clears it).
    """
    global _active_live  # noqa: PLW0603
    if _active_live is not None:
        if _spinner_message is not None:
            # Inside a spinner - restore the spinner renderable
            _active_live.update(Spinner("dots", text=_spinner_message))
        else:
            # Standalone - stop and clear
            _active_live.stop()
            _active_live = None
