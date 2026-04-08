"""Logging infrastructure for k-extract.

Provides structlog configuration with two output modes:
- Color output for human-readable terminal display
- JSON output for machine-consumable log streams

Also provides a conversation logger that streams agent messages
to per-worker JSONL files for debugging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog


def configure_logging(*, json_output: bool = False) -> None:
    """Configure structlog for k-extract.

    Args:
        json_output: If True, emit JSON lines. If False, emit colored
            human-readable output.
    """
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_context: Any) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger with optional initial context bindings.

    Args:
        **initial_context: Key-value pairs to bind to the logger.

    Returns:
        A bound structlog logger.
    """
    return structlog.get_logger(**initial_context)


class ConversationLogger:
    """Streams agent conversation messages to a per-worker JSONL file.

    Each message is written as a single JSON line, flushed immediately
    for crash-safety. Off by default — only created when
    ``--log-conversations`` is enabled.
    """

    def __init__(self, output_dir: Path, worker_id: str) -> None:
        self._output_dir = output_dir
        self._worker_id = worker_id
        self._file_path = output_dir / f"worker-{worker_id}.jsonl"
        output_dir.mkdir(parents=True, exist_ok=True)
        self._file = open(self._file_path, "a", encoding="utf-8")  # noqa: SIM115

    def log_message(self, message_type: str, data: dict[str, Any]) -> None:
        """Write a single message entry as a JSON line.

        Args:
            message_type: The type of message (e.g., "assistant", "result").
            data: The message data to log.
        """
        entry = {"type": message_type, **data}
        self._file.write(json.dumps(entry, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Close the underlying file handle."""
        self._file.close()
