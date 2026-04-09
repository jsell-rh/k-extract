"""File discovery, inventory reporting, and character counting.

Scans data source paths recursively, collects file metadata,
groups by parent directory, and generates data inventory reports
for the guided session (init Step 2).
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pathspec


@dataclass
class DiscoveredFile:
    """Metadata for a single discovered file."""

    path: str
    size: int
    char_count: int
    file_type: str


@dataclass
class FolderGroup:
    """Files grouped by parent directory."""

    directory: str
    files: list[DiscoveredFile] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        """Total size in bytes of all files in this group."""
        return sum(f.size for f in self.files)

    @property
    def total_chars(self) -> int:
        """Total character count of all files in this group."""
        return sum(f.char_count for f in self.files)


@dataclass
class DataSourceInventory:
    """Inventory report for a single data source."""

    name: str
    path: str
    file_count: int
    total_size: int
    total_chars: int
    file_type_counts: dict[str, int]
    directories: list[str]
    patterns: list[str]


def _get_file_type(path: str) -> str:
    """Extract file type (extension) from a path.

    Returns the extension without the leading dot, lowercased.
    Files with no extension return an empty string.
    """
    ext = os.path.splitext(path)[1]
    if ext:
        return ext[1:].lower()
    return ""


def _count_chars(file_path: Path) -> int:
    """Count characters in a file, returning 0 for binary/unreadable files."""
    try:
        return len(file_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError):
        return 0


def _detect_patterns(root: Path, files: list[DiscoveredFile]) -> list[str]:
    """Detect recognizable patterns in the data source.

    Checks for common project structures like Python packages,
    markdown documentation, etc.
    """
    patterns: list[str] = []

    file_types = {f.file_type for f in files}
    file_names = {os.path.basename(f.path) for f in files}

    if "py" in file_types and (
        "__init__.py" in file_names
        or "setup.py" in file_names
        or "pyproject.toml" in file_names
    ):
        patterns.append("Python package")

    if "md" in file_types:
        md_count = sum(1 for f in files if f.file_type == "md")
        if md_count >= 3:
            patterns.append("Markdown documentation")

    if "go" in file_types and "go.mod" in file_names:
        patterns.append("Go module")

    if "rs" in file_types and "Cargo.toml" in file_names:
        patterns.append("Rust crate")

    if ("ts" in file_types or "tsx" in file_types) and "package.json" in file_names:
        patterns.append("Node.js/TypeScript project")

    if (
        ("js" in file_types or "jsx" in file_types)
        and "package.json" in file_names
        and "Node.js/TypeScript project" not in patterns
    ):
        patterns.append("Node.js project")

    if "yaml" in file_types or "yml" in file_types:
        yaml_count = sum(1 for f in files if f.file_type in ("yaml", "yml"))
        if yaml_count >= 3:
            patterns.append("YAML configuration")

    if "Dockerfile" in file_names or "Containerfile" in file_names:
        patterns.append("Container project")

    return patterns


def _load_gitignore_spec(root: Path) -> pathspec.PathSpec | None:
    """Load .gitignore patterns from the data source root, if present.

    Returns None if no .gitignore file exists.
    """
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    text = gitignore_path.read_text(encoding="utf-8", errors="replace")
    return pathspec.PathSpec.from_lines("gitignore", text.splitlines())


def _collect_file_metadata(args: tuple[Path, str]) -> DiscoveredFile:
    """Collect metadata for a single file (stat + char count).

    Designed to run in a thread pool for parallel I/O.
    """
    file_path, rel_str = args
    size = file_path.stat().st_size
    char_count = _count_chars(file_path)
    file_type = _get_file_type(rel_str)
    return DiscoveredFile(
        path=rel_str,
        size=size,
        char_count=char_count,
        file_type=file_type,
    )


def discover_files(
    source_path: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    on_scan_progress: Callable[[int], None] | None = None,
) -> list[DiscoveredFile]:
    """Recursively scan a data source path and collect file metadata.

    Files matched by ``.gitignore`` (if present at the data source root)
    are excluded. Hidden files and directories (dotfiles/dotdirs) are
    always excluded. Per-file I/O (stat + character counting) is
    parallelized across threads.

    Args:
        source_path: Root path to scan.
        on_progress: Optional callback ``(done, total)`` fired as files
            are processed during the metadata-collection phase.
        on_scan_progress: Optional callback ``(found_so_far)`` fired as
            candidate files are discovered during the directory walk.

    Returns:
        List of DiscoveredFile with path, size, char_count, and file_type.
        Paths are relative to the source root.
    """
    root = Path(source_path).resolve()
    if not root.is_dir():
        msg = f"Data source path is not a directory: {root}"
        raise ValueError(msg)

    gitignore_spec = _load_gitignore_spec(root)

    # Phase 1: Walk the directory tree, pruning hidden/ignored dirs
    candidates: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune hidden directories in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        # Prune gitignored directories
        if gitignore_spec is not None:
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [
                d
                for d in dirnames
                if not gitignore_spec.match_file(
                    os.path.join(rel_dir, d) + "/" if rel_dir != "." else d + "/"
                )
            ]

        for filename in filenames:
            if filename.startswith("."):
                continue
            full_path = Path(dirpath) / filename
            rel_str = str(full_path.relative_to(root))
            if gitignore_spec is not None and gitignore_spec.match_file(rel_str):
                continue
            candidates.append((full_path, rel_str))
            if on_scan_progress is not None:
                on_scan_progress(len(candidates))

    if not candidates:
        return []

    # Sort for deterministic ordering after streaming walk
    candidates.sort(key=lambda c: c[1])

    # Phase 2: Collect per-file metadata in parallel (I/O-bound)
    total = len(candidates)
    with ThreadPoolExecutor() as executor:
        files: list[DiscoveredFile] = []
        for done, result in enumerate(
            executor.map(_collect_file_metadata, candidates), 1
        ):
            files.append(result)
            if on_progress is not None:
                on_progress(done, total)

    return files


def group_by_directory(files: list[DiscoveredFile]) -> list[FolderGroup]:
    """Group discovered files by their parent directory.

    Args:
        files: List of discovered files.

    Returns:
        List of FolderGroup, sorted by directory path.
    """
    groups: dict[str, list[DiscoveredFile]] = defaultdict(list)
    for f in files:
        parent = str(Path(f.path).parent)
        groups[parent].append(f)

    return [
        FolderGroup(directory=d, files=group_files)
        for d, group_files in sorted(groups.items())
    ]


def build_inventory(
    name: str,
    source_path: str | Path,
    files: list[DiscoveredFile],
) -> DataSourceInventory:
    """Build a data inventory report for a single data source.

    Args:
        name: Human-readable data source name.
        source_path: Root path of the data source.
        files: Discovered files from this source.

    Returns:
        DataSourceInventory with file type counts, volume, and patterns.
    """
    root = Path(source_path).resolve()

    file_type_counts: dict[str, int] = defaultdict(int)
    for f in files:
        key = f.file_type if f.file_type else "(no extension)"
        file_type_counts[key] += 1

    directories = sorted({str(Path(f.path).parent) for f in files})

    patterns = _detect_patterns(root, files)

    return DataSourceInventory(
        name=name,
        path=str(root),
        file_count=len(files),
        total_size=sum(f.size for f in files),
        total_chars=sum(f.char_count for f in files),
        file_type_counts=dict(sorted(file_type_counts.items())),
        directories=directories,
        patterns=patterns,
    )


def discover_and_inventory(
    data_sources: list[tuple[str, str | Path]],
) -> tuple[dict[str, list[DiscoveredFile]], list[DataSourceInventory]]:
    """Discover files and build inventories for multiple data sources.

    Args:
        data_sources: List of (name, path) tuples.

    Returns:
        Tuple of:
        - Dict mapping source name to discovered files
        - List of DataSourceInventory reports
    """
    all_files: dict[str, list[DiscoveredFile]] = {}
    inventories: list[DataSourceInventory] = []

    for name, path in data_sources:
        files = discover_files(path)
        all_files[name] = files
        inventory = build_inventory(name, path, files)
        inventories.append(inventory)

    return all_files, inventories
