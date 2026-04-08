"""Tests for k-extract init guided session."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from k_extract.cli import main
from k_extract.cli.init import (
    _extract_reasoning,
    _extract_yaml_block,
    _format_inventory_for_prompt,
    _format_size,
    _make_unique_name,
    _parse_ontology_response,
    _read_sample_files,
    run_guided_session,
)
from k_extract.config.loader import load_config
from k_extract.pipeline.sources import discover_files

# --- Test fixtures and helpers ---

_SAMPLE_ONTOLOGY_YAML = """\
entity_types:
  - label: Component
    description: "A software component"
    required_properties:
      - name
    optional_properties: []
    tag_definitions: {}
relationship_types:
  - label: DEPENDS_ON
    description: "Component dependency"
    source_entity_type: Component
    target_entity_type: Component
    required_properties: []
    optional_properties: []
"""

_SAMPLE_LLM_RESPONSE = f"""\
Here is the proposed ontology:

```yaml
{_SAMPLE_ONTOLOGY_YAML.strip()}
```

## Reasoning

- **Component**: Represents software components in the codebase.
- **DEPENDS_ON**: Captures dependency relationships between components.
"""

_REFINED_ONTOLOGY_YAML = """\
entity_types:
  - label: Component
    description: "A software component"
    required_properties:
      - name
    optional_properties: []
    tag_definitions: {}
  - label: TestCase
    description: "A test case"
    required_properties:
      - name
    optional_properties: []
    tag_definitions: {}
relationship_types:
  - label: DEPENDS_ON
    description: "Component dependency"
    source_entity_type: Component
    target_entity_type: Component
    required_properties: []
    optional_properties: []
