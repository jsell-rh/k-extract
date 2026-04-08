"""Tests for config schema models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from k_extract.config.schema import (
    DataSourceConfig,
    EntityTypeConfig,
    ExtractionConfig,
    OntologyConfig,
    OutputConfig,
    PromptsConfig,
    RelationshipTypeConfig,
)

# --- Helpers ---


def _minimal_entity(label: str = "TestCase", **kwargs: object) -> dict:
    defaults: dict = {
        "label": label,
        "description": "A test case",
        "required_properties": ["name"],
        "optional_properties": [],
    }
    defaults.update(kwargs)
    return defaults


def _minimal_relationship(
    label: str = "TESTS",
    source: str = "TestCase",
    target: str = "Component",
    **kwargs: object,
) -> dict:
    defaults: dict = {
        "label": label,
        "description": "A test exercises a component",
        "source_entity_type": source,
        "target_entity_type": target,
        "required_properties": [],
        "optional_properties": [],
    }
    defaults.update(kwargs)
    return defaults


def _minimal_config(**overrides: object) -> dict:
    defaults: dict = {
        "problem_statement": "Test problem",
        "data_sources": [{"name": "src1", "path": "/path/to/src1"}],
        "ontology": {
            "entity_types": [
                _minimal_entity("TestCase"),
                _minimal_entity("Component"),
            ],
            "relationship_types": [_minimal_relationship()],
        },
        "prompts": {
            "system_prompt": "You are an extractor",
            "job_description_template": "Process {job_id}",
        },
        "output": {"file": "graph.jsonl"},
    }
    defaults.update(overrides)
    return defaults


# --- DataSourceConfig ---


class TestDataSourceConfig:
    def test_valid(self) -> None:
        ds = DataSourceConfig(name="my-source", path="/some/path")
        assert ds.name == "my-source"
        assert ds.path == "/some/path"

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Data source path must not be empty"):
            DataSourceConfig(name="my-source", path="")

    def test_missing_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataSourceConfig(path="/some/path")  # type: ignore[call-arg]

    def test_missing_path_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataSourceConfig(name="my-source")  # type: ignore[call-arg]


# --- EntityTypeConfig ---


class TestEntityTypeConfig:
    def test_valid(self) -> None:
        et = EntityTypeConfig(**_minimal_entity())
        assert et.label == "TestCase"
        assert et.tag_definitions == {}

    def test_with_tag_definitions(self) -> None:
        tags = {"unit": "Unit test", "e2e": "End-to-end"}
        et = EntityTypeConfig(**_minimal_entity(tag_definitions=tags))
        assert et.tag_definitions == tags

    def test_label_not_pascal_case_rejected(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            EntityTypeConfig(**_minimal_entity(label="test_case"))

    def test_label_upper_snake_rejected(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            EntityTypeConfig(**_minimal_entity(label="TEST_CASE"))

    def test_label_lowercase_rejected(self) -> None:
        with pytest.raises(ValidationError, match="PascalCase"):
            EntityTypeConfig(**_minimal_entity(label="testcase"))

    def test_label_single_uppercase_valid(self) -> None:
        et = EntityTypeConfig(**_minimal_entity(label="T"))
        assert et.label == "T"

    def test_label_with_digits_valid(self) -> None:
        et = EntityTypeConfig(**_minimal_entity(label="Test2Case"))
        assert et.label == "Test2Case"


# --- RelationshipTypeConfig ---


class TestRelationshipTypeConfig:
    def test_valid(self) -> None:
        rt = RelationshipTypeConfig(**_minimal_relationship())
        assert rt.label == "TESTS"
        assert rt.source_entity_type == "TestCase"
        assert rt.target_entity_type == "Component"

    def test_label_not_upper_snake_rejected(self) -> None:
        with pytest.raises(ValidationError, match="UPPER_SNAKE_CASE"):
            RelationshipTypeConfig(**_minimal_relationship(label="tests"))

    def test_label_pascal_case_rejected(self) -> None:
        with pytest.raises(ValidationError, match="UPPER_SNAKE_CASE"):
            RelationshipTypeConfig(**_minimal_relationship(label="Tests"))

    def test_label_with_digits_valid(self) -> None:
        rt = RelationshipTypeConfig(**_minimal_relationship(label="HAS_V2"))
        assert rt.label == "HAS_V2"

    def test_label_multi_segment_valid(self) -> None:
        rt = RelationshipTypeConfig(**_minimal_relationship(label="DEPENDS_ON"))
        assert rt.label == "DEPENDS_ON"

    def test_label_single_word_valid(self) -> None:
        rt = RelationshipTypeConfig(**_minimal_relationship(label="TESTS"))
        assert rt.label == "TESTS"


# --- OntologyConfig ---


class TestOntologyConfig:
    def test_valid_cross_references(self) -> None:
        oc = OntologyConfig(
            entity_types=[
                EntityTypeConfig(**_minimal_entity("TestCase")),
                EntityTypeConfig(**_minimal_entity("Component")),
            ],
            relationship_types=[
                RelationshipTypeConfig(**_minimal_relationship()),
            ],
        )
        assert len(oc.entity_types) == 2
        assert len(oc.relationship_types) == 1

    def test_undefined_source_entity_type_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="references undefined source entity type"
        ):
            OntologyConfig(
                entity_types=[EntityTypeConfig(**_minimal_entity("Component"))],
                relationship_types=[
                    RelationshipTypeConfig(
                        **_minimal_relationship(source="TestCase", target="Component")
                    ),
                ],
            )

    def test_undefined_target_entity_type_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="references undefined target entity type"
        ):
            OntologyConfig(
                entity_types=[EntityTypeConfig(**_minimal_entity("TestCase"))],
                relationship_types=[
                    RelationshipTypeConfig(
                        **_minimal_relationship(source="TestCase", target="Component")
                    ),
                ],
            )

    def test_empty_lists_valid(self) -> None:
        oc = OntologyConfig(entity_types=[], relationship_types=[])
        assert oc.entity_types == []
        assert oc.relationship_types == []

    def test_no_relationships_valid(self) -> None:
        oc = OntologyConfig(
            entity_types=[EntityTypeConfig(**_minimal_entity("TestCase"))],
            relationship_types=[],
        )
        assert len(oc.entity_types) == 1


# --- OutputConfig ---


class TestOutputConfig:
    def test_valid_with_defaults(self) -> None:
        oc = OutputConfig(file="graph.jsonl")
        assert oc.file == "graph.jsonl"
        assert oc.database == "extraction.db"

    def test_custom_database(self) -> None:
        oc = OutputConfig(file="graph.jsonl", database="custom.db")
        assert oc.database == "custom.db"

    def test_empty_file_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Output file path must not be empty"):
            OutputConfig(file="")

    def test_missing_file_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OutputConfig()  # type: ignore[call-arg]


# --- PromptsConfig ---


class TestPromptsConfig:
    def test_valid(self) -> None:
        pc = PromptsConfig(
            system_prompt="You are an extractor",
            job_description_template="Process {job_id}",
        )
        assert pc.system_prompt == "You are an extractor"

    def test_missing_system_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PromptsConfig(job_description_template="Process {job_id}")  # type: ignore[call-arg]

    def test_missing_template_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PromptsConfig(system_prompt="You are an extractor")  # type: ignore[call-arg]


# --- ExtractionConfig (full config) ---


class TestExtractionConfig:
    def test_valid_full_config(self) -> None:
        config = ExtractionConfig(**_minimal_config())
        assert config.problem_statement == "Test problem"
        assert len(config.data_sources) == 1
        assert config.output.database == "extraction.db"

    def test_multiline_problem_statement(self) -> None:
        config = ExtractionConfig(
            **_minimal_config(problem_statement="Line 1\nLine 2\nLine 3\n")
        )
        assert "Line 1" in config.problem_statement
        assert "Line 2" in config.problem_statement

    def test_missing_problem_statement_rejected(self) -> None:
        data = _minimal_config()
        del data["problem_statement"]
        with pytest.raises(ValidationError):
            ExtractionConfig(**data)

    def test_missing_data_sources_rejected(self) -> None:
        data = _minimal_config()
        del data["data_sources"]
        with pytest.raises(ValidationError):
            ExtractionConfig(**data)

    def test_missing_ontology_rejected(self) -> None:
        data = _minimal_config()
        del data["ontology"]
        with pytest.raises(ValidationError):
            ExtractionConfig(**data)

    def test_missing_prompts_rejected(self) -> None:
        data = _minimal_config()
        del data["prompts"]
        with pytest.raises(ValidationError):
            ExtractionConfig(**data)

    def test_missing_output_rejected(self) -> None:
        data = _minimal_config()
        del data["output"]
        with pytest.raises(ValidationError):
            ExtractionConfig(**data)

    def test_invalid_entity_label_propagates(self) -> None:
        data = _minimal_config()
        data["ontology"]["entity_types"][0]["label"] = "bad_label"
        with pytest.raises(ValidationError, match="PascalCase"):
            ExtractionConfig(**data)

    def test_invalid_relationship_label_propagates(self) -> None:
        data = _minimal_config()
        data["ontology"]["relationship_types"][0]["label"] = "notUpper"
        with pytest.raises(ValidationError, match="UPPER_SNAKE_CASE"):
            ExtractionConfig(**data)

    def test_cross_reference_error_propagates(self) -> None:
        data = _minimal_config()
        data["ontology"]["relationship_types"][0]["source_entity_type"] = "Nonexistent"
        with pytest.raises(ValidationError, match="undefined source entity type"):
            ExtractionConfig(**data)

    def test_multiple_data_sources(self) -> None:
        data = _minimal_config(
            data_sources=[
                {"name": "src1", "path": "/path/1"},
                {"name": "src2", "path": "/path/2"},
                {"name": "src3", "path": "/path/3"},
            ]
        )
        config = ExtractionConfig(**data)
        assert len(config.data_sources) == 3

    def test_config_from_spec_example(self) -> None:
        """Validate against the full example from the config schema spec."""
        config = ExtractionConfig(
            problem_statement=(
                "I have 3 repos for openshift-hyperfleet, and I don't understand "
                "my testing inventory.\n"
            ),
            data_sources=[
                DataSourceConfig(
                    name="hyperfleet-core", path="/path/to/hyperfleet-core"
                ),
                DataSourceConfig(
                    name="hyperfleet-operator", path="/path/to/hyperfleet-operator"
                ),
                DataSourceConfig(name="hyperfleet-cli", path="/path/to/hyperfleet-cli"),
                DataSourceConfig(name="rosa-tests", path="/path/to/rosa-tests"),
            ],
            ontology=OntologyConfig(
                entity_types=[
                    EntityTypeConfig(
                        label="TestCase",
                        description="Individual test function/method...",
                        required_properties=["name", "framework", "file_path"],
                        optional_properties=[],
                        tag_definitions={},
                    ),
                    EntityTypeConfig(
                        label="Component",
                        description="A software module, package, or subsystem...",
                        required_properties=["name", "kind"],
                        optional_properties=[],
                        tag_definitions={},
                    ),
                    EntityTypeConfig(
                        label="TestSuite",
                        description="A collection of test cases",
                        required_properties=["name"],
                        optional_properties=[],
                        tag_definitions={},
                    ),
                ],
                relationship_types=[
                    RelationshipTypeConfig(
                        label="TESTS",
                        description="A test exercises a component...",
                        source_entity_type="TestCase",
                        target_entity_type="Component",
                        required_properties=[],
                        optional_properties=[],
                    ),
                    RelationshipTypeConfig(
                        label="CONTAINS",
                        description="A suite groups tests...",
                        source_entity_type="TestSuite",
                        target_entity_type="TestCase",
                        required_properties=[],
                        optional_properties=[],
                    ),
                ],
            ),
            prompts=PromptsConfig(
                system_prompt="You are a knowledge extraction agent...",
                job_description_template=(
                    "## Job {job_id}\nProcess the following {file_count} files"
                ),
            ),
            output=OutputConfig(file="graph.jsonl", database="extraction.db"),
        )
        assert config.problem_statement.startswith("I have 3 repos")
        assert len(config.data_sources) == 4
        assert len(config.ontology.entity_types) == 3
        assert len(config.ontology.relationship_types) == 2
