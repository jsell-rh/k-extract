"""Worker loop for the extraction pipeline.

Each worker runs in a loop: claim next job, set up workspace, run agent,
record result. On success, emits CREATE operations to the JSONL output.
On failure, marks the job as failed with error details. Workers are
isolated — one failure does not affect other workers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from k_extract.config.schema import ExtractionConfig
from k_extract.domain.ontology import Ontology
from k_extract.extraction.agent import AgentResult, CumulativeUsage, run_agent
from k_extract.extraction.logging import get_logger
from k_extract.extraction.prompts import substitute_job_variables
from k_extract.extraction.store import OntologyStore
from k_extract.extraction.tools import create_tool_server
from k_extract.pipeline.defines import generate_creates
from k_extract.pipeline.jobs import claim_next_job, mark_completed, mark_failed
from k_extract.pipeline.writer import JsonlWriter


@dataclass
class WorkerResult:
    """Aggregated result from a single worker's lifetime."""

    jobs_processed: int = 0
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    cumulative_usage: CumulativeUsage = field(default_factory=CumulativeUsage)
    failed_job_details: list[tuple[str, str]] = field(default_factory=list)


def _build_file_list(files: list[str]) -> str:
    """Build a formatted file list string for prompt substitution."""
    return "\n".join(f"- {f}" for f in files)


async def worker_loop(
    *,
    worker_id: str,
    store: OntologyStore,
    ontology: Ontology,
    session_factory: sessionmaker,
    config: ExtractionConfig,
    writer: JsonlWriter,
    data_source: str,
    source_path: Path,
    conversation_log_dir: Path | None = None,
    max_jobs: int | None = None,
    shared_counter: list[int] | None = None,
) -> WorkerResult:
    """Run the worker loop: claim, process, record.

    Claims jobs from the queue, runs an agent for each, and records
    results. Continues until no pending jobs remain or the max_jobs
    cap is reached.

    Args:
        worker_id: Zero-padded worker identifier (e.g., "01").
        store: Shared ontology store.
        ontology: Domain ontology with type definitions.
        session_factory: Database session factory for job operations.
        config: Extraction config.
        writer: JSONL writer for output operations.
        data_source: Data source name to claim jobs from.
        source_path: Filesystem path to the data source.
        conversation_log_dir: If set, log agent conversations to this dir.
        max_jobs: Cap on total jobs this worker should process.
        shared_counter: If provided, a single-element list [count] shared
            across workers for global max_jobs enforcement. Since asyncio
            is single-threaded, no lock is needed.

    Returns:
        WorkerResult with aggregated stats.
    """
    log = get_logger(worker_id=worker_id, data_source=data_source)
    result = WorkerResult()

    while True:
        # Check max_jobs cap (shared across all workers)
        if max_jobs is not None and (
            (shared_counter is not None and shared_counter[0] >= max_jobs)
            or (shared_counter is None and result.jobs_processed >= max_jobs)
        ):
            break

        # Claim next job
        with session_factory() as session:
            job = claim_next_job(session, worker_id, data_source=data_source)

        if job is None:
            break

        # Increment shared counter immediately after claiming (before await)
        # to prevent other workers from exceeding the cap
        if shared_counter is not None:
            shared_counter[0] += 1

        log.info(
            "extraction.job_claimed",
            job_id=job.job_id,
            file_count=job.file_count,
            total_characters=job.total_characters,
        )

        # Clear staging area for this worker
        store.clear_staging(worker_id)

        # Set up agent for this job
        mcp_server = create_tool_server(worker_id, store, ontology)

        # Build job description via prompt substitution
        file_list = _build_file_list(job.files)
        initial_message = substitute_job_variables(
            config.prompts.job_description_template,
            job_id=job.job_id,
            file_count=job.file_count,
            total_characters=job.total_characters,
            file_list=file_list,
        )

        # Run agent
        agent_result: AgentResult = await run_agent(
            worker_id=worker_id,
            system_prompt=config.prompts.system_prompt,
            initial_message=initial_message,
            mcp_server=mcp_server,
            job_id=job.job_id,
            data_source=data_source,
            cwd=str(source_path),
            conversation_log_dir=conversation_log_dir,
        )

        result.jobs_processed += 1
        result.cumulative_usage.add(agent_result.usage)

        if agent_result.success:
            # Emit CREATE operations for committed entities/relationships
            committed_entities, committed_rels = store.pop_committed(worker_id)
            if committed_entities or committed_rels:
                creates = generate_creates(
                    committed_entities,
                    committed_rels,
                    data_source,
                    ontology,
                )
                await writer.write_operations(creates)

            # Mark job as completed
            with session_factory() as session:
                mark_completed(session, job.job_id)
            result.jobs_succeeded += 1
            log.info("extraction.job_completed", job_id=job.job_id)
        else:
            # Mark job as failed
            error_msg = agent_result.error_message or "Unknown error"
            with session_factory() as session:
                mark_failed(session, job.job_id, error_msg)
            result.jobs_failed += 1
            result.failed_job_details.append((job.job_id, error_msg))
            log.error(
                "extraction.job_failed",
                job_id=job.job_id,
                error=error_msg,
            )

    return result
