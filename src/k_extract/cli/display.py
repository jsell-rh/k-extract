"""Rich display layer for k-extract CLI.

Provides console output, animated spinners for LLM calls, and
streaming thinking display for the init guided session.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from collections.abc import Generator

_console: Console | None = None


def get_console() -> Console:
    """Return a shared Console instance for consistent Rich output."""
    global _console  # noqa: PLW0603
    if _console is None:
        _console = Console()
    return _console


@contextmanager
def spinner(message: str, console: Console | None = None) -> Generator[None]:
    """Show an animated spinner with a message during a long operation.

    On exit, replaces the spinner line with a checkmark and "done" message.

    Args:
        message: Status message to display alongside the spinner.
        console: Console instance to use. Uses shared console if None.
    """
    c = console or get_console()
    with c.status(message, spinner="dots"):
        yield
    c.print(f"[green]✓[/green] {message} done")


def stream_thinking(console: Console, text: str, width: int | None = None) -> None:
    """Print a dim, overwriting status line showing LLM thinking.

    Displays a truncated snippet of the LLM's latest output so the user
    can see the AI is working without cluttering the terminal.

    Args:
        console: Rich Console instance.
        text: Latest text chunk from the LLM response.
        width: Maximum width for truncation. Uses terminal width if None.
    """
    max_width = width or console.width
    # Take the last non-empty line of the text chunk
    line = text.rstrip().rsplit("\n", 1)[-1].strip()
    if not line:
        return
    if len(line) > max_width - 4:
        line = line[: max_width - 4] + "..."
    console.print(f"\r{line}", style="dim", end="", highlight=False)


def clear_thinking(console: Console) -> None:
    """Clear the thinking status line after LLM call completes."""
    # Overwrite the line with blanks then return cursor
    console.print("\r" + " " * console.width + "\r", end="", highlight=False)