"""


def _make_mock_llm(response: str = _SAMPLE_LLM_RESPONSE):
    """Create a mock LLM callable that returns a fixed response."""

    async def mock_call(prompt: str) -> str:
        return response

    return mock_call


@pytest.fixture()
def data_source(tmp_path: Path) -> Path:
    """Create a temporary data source with sample files."""
    src = tmp_path / "sample-project"
    src.mkdir()
    (src / "README.md").write_text("# Sample Project\nThis is a sample.")
    (src / "main.py").write_text("def main():\n    print('hello')\n")
    (src / "utils.py").write_text("def helper():\n    return 42\n")
    subdir = src / "lib"
    subdir.mkdir()
    (subdir / "__init__.py").write_text("")
    (subdir / "core.py").write_text("class Core:\n    pass\n")
    return src


# --- Unit tests for helper functions ---


class TestExtractYamlBlock:
    def test_extracts_yaml_block(self) -> None:
        text = "Some text\n```yaml\nkey: value\n```\nMore text"
        assert _extract_yaml_block(text) == "key: value\n"

    def test_extracts_yml_block(self) -> None:
        text = "```yml\nfoo: bar\n```"
        assert _extract_yaml_block(text) == "foo: bar\n"

    def test_returns_full_text_when_no_block(self) -> None:
        text = "key: value"
        assert _extract_yaml_block(text) == "key: value"

    def test_extracts_first_block(self) -> None:
        text = "```yaml\nfirst: 1\n```\n```yaml\nsecond: 2\n```"
        assert _extract_yaml_block(text) == "first: 1\n"


class TestParseOntologyResponse:
    def test_parses_valid_response(self) -> None:
        ontology, reasoning = _parse_ontology_response(_SAMPLE_LLM_RESPONSE)
        assert len(ontology.entity_types) == 1
        assert ontology.entity_types[0].label == "Component"
        assert ontology.entity_types[0].description == "A software component"
        assert ontology.entity_types[0].required_properties == ["name"]
        assert len(ontology.relationship_types) == 1
        assert ontology.relationship_types[0].label == "DEPENDS_ON"
        assert ontology.relationship_types[0].source_entity_type == "Component"
        assert ontology.relationship_types[0].target_entity_type == "Component"

    def test_extracts_reasoning(self) -> None:
        _, reasoning = _parse_ontology_response(_SAMPLE_LLM_RESPONSE)
        assert "Component" in reasoning
        assert "DEPENDS_ON" in reasoning

    def test_empty_reasoning_without_yaml_block(self) -> None:
        _, reasoning = _parse_ontology_response(_SAMPLE_ONTOLOGY_YAML)
        assert reasoning == ""

    def test_raises_on_invalid_yaml(self) -> None:
        with pytest.raises(click.ClickException):
            _parse_ontology_response("```yaml\n{invalid: [yaml\n```")

    def test_raises_on_non_dict(self) -> None:
        with pytest.raises(click.ClickException):
            _parse_ontology_response("```yaml\n- just a list\n```")

    def test_raises_on_invalid_schema(self) -> None:
        with pytest.raises(click.ClickException):
            _parse_ontology_response("```yaml\nentity_types: not_a_list\n```")

    def test_parses_raw_yaml_without_fences(self) -> None:
        ontology, _ = _parse_ontology_response(_SAMPLE_ONTOLOGY_YAML)
        assert len(ontology.entity_types) == 1
        assert ontology.entity_types[0].label == "Component"


class TestExtractReasoning:
    def test_extracts_text_after_yaml(self) -> None:
        text = "Intro\n```yaml\nkey: value\n```\nThis is reasoning."
        assert _extract_reasoning(text) == "This is reasoning."

    def test_empty_when_no_yaml_block(self) -> None:
        assert _extract_reasoning("just some text") == ""

    def test_empty_when_nothing_after_block(self) -> None:
        assert _extract_reasoning("```yaml\nkey: value\n```") == ""

    def test_multiline_reasoning(self) -> None:
        text = "```yaml\nk: v\n```\nLine 1\nLine 2\nLine 3"
        result = _extract_reasoning(text)
        assert "Line 1" in result
        assert "Line 3" in result


class TestFormatSize:
    def test_bytes(self) -> None:
        assert _format_size(500) == "500 B"

    def test_kilobytes(self) -> None:
        assert _format_size(2048) == "2.0 KB"

    def test_megabytes(self) -> None:
        assert _format_size(1_500_000) == "1.4 MB"

    def test_zero(self) -> None:
        assert _format_size(0) == "0 B"

    def test_boundary_kb(self) -> None:
        assert _format_size(1024) == "1.0 KB"


class TestMakeUniqueName:
    def test_unique_name(self) -> None:
        assert _make_unique_name("foo", set()) == "foo"

    def test_duplicate_name(self) -> None:
        assert _make_unique_name("foo", {"foo"}) == "foo-2"

    def test_multiple_duplicates(self) -> None:
        assert _make_unique_name("foo", {"foo", "foo-2"}) == "foo-3"

    def test_non_conflicting(self) -> None:
        assert _make_unique_name("bar", {"foo", "baz"}) == "bar"


class TestReadSampleFiles:
    def test_reads_files(self, data_source: Path) -> None:
        files = discover_files(data_source)
        all_files = {str(data_source.resolve()): files}
        result = _read_sample_files(all_files)
        assert "README.md" in result
        assert "Sample Project" in result

    def test_respects_max_chars(self, data_source: Path) -> None:
        files = discover_files(data_source)
        all_files = {str(data_source.resolve()): files}
        result = _read_sample_files(all_files, max_chars=20)
        # Should contain some content but be truncated
        assert len(result) > 0

    def test_empty_files(self) -> None:
        result = _read_sample_files({})
        assert result == "(no readable files found)"

    def test_fair_allocation_across_sources(self, tmp_path: Path) -> None:
        """Each data source gets an equal share of the character budget."""
        src1 = tmp_path / "source1"
        src1.mkdir()
        # Write a large file that would consume the entire budget alone
        (src1 / "big.txt").write_text("A" * 1000)

        src2 = tmp_path / "source2"
        src2.mkdir()
        (src2 / "small.txt").write_text("B" * 100)

        files1 = discover_files(src1)
        files2 = discover_files(src2)
        all_files = {
            str(src1.resolve()): files1,
            str(src2.resolve()): files2,
        }

        result = _read_sample_files(all_files, max_chars=200)
        # Both sources must be represented
        assert "big.txt" in result
        assert "small.txt" in result
        # Source 1 should be limited to ~100 chars (200 / 2 sources)
        assert "A" * 101 not in result


class TestFormatInventoryForPrompt:
    def test_formats_inventory(self, data_source: Path) -> None:
        from k_extract.pipeline.sources import build_inventory

        files = discover_files(data_source)
        inventory = build_inventory("test-source", str(data_source), files)
        result = _format_inventory_for_prompt([inventory])
        assert "test-source" in result
        assert "Files:" in result
        assert "Characters:" in result


# --- Guided session integration tests ---


class TestGuidedSessionHeadless:
    def test_produces_valid_config(self, data_source: Path, tmp_path: Path) -> None:
        """Headless mode with --problem produces a valid config."""
        output_path = tmp_path / "extraction.yaml"

        config = asyncio.run(
            run_guided_session(
                data_source_paths=[str(data_source)],
                problem_statement="I need to understand my codebase",
                output_path=str(output_path),
                llm_call=_make_mock_llm(),
            )
        )

        assert config.problem_statement == "I need to understand my codebase"
        assert len(config.data_sources) == 1
        assert config.data_sources[0].name == "sample-project"
        assert len(config.ontology.entity_types) == 1
        assert config.ontology.entity_types[0].label == "Component"
        assert config.prompts.system_prompt
        assert config.prompts.job_description_template
        assert config.output.file == "graph.jsonl"

        # Verify saved file passes validation from Task 003
        loaded = load_config(output_path)
        assert loaded.problem_statement == config.problem_statement

    def test_skips_refinement(self, data_source: Path, tmp_path: Path) -> None:
        """Headless mode does not prompt for refinement feedback."""
        output_path = tmp_path / "extraction.yaml"

        def fail_input(prompt: str) -> str:
            raise AssertionError("Should not be called in headless mode")

        config = asyncio.run(
            run_guided_session(
                data_source_paths=[str(data_source)],
                problem_statement="Test problem",
                output_path=str(output_path),
                llm_call=_make_mock_llm(),
                input_func=fail_input,
            )
        )

        assert config.problem_statement == "Test problem"

    def test_multiple_data_sources(self, tmp_path: Path) -> None:
        """Headless mode handles multiple data source paths."""
        src1 = tmp_path / "project-a"
        src1.mkdir()
        (src1 / "a.py").write_text("a = 1")

        src2 = tmp_path / "project-b"
        src2.mkdir()
        (src2 / "b.py").write_text("b = 2")

        output_path = tmp_path / "extraction.yaml"

        config = asyncio.run(
            run_guided_session(
                data_source_paths=[str(src1), str(src2)],
                problem_statement="Analyze both projects",
                output_path=str(output_path),
                llm_call=_make_mock_llm(),
            )
        )

        assert len(config.data_sources) == 2
        names = {ds.name for ds in config.data_sources}
        assert "project-a" in names
        assert "project-b" in names

    def test_duplicate_source_names(self, tmp_path: Path) -> None:
        """Duplicate directory names get unique suffixes."""
        dir_a = tmp_path / "a" / "src"
        dir_a.mkdir(parents=True)
        (dir_a / "f.py").write_text("x = 1")

        dir_b = tmp_path / "b" / "src"
        dir_b.mkdir(parents=True)
        (dir_b / "g.py").write_text("y = 2")

        output_path = tmp_path / "extraction.yaml"

        config = asyncio.run(
            run_guided_session(
                data_source_paths=[str(dir_a), str(dir_b)],
                problem_statement="Test duplicates",
                output_path=str(output_path),
                llm_call=_make_mock_llm(),
            )
        )

        names = [ds.name for ds in config.data_sources]
        assert names[0] == "src"
        assert names[1] == "src-2"

    def test_config_passes_schema_validation(
        self, data_source: Path, tmp_path: Path
    ) -> None:
        """Output config file passes full ExtractionConfig validation."""
        output_path = tmp_path / "extraction.yaml"

        asyncio.run(
            run_guided_session(
                data_source_paths=[str(data_source)],
                problem_statement="Test validation",
                output_path=str(output_path),
                llm_call=_make_mock_llm(),
            )
        )

        # load_config runs full Pydantic validation
        config = load_config(output_path)
        assert config.problem_statement == "Test validation"
        assert len(config.ontology.entity_types) >= 1
        assert len(config.ontology.relationship_types) >= 1
        assert "{job_id}" in config.prompts.job_description_template
        assert "{file_list}" in config.prompts.job_description_template


class TestGuidedSessionInteractive:
    def test_immediate_accept(self, data_source: Path, tmp_path: Path) -> None:
        """Interactive mode where user accepts first proposal."""
        output_path = tmp_path / "extraction.yaml"
        inputs = iter(["My problem statement", ""])

        config = asyncio.run(
            run_guided_session(
                data_source_paths=[str(data_source)],
                output_path=str(output_path),
                llm_call=_make_mock_llm(),
                input_func=lambda _: next(inputs),
            )
        )

        assert config.problem_statement == "My problem statement"
        assert len(config.ontology.entity_types) == 1

    def test_one_refinement_round(self, data_source: Path, tmp_path: Path) -> None:
        """Interactive mode with one round of refinement."""
        output_path = tmp_path / "extraction.yaml"
        refined_response = f"```yaml\n{_REFINED_ONTOLOGY_YAML.strip()}\n```"

        call_count = 0

        async def mock_llm(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _SAMPLE_LLM_RESPONSE  # initial proposal
            if call_count == 2:
                return refined_response  # refinement
            return _SAMPLE_LLM_RESPONSE  # extraction guidance

        inputs = iter(["My problem", "Add TestCase entity type", ""])

        config = asyncio.run(
            run_guided_session(
                data_source_paths=[str(data_source)],
                output_path=str(output_path),
                llm_call=mock_llm,
                input_func=lambda _: next(inputs),
            )
        )

        assert len(config.ontology.entity_types) == 2
        labels = {et.label for et in config.ontology.entity_types}
        assert "Component" in labels
        assert "TestCase" in labels

    def test_multiple_refinement_rounds(
        self, data_source: Path, tmp_path: Path
    ) -> None:
        """Interactive mode with multiple rounds of refinement."""
        output_path = tmp_path / "extraction.yaml"
        refined_response = f"```yaml\n{_REFINED_ONTOLOGY_YAML.strip()}\n```"

        call_count = 0

        async def mock_llm(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _SAMPLE_LLM_RESPONSE  # proposal + first refinement
            if call_count == 3:
                return refined_response  # second refinement
            return _SAMPLE_LLM_RESPONSE  # extraction guidance

        # problem, feedback1, feedback2, accept
        inputs = iter(["My problem", "Change something", "Add TestCase", ""])

        config = asyncio.run(
            run_guided_session(
                data_source_paths=[str(data_source)],
                output_path=str(output_path),
                llm_call=mock_llm,
                input_func=lambda _: next(inputs),
            )
        )

        # After two refinement rounds, should have the refined ontology
        assert len(config.ontology.entity_types) == 2


# --- CLI command tests ---


class TestInitCLI:
    def test_init_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--problem" in result.output
        assert "--output" in result.output
        assert "DATA_SOURCES" in result.output

    def test_init_no_args(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init"])
        assert result.exit_code != 0

    def test_init_nonexistent_path(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", "/nonexistent/path"])
        assert result.exit_code != 0

    def test_init_headless_via_cli(
        self, data_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full CLI headless flow with mocked LLM."""
        monkeypatch.setattr(
            "k_extract.cli.init._create_default_llm_caller",
            lambda: _make_mock_llm(),
        )
        output_path = tmp_path / "output.yaml"
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                str(data_source),
                "--problem",
                "Test problem via CLI",
                "--output",
                str(output_path),
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Config written to" in result.output
        assert output_path.exists()

        config = load_config(output_path)
        assert config.problem_statement == "Test problem via CLI"

    def test_reasoning_displayed(
        self, data_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reasoning from the AI ontology proposal is displayed to the user."""
        monkeypatch.setattr(
            "k_extract.cli.init._create_default_llm_caller",
            lambda: _make_mock_llm(),
        )
        output_path = tmp_path / "output.yaml"
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                str(data_source),
                "--problem",
                "Test problem",
                "--output",
                str(output_path),
            ],
        )

        assert result.exit_code == 0, result.output
        # Reasoning from _SAMPLE_LLM_RESPONSE should appear in output
        assert "Reasoning" in result.output
        assert "Component" in result.output
        assert "DEPENDS_ON" in result.output

    def test_init_shows_in_help(self) -> None:
        """The init command is listed in the main help."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output
