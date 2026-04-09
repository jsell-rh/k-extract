"""Streaming JSONL writer for kartograph-compatible mutation output.

Writes one JSON line per operation in append mode. Partial output
from an interrupted run is always valid JSONL.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path

from k_extract.domain.mutations import MutationOperation


class JsonlWriter:
    """Streaming JSONL writer that appends one JSON line per operation.

    Thread-safe and async-safe via a threading lock. Each write is
    flushed immediately so partial output is always valid.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """The output file path."""
        return self._path

    async def write_operation(self, operation: MutationOperation) -> None:
        """Write a single operation as one JSON line.

        Acquires the lock, appends the JSON line, and flushes.
        """
        line = json.dumps(
            operation.model_dump(exclude_none=True), separators=(",", ":")
        )
        with self._lock, self._path.open("a") as f:
            f.write(line + "\n")

    async def write_operations(self, operations: Sequence[MutationOperation]) -> None:
        """Write multiple operations as consecutive JSON lines.

        All operations in a single batch are written under one lock
        acquisition for efficiency.
        """
        lines = [
            json.dumps(op.model_dump(exclude_none=True), separators=(",", ":"))
            for op in operations
        ]
        with self._lock, self._path.open("a") as f:
            for line in lines:
                f.write(line + "\n")
