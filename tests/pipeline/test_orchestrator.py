"""Tests for the pipeline orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from k_extract.config.schema import (
    DataSourceConfig,
    EntityTypeConfig,
    ExtractionConfig,
    OntologyConfig,
    OutputConfig,
    PromptsConfig,
    RelationshipTypeConfig,
)
from k_extract.domain.entities import EntityInstance
from k_extract.domain.mutations import (
    DefineType,
    OpType,
)
from k_extract.domain.ontology import (
    Ontology,
    RelationshipCategory,
    RelationshipDirection,
    RelationshipTypeDefinition,
    Tier,
)
from k_extract.domain.relationships import RelationshipInstance
from k_extract.extraction.agent import AgentResult, UsageStats
from k_extract.pipeline.defines import generate_creates
from k_extract.pipeline.orchestrator import (
    build_ontology_from_config,
    run_pipeline,
)


def _make_config(
    tmp_path: Path,
    data_source_name: str = "test-source",
) -> tuple[Path, Path]:
    """Create test data source dir and config file.

    Returns (config_path, source_dir).
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "doc.md").write_text("# Hello\nWorld")

    ontology_config = OntologyConfig(
        entity_types=[
            EntityTypeConfig(
                label="Document",
                description="A document",
                required_properties=["title"],
                optional_properties=["summary"],
            ),
        ],
        relationship_types=[
            RelationshipTypeConfig(
                label="REFERENCES",
                description="References another document",
                source_entity_type="Document",
                target_entity_type="Document",
                required_properties=[],
                optional_properties=[],
            ),
        ],
    )

    config = ExtractionConfig(
        problem_statement="Test extraction",
        data_sources=[
            DataSourceConfig(
                name=data_source_name,
                path=str(source_dir),
            ),
        ],
        ontology=ontology_config,
        prompts=PromptsConfig(
            system_prompt="You are an extractor.",
            job_description_template=(
                "Job {job_id}: process {file_count} files "
                "({total_characters} chars)\n{file_list}"
            ),
        ),
        output=OutputConfig(
            file=str(tmp_path / "output.jsonl"),
            database=str(tmp_path / "test.db"),
        ),
    )

    from k_extract.config.loader import save_config

    config_path = tmp_path / "extraction.yaml"
    save_config(config, config_path)

    return config_path, source_dir


class TestBuildOntologyFromConfig:
    def test_entity_types_mapped(self) -> None:
        config = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Product",
                    description="A product",
                    required_properties=["name"],
                    optional_properties=["desc"],
                    tag_definitions={"core": "Core product"},
                ),
            ],
            relationship_types=[],
        )
        ontology = build_ontology_from_config(config)
        assert "Product" in ontology.entity_types
        et = ontology.entity_types["Product"]
        assert et.type == "Product"
        assert et.description == "A product"
        assert et.tier == Tier.FILE_BASED
        assert et.required_properties == ["name"]
        assert et.optional_properties == ["desc"]
        assert et.tag_definitions == {"core": "Core product"}

    def test_relationship_types_mapped(self) -> None:
        config = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Product",
                    description="A product",
                    required_properties=[],
                    optional_properties=[],
                ),
                EntityTypeConfig(
                    label="Category",
                    description="A category",
                    required_properties=[],
                    optional_properties=[],
                ),
            ],
            relationship_types=[
                RelationshipTypeConfig(
                    label="BELONGS_TO",
                    description="Belongs to category",
                    source_entity_type="Product",
                    target_entity_type="Category",
                    required_properties=["since"],
                    optional_properties=["note"],
                ),
            ],
        )
        ontology = build_ontology_from_config(config)
        ck = "Product|BELONGS_TO|Category"
        assert ck in ontology.relationship_types
        rt = ontology.relationship_types[ck]
        assert rt.source_entity_type == "Product"
        assert rt.target_entity_type == "Category"
        assert rt.forward_relationship.type == "BELONGS_TO"
        assert rt.category == RelationshipCategory.AGENT_MANAGED
        assert rt.required_parameters == ["since"]

    def test_empty_ontology(self) -> None:
        config = OntologyConfig(entity_types=[], relationship_types=[])
        ontology = build_ontology_from_config(config)
        assert ontology.entity_types == {}
        assert ontology.relationship_types == {}


