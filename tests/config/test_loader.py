"""Tests for config YAML loader/saver."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from k_extract.config.loader import load_config, save_config
from k_extract.config.schema import (
    DataSourceConfig,
    EntityTypeConfig,
    ExtractionConfig,
    OntologyConfig,
    OutputConfig,
    PromptsConfig,
    RelationshipTypeConfig,
)


def _sample_config() -> ExtractionConfig:
    return ExtractionConfig(
        problem_statement="I need to understand my testing inventory.\n",
        data_sources=[
            DataSourceConfig(name="core", path="/path/to/core"),
            DataSourceConfig(name="cli", path="/path/to/cli"),
        ],
        ontology=OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="TestCase",
                    description="A test case",
                    required_properties=["name"],
                    optional_properties=["framework"],
                    tag_definitions={"unit": "Unit test"},
                ),
                EntityTypeConfig(
                    label="Component",
                    description="A component",
                    required_properties=["name"],
                    optional_properties=[],
                ),
            ],
            relationship_types=[
                RelationshipTypeConfig(
                    label="TESTS",
                    description="A test exercises a component",
                    source_entity_type="TestCase",
                    target_entity_type="Component",
                    required_properties=[],
                    optional_properties=[],
                ),
            ],
        ),
        prompts=PromptsConfig(
            system_prompt="You are an extractor.\nFollow the rules.\n",
            job_description_template="## Job {job_id}\nProcess {file_count} files.\n",
        ),
        output=OutputConfig(file="graph.jsonl"),
    )


class TestLoadConfig:
    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "extraction.yaml"
        save_config(config, yaml_path)

        loaded = load_config(yaml_path)
        assert loaded.problem_statement == config.problem_statement
        assert len(loaded.data_sources) == 2
        assert loaded.data_sources[0].name == "core"
        assert loaded.ontology.entity_types[0].label == "TestCase"
        assert loaded.output.file == "graph.jsonl"
        assert loaded.output.database == "extraction.db"

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text(":\n  :\n    - ][")
        with pytest.raises(yaml.YAMLError):
            load_config(yaml_path)

    def test_load_missing_required_field(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "incomplete.yaml"
        yaml_path.write_text("problem_statement: test\n")
        with pytest.raises(ValidationError):
            load_config(yaml_path)

    def test_load_invalid_entity_label(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "extraction.yaml"
        save_config(config, yaml_path)

        # Manually corrupt the label
        data = yaml.safe_load(yaml_path.read_text())
        data["ontology"]["entity_types"][0]["label"] = "bad_label"
        yaml_path.write_text(yaml.dump(data))

        with pytest.raises(ValidationError, match="PascalCase"):
            load_config(yaml_path)

    def test_load_accepts_string_path(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "extraction.yaml"
        save_config(config, yaml_path)

        loaded = load_config(str(yaml_path))
        assert loaded.problem_statement == config.problem_statement


class TestSaveConfig:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "output.yaml"
        save_config(config, yaml_path)
        assert yaml_path.exists()

    def test_save_produces_valid_yaml(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "output.yaml"
        save_config(config, yaml_path)

        data = yaml.safe_load(yaml_path.read_text())
        assert data["problem_statement"].strip() == config.problem_statement.strip()
        assert len(data["data_sources"]) == 2

    def test_save_accepts_string_path(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "output.yaml"
        save_config(config, str(yaml_path))
        assert yaml_path.exists()

    def test_save_multiline_uses_block_style(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "output.yaml"
        save_config(config, yaml_path)

        raw = yaml_path.read_text()
        # Multiline strings should use literal block style (|)
        assert "|" in raw


class TestRoundTrip:
    def test_load_save_round_trip(self, tmp_path: Path) -> None:
        config = _sample_config()
        path1 = tmp_path / "v1.yaml"
        path2 = tmp_path / "v2.yaml"

        save_config(config, path1)
        loaded = load_config(path1)
        save_config(loaded, path2)

        # Both files should produce equivalent configs
        loaded1 = load_config(path1)
        loaded2 = load_config(path2)
        assert loaded1 == loaded2

    def test_round_trip_preserves_all_fields(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "extraction.yaml"
        save_config(config, yaml_path)
        loaded = load_config(yaml_path)

        assert loaded.problem_statement == config.problem_statement
        assert loaded.data_sources == config.data_sources
        assert loaded.ontology.entity_types == config.ontology.entity_types
        assert loaded.ontology.relationship_types == config.ontology.relationship_types
        assert loaded.prompts == config.prompts
        assert loaded.output == config.output

    def test_round_trip_with_tag_definitions(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "extraction.yaml"
        save_config(config, yaml_path)
        loaded = load_config(yaml_path)

        assert loaded.ontology.entity_types[0].tag_definitions == {"unit": "Unit test"}

    def test_round_trip_with_default_database(self, tmp_path: Path) -> None:
        config = _sample_config()
        yaml_path = tmp_path / "extraction.yaml"
        save_config(config, yaml_path)
        loaded = load_config(yaml_path)

        assert loaded.output.database == "extraction.db"
