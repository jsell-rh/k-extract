"""End-to-end integration test for the full k-extract pipeline.

Exercises run_pipeline() against the real Claude API with no mocking.
Validates that all components (config loading, fingerprinting, job generation,
agent instantiation, tool execution, ontology store, JSONL output) work
together correctly against the live system.

To run:
    K_EXTRACT_E2E=1 uv run pytest -m e2e -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from k_extract.config.loader import save_config
from k_extract.config.schema import (
    DataSourceConfig,
    EntityTypeConfig,
    ExtractionConfig,
    OntologyConfig,
    OutputConfig,
    PromptsConfig,
    RelationshipTypeConfig,
)
from k_extract.domain.mutations import DefineType, OpType
from k_extract.extraction.prompts import (
    compose_system_prompt,
    load_job_description_template,
)
from k_extract.pipeline.database import JobStatus, create_engine_with_wal
from k_extract.pipeline.orchestrator import run_pipeline

pytestmark = pytest.mark.e2e

# Skip unless K_EXTRACT_E2E=1 is set in the environment
if not os.environ.get("K_EXTRACT_E2E"):
    pytestmark = [
        pytestmark,
        pytest.mark.skip(reason="K_EXTRACT_E2E not set; skipping e2e tests"),
    ]


def _create_test_data(tmp_path: Path) -> Path:
    """Create a small test data source with 2-3 short text files.

    Describes a tiny software domain: a few components and their relationships.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    (source_dir / "api-gateway.md").write_text(
        "# API Gateway\n\n"
        "The API Gateway is the main entry point for all client requests. "
        "It handles authentication, rate limiting, and request routing. "
        "The gateway forwards validated requests to the backend services.\n"
    )

    (source_dir / "user-service.md").write_text(
        "# User Service\n\n"
        "The User Service manages user accounts, profiles, and authentication tokens. "
        "It exposes REST endpoints for user CRUD operations. "
        "The API Gateway depends on the User Service for authentication.\n"
    )

    (source_dir / "database.md").write_text(
        "# Database\n\n"
        "The PostgreSQL database stores all persistent data for the system. "
        "The User Service connects to the database "
        "to store and retrieve user records.\n"
    )

    return source_dir


def _build_config(tmp_path: Path, source_dir: Path) -> Path:
    """Build a valid extraction.yaml in tmp_path with all required fields.

    Uses real default prompts from the template loading mechanism.
    """
    ontology_config = OntologyConfig(
        entity_types=[
            EntityTypeConfig(
                label="Component",
                description="A software component or service in the system",
                required_properties=["name"],
                optional_properties=["description"],
            ),
            EntityTypeConfig(
                label="Technology",
                description="A technology, framework, or tool used by a component",
                required_properties=["name"],
                optional_properties=[],
            ),
        ],
        relationship_types=[
            RelationshipTypeConfig(
                label="DEPENDS_ON",
                description="One component depends on another component",
                source_entity_type="Component",
                target_entity_type="Component",
                required_properties=[],
                optional_properties=["description"],
            ),
        ],
    )

    # Use the real system prompt template with simple extraction guidance
    extraction_guidance = (
        "## Entity Types\n\n"
        "### Component\n"
        "Create a Component entity for each distinct software service, "
        "application, or infrastructure component mentioned in the source files. "
        "Set the 'name' property to the component's name.\n\n"
        "### Technology\n"
        "Create a Technology entity for each database engine, framework, "
        "or protocol mentioned. Set the 'name' property.\n\n"
        "## Relationship Types\n\n"
        "### DEPENDS_ON\n"
        "Create a DEPENDS_ON relationship when one component relies on, "
        "calls, or connects to another component.\n\n"
        "## Priorities\n"
        "Focus on identifying the main components and their direct dependencies."
    )
    system_prompt = compose_system_prompt(extraction_guidance)
    job_description_template = load_job_description_template()

    config = ExtractionConfig(
        problem_statement=(
            "Map the architecture of a small software system to understand "
            "component dependencies and technology usage."
        ),
        data_sources=[
            DataSourceConfig(name="test-source", path=str(source_dir)),
        ],
        ontology=ontology_config,
        prompts=PromptsConfig(
            system_prompt=system_prompt,
            job_description_template=job_description_template,
        ),
        output=OutputConfig(
            file=str(tmp_path / "output.jsonl"),
            database=str(tmp_path / "test.db"),
        ),
    )

    config_path = tmp_path / "extraction.yaml"
    save_config(config, config_path)
    return config_path


