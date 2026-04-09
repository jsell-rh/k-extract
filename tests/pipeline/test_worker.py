"""Tests for the worker loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
from k_extract.domain.ontology import (
    EntityTypeDefinition,
    Ontology,
    RelationshipCategory,
    RelationshipDirection,
    RelationshipTypeDefinition,
    Tier,
)
from k_extract.extraction.agent import AgentResult, UsageStats
from k_extract.extraction.store import OntologyStore
from k_extract.pipeline.database import (
    Job,
    JobStatus,
    create_engine_with_wal,
    create_session_factory,
)
from k_extract.pipeline.progress import PipelineProgress, WorkerStatus
from k_extract.pipeline.worker import worker_loop
from k_extract.pipeline.writer import JsonlWriter


def _make_ontology() -> Ontology:
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


def _make_config(tmp_path: Path) -> ExtractionConfig:
    source_dir = tmp_path / "source"
    source_dir.mkdir(exist_ok=True)
    return ExtractionConfig(
        problem_statement="Test",
        data_sources=[
            DataSourceConfig(name="test-source", path=str(source_dir)),
        ],
        ontology=OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Document",
                    description="A doc",
                    required_properties=["title"],
                    optional_properties=[],
                ),
            ],
            relationship_types=[
                RelationshipTypeConfig(
                    label="REFERENCES",
                    description="refs",
                    source_entity_type="Document",
                    target_entity_type="Document",
                    required_properties=[],
                    optional_properties=[],
                ),
            ],
        ),
        prompts=PromptsConfig(
            system_prompt="You are an extractor.",
            job_description_template=(
                "Job {job_id}: {file_count} files "
                "({total_characters} chars)\n{file_list}"
            ),
        ),
        output=OutputConfig(
            file=str(tmp_path / "output.jsonl"),
            database=str(tmp_path / "test.db"),
        ),
    )


def _insert_job(
    session_factory,
    job_id: str,
    data_source: str = "test-source",
    order: int = 0,
    status: str = JobStatus.PENDING,
    files: list[str] | None = None,
) -> Job:
    """Insert a test job into the database."""
    now = datetime.now(UTC)
    job = Job(
        job_id=job_id,
        order=order,
        data_source=data_source,
        files=files or ["doc.md"],
        file_count=len(files or ["doc.md"]),
        total_characters=100,
        status=status,
        created_at=now,
        attempt=0,
    )
    with session_factory() as session:
        session.add(job)
        session.commit()
    return job


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def session_factory(db_path: Path):
    engine = create_engine_with_wal(db_path)
    return create_session_factory(engine)


@pytest.fixture
def ontology() -> Ontology:
    return _make_ontology()


@pytest.fixture
def store(db_path: Path, ontology: Ontology) -> OntologyStore:
    engine = create_engine_with_wal(db_path)
    return OntologyStore(engine, ontology)


@pytest.fixture
def config(tmp_path: Path) -> ExtractionConfig:
    return _make_config(tmp_path)


@pytest.fixture
def writer(tmp_path: Path) -> JsonlWriter:
    return JsonlWriter(tmp_path / "output.jsonl")


class TestWorkerLoop:
    @pytest.mark.asyncio
    async def test_claims_and_completes_job(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker claims a pending job, runs agent, marks completed."""
        _insert_job(session_factory, "job-1")

        mock_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_run,
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
            )

        assert result.jobs_processed == 1
        assert result.jobs_succeeded == 1
        assert result.jobs_failed == 0
        mock_run.assert_called_once()

        # Verify job is marked completed in DB
        with session_factory() as session:
            job = session.get(Job, "job-1")
            assert job is not None
            assert job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_marks_failed_job(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker marks job as failed when agent fails."""
        _insert_job(session_factory, "job-fail")

        mock_result = AgentResult(
            success=False,
            error_message="Validation failed",
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
            )

        assert result.jobs_processed == 1
        assert result.jobs_succeeded == 0
        assert result.jobs_failed == 1
        assert len(result.failed_job_details) == 1
        assert result.failed_job_details[0] == (
            "job-fail",
            "Validation failed",
        )

        # Verify job is marked failed in DB
        with session_factory() as session:
            job = session.get(Job, "job-fail")
            assert job is not None
            assert job.status == JobStatus.FAILED
            assert job.error_message == "Validation failed"

    @pytest.mark.asyncio
    async def test_stops_when_no_pending_jobs(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker stops when no pending jobs are available."""
        # No jobs inserted
        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
            ) as mock_run,
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
            )

        assert result.jobs_processed == 0
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_max_jobs_cap(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker respects max_jobs cap."""
        _insert_job(session_factory, "job-a", order=0)
        _insert_job(session_factory, "job-b", order=1)
        _insert_job(session_factory, "job-c", order=2)

        mock_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
                max_jobs=2,
            )

        assert result.jobs_processed == 2
        assert result.jobs_succeeded == 2

    @pytest.mark.asyncio
    async def test_processes_multiple_jobs(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker processes all pending jobs in sequence."""
        _insert_job(session_factory, "j1", order=0)
        _insert_job(session_factory, "j2", order=1)

        mock_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
            )

        assert result.jobs_processed == 2
        assert result.jobs_succeeded == 2

    @pytest.mark.asyncio
    async def test_failure_isolation(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """One job failure doesn't prevent processing other jobs."""
        _insert_job(session_factory, "good-1", order=0)
        _insert_job(session_factory, "bad-1", order=1)
        _insert_job(session_factory, "good-2", order=2)

        call_count = 0

        async def mock_run_agent(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("job_id") == "bad-1":
                return AgentResult(
                    success=False,
                    error_message="Boom",
                    usage=UsageStats(),
                )
            return AgentResult(
                success=True,
                error_message=None,
                usage=UsageStats(),
            )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                side_effect=mock_run_agent,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
            )

        assert result.jobs_processed == 3
        assert result.jobs_succeeded == 2
        assert result.jobs_failed == 1

    @pytest.mark.asyncio
    async def test_claims_globally_across_data_sources(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker claims jobs from all data sources via global queue."""
        source_a_dir = tmp_path / "source-a"
        source_a_dir.mkdir()
        source_b_dir = tmp_path / "source-b"
        source_b_dir.mkdir()

        _insert_job(
            session_factory,
            "source-a-job",
            data_source="source-a",
            order=0,
        )
        _insert_job(
            session_factory,
            "source-b-job",
            data_source="source-b",
            order=1,
        )

        mock_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_run,
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={
                    "source-a": source_a_dir,
                    "source-b": source_b_dir,
                },
            )

        # Both jobs processed
        assert result.jobs_processed == 2
        assert result.jobs_succeeded == 2

        # Verify cwd was set correctly for each data source
        calls = mock_run.call_args_list
        assert len(calls) == 2
        # First call should use source-a's path
        assert calls[0].kwargs["cwd"] == str(source_a_dir)
        assert calls[0].kwargs["data_source"] == "source-a"
        # Second call should use source-b's path
        assert calls[1].kwargs["cwd"] == str(source_b_dir)
        assert calls[1].kwargs["data_source"] == "source-b"

    @pytest.mark.asyncio
    async def test_emits_create_operations(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker emits CREATEs for committed entities."""
        _insert_job(session_factory, "job-with-commit")

        async def mock_run_agent(**kwargs):
            # Simulate agent staging and committing
            entity = EntityInstance(
                slug="document:test-doc",
                properties={
                    "title": "Test",
                    "source_path": "doc.md",
                },
            )
            store.stage_entity("01", entity)
            store.validate_and_commit("01")
            return AgentResult(
                success=True,
                error_message=None,
                usage=UsageStats(),
            )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                side_effect=mock_run_agent,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
            )

        assert result.jobs_succeeded == 1

        # Verify JSONL output has CREATE operations
        output_content = writer.path.read_text().strip()
        assert output_content  # not empty
        lines = output_content.split("\n")
        creates = [
            json.loads(line) for line in lines if json.loads(line).get("op") == "CREATE"
        ]
        assert len(creates) >= 1
        assert creates[0]["label"] == "Document"
        assert creates[0]["set_properties"]["slug"] == "document:test-doc"

    @pytest.mark.asyncio
    async def test_accumulates_usage(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker accumulates usage stats across jobs."""
        _insert_job(session_factory, "j1", order=0)
        _insert_job(session_factory, "j2", order=1)

        mock_usage = UsageStats(
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
        )
        mock_result = AgentResult(
            success=True,
            error_message=None,
            usage=mock_usage,
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
            )

        assert result.cumulative_usage.input_tokens == 200
        assert result.cumulative_usage.output_tokens == 100
        assert result.cumulative_usage.cost_usd == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_crash_updates_progress_for_inflight_job(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker crash during processing updates progress tracker for in-flight job."""
        _insert_job(session_factory, "crash-job")

        progress = PipelineProgress(worker_count=1)
        progress.register_sources({"test-source": 1})

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                side_effect=RuntimeError("unexpected crash"),
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
                progress=progress,
            )

        # The crash should have been recorded as a failure in progress
        assert progress.failed_jobs == 1
        assert progress.pending_jobs == 0
        # Worker should end in FINISHED state
        assert progress.workers["01"].status == WorkerStatus.FINISHED
        # Worker result reflects 0 processed (crash happened during processing)
        assert result.jobs_processed == 0

    @pytest.mark.asyncio
    async def test_progress_none_safe(
        self,
        tmp_path: Path,
        session_factory,
        store: OntologyStore,
        ontology: Ontology,
        config: ExtractionConfig,
        writer: JsonlWriter,
    ) -> None:
        """Worker loop works correctly when progress=None (default)."""
        _insert_job(session_factory, "job-no-progress")

        mock_result = AgentResult(
            success=True,
            error_message=None,
            usage=UsageStats(),
        )

        with (
            patch(
                "k_extract.pipeline.worker.run_agent",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch(
                "k_extract.pipeline.worker.create_tool_server",
                return_value=None,
            ),
        ):
            result = await worker_loop(
                worker_id="01",
                store=store,
                ontology=ontology,
                session_factory=session_factory,
                config=config,
                writer=writer,
                source_paths={"test-source": tmp_path / "source"},
                progress=None,
            )

        assert result.jobs_processed == 1
        assert result.jobs_succeeded == 1