class TestGenerateCreates:
    def _make_ontology(self) -> Ontology:
        from k_extract.domain.ontology import EntityTypeDefinition

        return Ontology(
            entity_types={
                "Document": EntityTypeDefinition(
                    type="Document",
                    description="A doc",
                    tier=Tier.FILE_BASED,
                    required_properties=["title"],
                    optional_properties=[],
                    property_definitions={},
                ),
            },
            relationship_types={
                "Document|REFERENCES|Document": RelationshipTypeDefinition(
                    source_entity_type="Document",
                    target_entity_type="Document",
                    forward_relationship=RelationshipDirection(
                        type="REFERENCES",
                        description="refs",
                    ),
                    category=RelationshipCategory.AGENT_MANAGED,
                    required_parameters=[],
                    optional_parameters=[],
                ),
            },
        )

    def test_entity_creates(self) -> None:
        ontology = self._make_ontology()
        entities = [
            EntityInstance(
                slug="document:my-doc",
                properties={
                    "title": "My Doc",
                    "source_path": "doc.md",
                },
            ),
        ]
        creates = generate_creates(entities, [], "test-source", ontology)
        assert len(creates) == 1
        c = creates[0]
        assert c.op == OpType.CREATE
        assert c.type == DefineType.NODE
        assert c.label == "Document"
        assert c.set_properties["slug"] == "document:my-doc"
        assert c.set_properties["title"] == "My Doc"
        assert c.set_properties["data_source_id"] == "test-source"
        assert c.set_properties["source_path"] == "doc.md"
        assert c.start_id is None
        assert c.end_id is None

    def test_relationship_creates(self) -> None:
        ontology = self._make_ontology()
        rels = [
            RelationshipInstance(
                source_entity_type="Document",
                source_slug="document:a",
                target_entity_type="Document",
                target_slug="document:b",
                relationship_type="REFERENCES",
                properties={"source_path": "a.md"},
            ),
        ]
        creates = generate_creates([], rels, "src", ontology)
        assert len(creates) == 1
        c = creates[0]
        assert c.op == OpType.CREATE
        assert c.type == DefineType.EDGE
        assert c.label == "REFERENCES"
        assert c.start_id is not None
        assert c.end_id is not None
        assert c.set_properties["data_source_id"] == "src"

    def test_default_system_properties(self) -> None:
        """Entities without source_path get it from file_path."""
        ontology = self._make_ontology()
        entities = [
            EntityInstance(
                slug="document:x",
                properties={
                    "title": "X",
                    "file_path": "x.md",
                },
            ),
        ]
        creates = generate_creates(entities, [], "ds", ontology)
        assert creates[0].set_properties["source_path"] == "x.md"
        assert creates[0].set_properties["data_source_id"] == "ds"

    def test_preserves_existing_data_source_id(self) -> None:
        """If entity already has data_source_id, don't override."""
        ontology = self._make_ontology()
        entities = [
            EntityInstance(
                slug="document:z",
                properties={
                    "title": "Z",
                    "data_source_id": "original",
                    "source_path": "z.md",
                },
            ),
        ]
        creates = generate_creates(entities, [], "new-source", ontology)
        assert creates[0].set_properties["data_source_id"] == "original"


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_fresh_run_emits_defines_and_processes_jobs(
        self, tmp_path: Path
    ) -> None:
        """Pipeline fresh run: emits DEFINEs, generates jobs,
        launches workers."""
        config_path, source_dir = _make_config(tmp_path)

        mock_agent_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await run_pipeline(
                config_path=config_path,
                workers=1,
                force=False,
                db_path=str(tmp_path / "test.db"),
            )

        assert result.total_jobs >= 1
        assert result.completed_jobs >= 1
        assert result.failed_jobs == 0

        # Verify JSONL output has DEFINEs
        output_path = Path(result.output_file)
        assert output_path.exists()
        lines = output_path.read_text().strip().split("\n")
        # At least DEFINE for Document and REFERENCES
        defines = [
            json.loads(line) for line in lines if json.loads(line).get("op") == "DEFINE"
        ]
        assert len(defines) >= 2

    @pytest.mark.asyncio
    async def test_resume_skips_completed_source(self, tmp_path: Path) -> None:
        """Resume run skips data sources with all jobs completed."""
        config_path, source_dir = _make_config(tmp_path)

        mock_agent_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        # First run
        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result1 = await run_pipeline(
                config_path=config_path,
                workers=1,
                db_path=str(tmp_path / "test.db"),
            )

        assert result1.completed_jobs >= 1

        # Second run (resume) — should skip all completed jobs
        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ) as mock_run,
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result2 = await run_pipeline(
                config_path=config_path,
                workers=1,
                db_path=str(tmp_path / "test.db"),
            )

        # Agent should not be called on resume (all jobs done)
        mock_run.assert_not_called()
        assert result2.completed_jobs >= 1

    @pytest.mark.asyncio
    async def test_force_flag_starts_fresh(self, tmp_path: Path) -> None:
        """--force discards previous state and starts fresh."""
        config_path, source_dir = _make_config(tmp_path)

        mock_agent_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        # First run
        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            await run_pipeline(
                config_path=config_path,
                workers=1,
                db_path=str(tmp_path / "test.db"),
            )

        # Second run with --force — agent IS called again
        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ) as mock_run,
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await run_pipeline(
                config_path=config_path,
                workers=1,
                force=True,
                db_path=str(tmp_path / "test.db"),
            )

        assert mock_run.call_count >= 1
        assert result.completed_jobs >= 1

    @pytest.mark.asyncio
    async def test_max_jobs_cap(self, tmp_path: Path) -> None:
        """--max-jobs limits total jobs processed."""
        config_path, source_dir = _make_config(tmp_path)
        # Create multiple files so there are more jobs
        for i in range(10):
            (source_dir / f"file_{i}.md").write_text(
                f"# File {i}\n" + "content " * 1000
            )

        mock_agent_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await run_pipeline(
                config_path=config_path,
                workers=2,
                max_jobs=2,
                db_path=str(tmp_path / "test.db"),
            )

        # Should have processed at most 2 jobs
        assert result.completed_jobs <= 2

    @pytest.mark.asyncio
    async def test_failed_jobs_reported(self, tmp_path: Path) -> None:
        """Failed jobs are tracked in PipelineResult."""
        config_path, source_dir = _make_config(tmp_path)

        mock_agent_result = AgentResult(
            success=False,
            error_message="Agent error",
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await run_pipeline(
                config_path=config_path,
                workers=1,
                db_path=str(tmp_path / "test.db"),
            )

        assert result.failed_jobs >= 1
        assert len(result.failed_job_details) >= 1
        assert result.failed_job_details[0][1] == "Agent error"

    @pytest.mark.asyncio
    async def test_hard_stop_on_changed_environment(self, tmp_path: Path) -> None:
        """Changed environment triggers hard stop."""
        config_path, source_dir = _make_config(tmp_path)

        mock_agent_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        # First run
        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            await run_pipeline(
                config_path=config_path,
                workers=1,
                db_path=str(tmp_path / "test.db"),
            )

        # Modify config to change fingerprint
        config_text = config_path.read_text()
        config_text = config_text.replace(
            "Test extraction",
            "Modified problem statement",
        )
        config_path.write_text(config_text)

        # Second run — should hard stop
        with pytest.raises(SystemExit, match="changed"):
            await run_pipeline(
                config_path=config_path,
                workers=1,
                db_path=str(tmp_path / "test.db"),
            )
