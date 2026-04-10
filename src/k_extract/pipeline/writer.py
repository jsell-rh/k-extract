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
        self._emitted_ids: set[str] = set()
        # Load existing IDs from the file if resuming
        if self._path.exists():
            with self._path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        oid = obj.get("id")
                        if oid is not None:
                            self._emitted_ids.add(oid)
                    except json.JSONDecodeError:
                        continue

    @property
    def path(self) -> Path:
        """The output file path."""
        return self._path

    async def write_operation(self, operation: MutationOperation) -> None:
        """Write a single operation as one JSON line.

        Skips operations with IDs already emitted (deduplication).
        Acquires the lock, appends the JSON line, and flushes.
        """
        data = operation.model_dump(exclude_none=True)
        oid = data.get("id")
        with self._lock:
            if oid is not None and oid in self._emitted_ids:
                return
            line = json.dumps(data, separators=(",", ":"))
            with self._path.open("a") as f:
                f.write(line + "\n")
            if oid is not None:
                self._emitted_ids.add(oid)

    async def write_operations(self, operations: Sequence[MutationOperation]) -> None:
        """Write multiple operations as consecutive JSON lines.

        Deduplicates by ID — operations with already-emitted IDs are
        silently skipped. All non-duplicate operations in a single batch
        are written under one lock acquisition for efficiency.
        """
        with self._lock, self._path.open("a") as f:
            for op in operations:
                data = op.model_dump(exclude_none=True)
                oid = data.get("id")
                if oid is not None and oid in self._emitted_ids:
                    continue
                line = json.dumps(data, separators=(",", ":"))
                f.write(line + "\n")
                if oid is not None:
                    self._emitted_ids.add(oid)