@pytest.mark.asyncio
async def test_full_pipeline_e2e(tmp_path: Path) -> None:
    """Run the full extraction pipeline against the real Claude API.

    This test calls run_pipeline() with no mocked components, verifying that:
    - The pipeline completes with at least one job
    - No jobs fail
    - Real API costs are incurred (total_cost > 0)
    - JSONL output contains DEFINE and CREATE operations with valid structure
    - Database contains completed job records

    Requires:
        - ANTHROPIC_API_KEY set in the environment
        - K_EXTRACT_E2E=1 to enable the test

    Run with:
        K_EXTRACT_E2E=1 uv run pytest -m e2e -v
    """
    source_dir = _create_test_data(tmp_path)
    config_path = _build_config(tmp_path, source_dir)

    result = await run_pipeline(
        config_path,
        workers=1,
        max_jobs=1,
        force=True,
    )

    # --- Pipeline completion assertions ---
    assert result.completed_jobs >= 1, (
        f"Expected at least 1 completed job, got {result.completed_jobs}"
    )
    assert result.failed_jobs == 0, (
        f"Expected 0 failed jobs, got {result.failed_jobs}: {result.failed_job_details}"
    )
    assert result.total_cost > 0, "Expected total_cost > 0 (proves real API was called)"

    # --- JSONL output assertions ---
    output_path = Path(result.output_file)
    assert output_path.exists(), f"Output file not found: {output_path}"

    lines = output_path.read_text().strip().splitlines()
    operations = [json.loads(line) for line in lines]

    # Collect DEFINE and CREATE operations
    defines = [op for op in operations if op["op"] == OpType.DEFINE]
    creates = [op for op in operations if op["op"] == OpType.CREATE]

    # Must have DEFINE operations
    assert len(defines) > 0, "Expected DEFINE operations in output"

    # Every DEFINE must have correct fields
    for define_op in defines:
        assert "op" in define_op, "DEFINE missing 'op' field"
        assert "type" in define_op, "DEFINE missing 'type' field"
        assert "label" in define_op, "DEFINE missing 'label' field"
        assert "description" in define_op, "DEFINE missing 'description' field"
        assert "required_properties" in define_op, (
            "DEFINE missing 'required_properties' field"
        )
        assert define_op["type"] in (
            DefineType.NODE.value,
            DefineType.EDGE.value,
        )

    # Must have at least one CREATE operation
    assert len(creates) >= 1, "Expected at least one CREATE operation in output"

    # At least one CREATE node operation with valid structure
    node_creates = [op for op in creates if op["type"] == DefineType.NODE.value]
    assert len(node_creates) >= 1, "Expected at least one CREATE node operation"

    for node_op in node_creates:
        # Validate structure
        assert "op" in node_op
        assert "type" in node_op
        assert "id" in node_op
        assert "label" in node_op
        assert "set_properties" in node_op

        # Validate required system properties
        props = node_op["set_properties"]
        assert "slug" in props, f"CREATE node missing 'slug': {node_op}"
        assert "data_source_id" in props, (
            f"CREATE node missing 'data_source_id' in set_properties: {node_op}"
        )
        assert "source_path" in props, (
            f"CREATE node missing 'source_path' in set_properties: {node_op}"
        )

    # --- Database assertions ---
    db_path = str(tmp_path / "test.db")
    engine = create_engine_with_wal(db_path)
    from sqlalchemy import text as sa_text

    with engine.connect() as conn:
        completed_count = conn.execute(
            sa_text("SELECT COUNT(*) FROM jobs WHERE status = :status"),
            {"status": JobStatus.COMPLETED.value},
        ).scalar()
        assert completed_count is not None
        assert completed_count >= 1, (
            f"Expected at least 1 completed job in database, got {completed_count}"
        )
