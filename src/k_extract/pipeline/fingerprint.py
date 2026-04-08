"""SHA256 hashing, fingerprint computation, and resume logic.

Computes a cryptographic environment fingerprint from config, prompts,
model ID, and source file contents. Determines whether a previous run
can be resumed based on fingerprint matching.
"""

from __future__ import annotations

import enum
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from k_extract.pipeline.database import EnvironmentFingerprint


def hash_file(file_path: str | Path) -> tuple[str, str]:
    """Compute SHA256 hash of a single file.

    Args:
        file_path: Path to the file to hash.

    Returns:
        Tuple of (relative_or_absolute_path_as_string, hex_digest).
    """
    path = Path(file_path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return str(file_path), h.hexdigest()


def hash_files_parallel(
    file_paths: list[str | Path],
    max_workers: int | None = None,
) -> list[tuple[str, str]]:
    """Hash multiple files in parallel using threads.

    Files are independent I/O operations, so thread parallelism
    provides speedup.

    Args:
        file_paths: List of file paths to hash.
        max_workers: Maximum thread pool size (None = default).

    Returns:
        List of (filepath, hex_digest) tuples, sorted by filepath
        for deterministic ordering.
    """
    if not file_paths:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(hash_file, file_paths))

    return sorted(results, key=lambda x: x[0])


def compute_fingerprint(
    config_contents: str,
    prompt_templates: str,
    model_id: str,
    file_hashes: list[tuple[str, str]],
) -> str:
    """Compute a deterministic environment fingerprint.

    The fingerprint is a SHA256 over the concatenation of:
    - Config file contents
    - Generated prompt templates
    - Model ID
    - Sorted file content hashes

    Args:
        config_contents: Full text of the config file.
        prompt_templates: Generated prompt templates (system prompt + job template).
        model_id: The model identifier.
        file_hashes: Sorted list of (filepath, hash) tuples.

    Returns:
        Hex digest of the combined SHA256 hash.
    """
    h = hashlib.sha256()
    h.update(config_contents.encode("utf-8"))
    h.update(prompt_templates.encode("utf-8"))
    h.update(model_id.encode("utf-8"))
    for filepath, file_hash in file_hashes:
        h.update(filepath.encode("utf-8"))
        h.update(file_hash.encode("utf-8"))
    return h.hexdigest()


class ResumeAction(enum.Enum):
    """Actions determined by the resume logic."""

    FRESH_START = "fresh_start"
    RESUME = "resume"
    HARD_STOP = "hard_stop"


@dataclass
class ResumeDecision:
    """Result of the resume logic evaluation."""

    action: ResumeAction
    message: str


def evaluate_resume(
    session: Session,
    current_fingerprint: str,
    force: bool = False,
) -> ResumeDecision:
    """Evaluate whether to start fresh, resume, or hard stop.

    Logic:
    - No previous run → fresh start
    - Previous run + matching fingerprint → resume (skip completed jobs)
    - Previous run + mismatched fingerprint → hard stop with error
    - --force flag → discard previous state, fresh start

    Args:
        session: Database session to query for previous fingerprints.
        current_fingerprint: The fingerprint computed for this run.
        force: If True, discard previous state and start fresh.

    Returns:
        ResumeDecision with action and explanatory message.
    """
    previous = (
        session.query(EnvironmentFingerprint)
        .order_by(EnvironmentFingerprint.created_at.desc())
        .first()
    )

    if previous is None:
        return ResumeDecision(
            action=ResumeAction.FRESH_START,
            message="No previous run found. Starting fresh.",
        )

    if force:
        return ResumeDecision(
            action=ResumeAction.FRESH_START,
            message="--force specified. Discarding previous state and starting fresh.",
        )

    if previous.fingerprint == current_fingerprint:
        return ResumeDecision(
            action=ResumeAction.RESUME,
            message="Environment unchanged. Resuming previous run.",
        )

    return ResumeDecision(
        action=ResumeAction.HARD_STOP,
        message=(
            "Environment has changed since the previous run. "
            "Cannot resume — results would be inconsistent. "
            "Use --force to discard previous state and start fresh."
        ),
    )


def store_fingerprint(
    session: Session,
    fingerprint: str,
    config_hash: str,
    model_id: str,
) -> EnvironmentFingerprint:
    """Store an environment fingerprint in the database.

    Args:
        session: Database session.
        fingerprint: The computed environment fingerprint.
        config_hash: SHA256 of the config file alone.
        model_id: The model identifier used.

    Returns:
        The created EnvironmentFingerprint record.
    """
    record = EnvironmentFingerprint(
        fingerprint=fingerprint,
        created_at=datetime.now(UTC),
        config_hash=config_hash,
        model_id=model_id,
    )
    session.add(record)
    session.commit()
    return record
