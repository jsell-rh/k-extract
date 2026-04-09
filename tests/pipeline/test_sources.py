"""Tests for file discovery, folder grouping, and data inventory."""

from __future__ import annotations

from pathlib import Path

import pytest

from k_extract.pipeline.sources import (
    DiscoveredFile,
    build_inventory,
    discover_and_inventory,
    discover_files,
    group_by_directory,
)


@pytest.fixture()
def source_tree(tmp_path: Path) -> Path:
    """Create a sample data source directory tree."""
    # Root files
    (tmp_path / "README.md").write_text("# Hello\nWorld")

    # Python package
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text("def main():\n    pass\n")
    (pkg / "utils.py").write_text("def helper():\n    return 42\n")

    # Docs folder
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\nThis is a guide.")
    (docs / "api.md").write_text("# API\nAPI docs here.")
    (docs / "faq.md").write_text("# FAQ\nFrequently asked.")

    # Config files
    (tmp_path / "config.yaml").write_text("key: value\n")

    return tmp_path


class TestDiscoverFiles:
    def test_discovers_all_files(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        paths = {f.path for f in files}
        assert "README.md" in paths
        assert "src/myapp/__init__.py" in paths
        assert "src/myapp/main.py" in paths
        assert "docs/guide.md" in paths
        assert "config.yaml" in paths

    def test_file_metadata(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        by_path = {f.path: f for f in files}

        readme = by_path["README.md"]
        assert readme.size > 0
        assert readme.char_count == len("# Hello\nWorld")
        assert readme.file_type == "md"

        init = by_path["src/myapp/__init__.py"]
        assert init.file_type == "py"
        assert init.char_count == 0  # empty file
        assert init.size == 0

    def test_paths_are_relative(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        for f in files:
            assert not Path(f.path).is_absolute()

    def test_files_sorted_by_path(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        paths = [f.path for f in files]
        assert paths == sorted(paths)

    def test_skips_directories(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        for f in files:
            assert not (source_tree / f.path).is_dir()

    def test_invalid_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a directory"):
            discover_files(tmp_path / "nonexistent")

    def test_file_not_directory_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("hello")
        with pytest.raises(ValueError, match="not a directory"):
            discover_files(f)

    def test_empty_directory(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        files = discover_files(empty)
        assert files == []

    def test_binary_file_zero_chars(self, tmp_path: Path) -> None:
        binary = tmp_path / "image.bin"
        binary.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 100)
        files = discover_files(tmp_path)
        assert len(files) == 1
        assert files[0].char_count == 0
        assert files[0].size > 0

    def test_no_extension_file_type(self, tmp_path: Path) -> None:
        (tmp_path / "Dockerfile").write_text("FROM ubuntu\n")
        files = discover_files(tmp_path)
        assert files[0].file_type == ""

    def test_mixed_case_extension(self, tmp_path: Path) -> None:
        (tmp_path / "README.MD").write_text("hello")
        files = discover_files(tmp_path)
        assert files[0].file_type == "md"

    def test_excludes_git_directory(self, tmp_path: Path) -> None:
        """Files under .git/ should be excluded (VCS metadata)."""
        git_dir = tmp_path / ".git" / "objects"
        git_dir.mkdir(parents=True)
        (git_dir / "abc123").write_bytes(b"\x00\x01\x02")
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (tmp_path / "README.md").write_text("# Hello")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert "README.md" in paths
        assert not any(".git" in p for p in paths)

    def test_excludes_hidden_files_and_dirs(self, tmp_path: Path) -> None:
        """All hidden files/directories (dotfiles/dotdirs) should be excluded."""
        (tmp_path / ".env").write_text("SECRET=123")
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.txt").write_text("secret")
        (tmp_path / "visible.txt").write_text("public")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert paths == {"visible.txt"}

    def test_gitignore_excludes_matching_files(self, tmp_path: Path) -> None:
        """Files matching .gitignore patterns are excluded."""
        (tmp_path / ".gitignore").write_text("*.log\nbuild/\n")
        (tmp_path / "app.py").write_text("print('hi')")
        (tmp_path / "debug.log").write_text("log data")
        build = tmp_path / "build"
        build.mkdir()
        (build / "output.bin").write_bytes(b"\x00\x01")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert paths == {"app.py"}

    def test_no_gitignore_returns_all_files(self, tmp_path: Path) -> None:
        """When no .gitignore is present, all non-hidden files are returned."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.log").write_text("b")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.dat").write_text("c")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert paths == {"a.txt", "b.log", "sub/c.dat"}

    def test_gitignore_negation_pattern(self, tmp_path: Path) -> None:
        """Negation patterns (!) re-include previously excluded files."""
        (tmp_path / ".gitignore").write_text("*.log\n!important.log\n")
        (tmp_path / "debug.log").write_text("debug")
        (tmp_path / "important.log").write_text("keep me")
        (tmp_path / "app.py").write_text("code")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert "debug.log" not in paths
        assert "important.log" in paths
        assert "app.py" in paths

    def test_gitignore_nested_directory_pattern(self, tmp_path: Path) -> None:
        """Directory patterns exclude files in nested directories."""
        (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "main.py").write_text("code")
        cache = pkg / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-312.pyc").write_bytes(b"\x00")
        (tmp_path / "src" / "pkg" / "util.pyc").write_bytes(b"\x00")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert "src/pkg/main.py" in paths
        assert not any("__pycache__" in p for p in paths)
        assert not any(p.endswith(".pyc") for p in paths)

    def test_gitignore_comment_lines_ignored(self, tmp_path: Path) -> None:
        """Comment lines (starting with #) in .gitignore are ignored."""
        (tmp_path / ".gitignore").write_text("# This is a comment\n*.tmp\n")
        (tmp_path / "data.tmp").write_text("temp")
        (tmp_path / "keep.txt").write_text("keep")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert paths == {"keep.txt"}

    def test_gitignore_empty_has_no_effect(self, tmp_path: Path) -> None:
        """An empty .gitignore excludes nothing."""
        (tmp_path / ".gitignore").write_text("")
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.log").write_text("b")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert paths == {"a.txt", "b.log"}

    def test_gitignore_glob_patterns(self, tmp_path: Path) -> None:
        """Glob patterns like *.o and docs/**/*.pdf work correctly."""
        (tmp_path / ".gitignore").write_text("*.o\ndocs/**/*.pdf\n")
        (tmp_path / "main.c").write_text("int main(){}")
        (tmp_path / "main.o").write_bytes(b"\x00")
        docs = tmp_path / "docs" / "guides"
        docs.mkdir(parents=True)
        (docs / "manual.pdf").write_bytes(b"%PDF")
        (docs / "readme.txt").write_text("read me")

        files = discover_files(tmp_path)
        paths = {f.path for f in files}
        assert "main.c" in paths
        assert "main.o" not in paths
        assert "docs/guides/manual.pdf" not in paths
        assert "docs/guides/readme.txt" in paths


class TestGroupByDirectory:
    def test_groups_by_parent(self) -> None:
        files = [
            DiscoveredFile(path="src/a.py", size=10, char_count=10, file_type="py"),
            DiscoveredFile(path="src/b.py", size=20, char_count=20, file_type="py"),
            DiscoveredFile(path="docs/c.md", size=30, char_count=30, file_type="md"),
        ]
        groups = group_by_directory(files)
        assert len(groups) == 2
        dirs = [g.directory for g in groups]
        assert "src" in dirs
        assert "docs" in dirs

    def test_root_files_grouped_under_dot(self) -> None:
        files = [
            DiscoveredFile(path="README.md", size=10, char_count=10, file_type="md"),
        ]
        groups = group_by_directory(files)
        assert len(groups) == 1
        assert groups[0].directory == "."

    def test_sorted_by_directory(self) -> None:
        files = [
            DiscoveredFile(path="z/a.py", size=10, char_count=10, file_type="py"),
            DiscoveredFile(path="a/b.py", size=10, char_count=10, file_type="py"),
            DiscoveredFile(path="m/c.py", size=10, char_count=10, file_type="py"),
        ]
        groups = group_by_directory(files)
        dirs = [g.directory for g in groups]
        assert dirs == ["a", "m", "z"]

    def test_empty_files_list(self) -> None:
        groups = group_by_directory([])
        assert groups == []

    def test_folder_group_properties(self) -> None:
        files = [
            DiscoveredFile(path="src/a.py", size=100, char_count=80, file_type="py"),
            DiscoveredFile(path="src/b.py", size=200, char_count=150, file_type="py"),
        ]
        groups = group_by_directory(files)
        assert len(groups) == 1
        assert groups[0].total_size == 300
        assert groups[0].total_chars == 230

    def test_nested_directories(self) -> None:
        files = [
            DiscoveredFile(
                path="src/pkg/mod.py", size=10, char_count=10, file_type="py"
            ),
            DiscoveredFile(
                path="src/pkg/sub/deep.py", size=10, char_count=10, file_type="py"
            ),
        ]
        groups = group_by_directory(files)
        dirs = [g.directory for g in groups]
        assert "src/pkg" in dirs
        assert "src/pkg/sub" in dirs


class TestBuildInventory:
    def test_inventory_fields(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        inv = build_inventory("test-source", source_tree, files)

        assert inv.name == "test-source"
        assert inv.path == str(source_tree.resolve())
        assert inv.file_count == len(files)
        assert inv.total_size == sum(f.size for f in files)
        assert inv.total_chars == sum(f.char_count for f in files)
        assert len(inv.directories) > 0
        assert isinstance(inv.file_type_counts, dict)

    def test_file_type_counts(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        inv = build_inventory("test", source_tree, files)

        assert inv.file_type_counts["py"] == 3  # __init__, main, utils
        assert inv.file_type_counts["md"] == 4  # README, guide, api, faq
        assert inv.file_type_counts["yaml"] == 1  # config

    def test_pattern_detection_python(self, tmp_path: Path) -> None:
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "main.py").write_text("x = 1")

        files = discover_files(tmp_path)
        inv = build_inventory("test", tmp_path, files)
        assert "Python package" in inv.patterns

    def test_pattern_detection_markdown_docs(self, source_tree: Path) -> None:
        files = discover_files(source_tree)
        inv = build_inventory("test", source_tree, files)
        assert "Markdown documentation" in inv.patterns

    def test_pattern_detection_container(self, tmp_path: Path) -> None:
        (tmp_path / "Dockerfile").write_text("FROM ubuntu")
        (tmp_path / "app.py").write_text("print('hi')")

        files = discover_files(tmp_path)
        inv = build_inventory("test", tmp_path, files)
        assert "Container project" in inv.patterns

    def test_empty_source(self, tmp_path: Path) -> None:
        inv = build_inventory("empty", tmp_path, [])
        assert inv.file_count == 0
        assert inv.total_size == 0
        assert inv.total_chars == 0
        assert inv.directories == []
        assert inv.file_type_counts == {}

    def test_no_extension_counted(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:")
        files = discover_files(tmp_path)
        inv = build_inventory("test", tmp_path, files)
        assert "(no extension)" in inv.file_type_counts

    def test_directories_list(self, source_tree: Path) -> None:
        """Inventory should include actual directory paths, not just a count."""
        files = discover_files(source_tree)
        inv = build_inventory("test", source_tree, files)
        assert isinstance(inv.directories, list)
        assert "." in inv.directories  # root-level files
        assert "docs" in inv.directories
        assert "src/myapp" in inv.directories
        assert inv.directories == sorted(inv.directories)


class TestDiscoverAndInventory:
    def test_multiple_sources(self, tmp_path: Path) -> None:
        src_a = tmp_path / "repo-a"
        src_a.mkdir()
        (src_a / "file.py").write_text("a = 1")

        src_b = tmp_path / "repo-b"
        src_b.mkdir()
        (src_b / "doc.md").write_text("# Doc")
        (src_b / "notes.md").write_text("notes")

        sources = [("repo-a", src_a), ("repo-b", src_b)]
        all_files, inventories = discover_and_inventory(sources)

        assert len(all_files) == 2
        assert len(inventories) == 2
        assert len(all_files["repo-a"]) == 1
        assert len(all_files["repo-b"]) == 2
        assert inventories[0].name == "repo-a"
        assert inventories[1].name == "repo-b"

    def test_empty_source_list(self) -> None:
        all_files, inventories = discover_and_inventory([])
        assert all_files == {}
        assert inventories == []

    def test_single_source(self, tmp_path: Path) -> None:
        (tmp_path / "data.txt").write_text("hello")
        all_files, inventories = discover_and_inventory([("src", tmp_path)])
        assert len(all_files) == 1
        assert len(inventories) == 1
        assert inventories[0].file_count == 1
