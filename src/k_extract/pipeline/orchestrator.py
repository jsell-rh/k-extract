"""Pipeline orchestrator for `k-extract run`.

Coordinates the end-to-end extraction flow: config loading, fingerprinting,
resume logic, job generation, DEFINE emission, worker launch, and summary
reporting. Data sources are processed sequentially to ensure cross-source
entity visibility.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from sqlalchemy import text as sa_text

from k_extract.config.loader import load_config
from k_extract.config.schema import OntologyConfig
from k_extract.config.settings import get_settings
from k_extract.domain.ontology import (
    EntityTypeDefinition,
    Ontology,
    RelationshipCategory,
    RelationshipDirection,
    RelationshipTypeDefinition,
    Tier,
)
from k_extract.extraction.agent import discover_model_capabilities
from k_extract.extraction.logging import get_logger
from k_extract.extraction.store import OntologyStore
from k_extract.pipeline.database import (
    JobStatus,
    create_engine_with_wal,
    create_session_factory,
)
from k_extract.pipeline.defines import generate_defines
from k_extract.pipeline.fingerprint import (
    ResumeAction,
    compute_fingerprint,
    evaluate_resume,
    hash_files_parallel,
    store_fingerprint,
)
from k_extract.pipeline.jobs import (
    CHARS_PER_TOKEN,
    FileInfo,
    compute_available_tokens,
    create_jobs,
    reset_all_in_progress,
    reset_failed_jobs,
)
from k_extract.pipeline.progress import PipelineProgress, render_dashboard
from k_extract.pipeline.sources import discover_files
from k_extract.pipeline.worker import WorkerResult, worker_loop
from k_extract.pipeline.writer import JsonlWriter

SAFETY_MARGIN = 5_000
DEFAULT_WORKERS = 3


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    failed_job_details: list[tuple[str, str]] = field(default_factory=list)
    total_cost: float = 0.0
    output_file: str = ""
    output_lines: int = 0


def build_ontology_from_config(ontology_config: OntologyConfig) -> Ontology:
    """Build a domain Ontology from the config's OntologyConfig.

    Maps config entity/relationship types to domain type definitions
    with sensible defaults for fields not present in the config (tier,
    category, property_definitions).
    """
    entity_types: dict[str, EntityTypeDefinition] = {}
    for et in ontology_config.entity_types:
        etd = EntityTypeDefinition(
            type=et.label,
            description=et.description,
            tier=Tier.FILE_BASED,
            required_properties=et.required_properties,
            optional_properties=et.optional_properties,
            property_definitions={},
            tag_definitions=et.tag_definitions,
        )
        entity_types[et.label] = etd

    relationship_types: dict[str, RelationshipTypeDefinition] = {}
    for rt in ontology_config.relationship_types:
        composite_key = f"{rt.source_entity_type}|{rt.label}|{rt.target_entity_type}"
        rtd = RelationshipTypeDefinition(
            source_entity_type=rt.source_entity_type,
            target_entity_type=rt.target_entity_type,
            forward_relationship=RelationshipDirection(
                type=rt.label,
                description=rt.description,
            ),
            category=RelationshipCategory.AGENT_MANAGED,
            required_parameters=rt.required_properties,
            optional_parameters=rt.optional_properties,
        )
        relationship_types[composite_key] = rtd

    return Ontology(
        entity_types=entity_types,
        relationship_types=relationship_types,
    )


def _count_output_lines(path: Path) -> int:
    """Count lines in a file, returning 0 if it doesn't exist."""
    if not path.exists():
        return 0
    with path.open() as f:
        return sum(1 for _ in f)


async def run_pipeline(
    config_path: Path,
    *,
    workers: int = DEFAULT_WORKERS,
    max_jobs: int | None = None,
    force: bool = False,
    log_conversations: bool = False,
    db_path: str | None = None,
    console: Console | None = None,
) -> PipelineResult:
    """Execute the extraction pipeline.

    Loads config, computes fingerprint, handles resume logic, generates
    jobs, emits DEFINEs, launches workers per data source, and reports
    results.

    Args:
        config_path: Path to extraction.yaml.
        workers: Number of concurrent worker instances.
        max_jobs: Cap on total jobs to process (None = no cap).
        force: If True, discard previous state and start fresh.
        log_conversations: If True, log agent conversations to JSONL.
        db_path: Override database path from config.
        console: Rich Console for live dashboard output. If None, no
            dashboard is displayed.

    Returns:
        PipelineResult with completion stats.
    """
    log = get_logger()
    result = PipelineResult()
    settings = get_settings()

    # Setup spinner — updates text as each sub-step progresses
    setup_live: Live | None = None
    setup_spinner: Spinner | None = None

    def _setup_status(msg: str) -> None:
        if setup_spinner is not None and setup_live is not None:
            setup_spinner.update(text=msg)
            setup_live.update(setup_spinner)

    if console is not None:
        setup_spinner = Spinner("dots", text="Loading config...")
        setup_live = Live(
            setup_spinner,
            console=console,
            transient=True,
        )
        setup_live.start()

    try:
        # 1. Load and validate config
        config = load_config(config_path)
        log.info("pipeline.config_loaded", config_path=str(config_path))

        # 2. Determine database and output paths
        effective_db_path = db_path or config.output.database
        output_path = Path(config.output.file)
        result.output_file = str(output_path)

        # 3. Create database engine and session factory
        _setup_status("Initializing database...")
        engine = create_engine_with_wal(effective_db_path)
        session_factory = create_session_factory(engine)

        # 4. Discover model capabilities (context window, max output tokens)
        _setup_status("Discovering model capabilities...")
        model_caps = await discover_model_capabilities(model=settings.model_id)
        context_window = model_caps.context_window
        output_reservation = model_caps.max_output_tokens
        log.info(
            "pipeline.model_capabilities",
            context_window=context_window,
            output_reservation=output_reservation,
        )

        # 5. Build domain ontology from config
        ontology = build_ontology_from_config(config.ontology)

        # 6. Compute environment fingerprint
        all_source_files: list[str] = []
        _prev_files = [0]  # mutable container for closure capture
        num_sources = len(config.data_sources)
        for i, ds in enumerate(config.data_sources, 1):
            ds_path = Path(ds.path)
            if ds_path.is_dir():
                base = _prev_files[0]

                def _scan_progress(
                    found: int,
                    *,
                    _ds_name: str = ds.name,
                    _ds_idx: int = i,
                    _base: int = base,
                ) -> None:
                    if found % 50 == 0:
                        _setup_status(
                            f"Scanning {_ds_name}"
                            f" ({_ds_idx}/{num_sources},"
                            f" {_base + found} files found)..."
                        )

                def _metadata_progress(
                    done: int,
                    total: int,
                    *,
                    _ds_name: str = ds.name,
                    _ds_idx: int = i,
                    _base: int = base,
                ) -> None:
                    if done % 50 == 0 or done == total:
                        _setup_status(
                            f"Reading file metadata ({_ds_name},"
                            f" {_base + done}/{_base + total})..."
                        )

                _setup_status(f"Scanning {ds.name} ({i}/{num_sources})...")
                files = discover_files(
                    ds_path,
                    on_progress=_metadata_progress,
                    on_scan_progress=_scan_progress,
                )
                all_source_files.extend(str(ds_path / f.path) for f in files)
                _prev_files[0] += len(files)

        source_file_paths: list[str | Path] = list(all_source_files)

        def _hash_progress(done: int, total: int) -> None:
            _setup_status(f"Fingerprinting source files ({done}/{total})...")

        _setup_status("Fingerprinting source files...")
        file_hashes = hash_files_parallel(source_file_paths, on_progress=_hash_progress)

        prompt_templates = (
            config.prompts.system_prompt + config.prompts.job_description_template
        )
        config_contents = config_path.read_text(encoding="utf-8")
        config_hash = hashlib.sha256(config_contents.encode("utf-8")).hexdigest()

        current_fingerprint = compute_fingerprint(
            config_contents=config_contents,
            prompt_templates=prompt_templates,
            model_id=settings.model_id,
            file_hashes=file_hashes,
        )

        # 7. Evaluate resume decision
        _setup_status("Evaluating resume state...")
        with session_factory() as session:
            decision = evaluate_resume(session, current_fingerprint, force=force)

        log.info(
            "pipeline.resume_decision",
            action=decision.action.value,
            message=decision.message,
        )

        if decision.action == ResumeAction.HARD_STOP:
            raise SystemExit(decision.message)

        is_fresh = decision.action == ResumeAction.FRESH_START

        # 8. Create ontology store (before fresh-start handling so tables exist)
        ontology_engine = create_engine_with_wal(effective_db_path)
        store = OntologyStore(ontology_engine, ontology)

        # 9. Handle fresh start vs resume
        if is_fresh:
            _setup_status("Starting fresh run...")
        else:
            _setup_status("Preparing to resume...")
        with session_factory() as session:
            if is_fresh:
                # Delete all existing jobs
                session.execute(sa_text("DELETE FROM jobs"))
                # Clear ontology store tables (shared same database)
                session.execute(sa_text("DELETE FROM entity_instances"))
                session.execute(sa_text("DELETE FROM relationship_instances"))
                session.execute(sa_text("DELETE FROM staged_entities"))
                session.execute(sa_text("DELETE FROM staged_relationships"))
                session.commit()
                # Store new fingerprint
                store_fingerprint(
                    session, current_fingerprint, config_hash, settings.model_id
                )
            else:
                # Resume: unconditionally reset all in_progress jobs (startup reset)
                stale_count = reset_all_in_progress(session)
                if stale_count > 0:
                    log.info("pipeline.stale_jobs_reset", count=stale_count)
                # Reset failed jobs to pending for retry
                failed_count = reset_failed_jobs(session)
                if failed_count > 0:
                    log.info("pipeline.failed_jobs_reset", count=failed_count)

        # 10. Emit DEFINE operations (only on fresh start)
        if is_fresh and output_path.exists():
            output_path.unlink()
        writer = JsonlWriter(output_path)
        if is_fresh:
            _setup_status("Emitting type definitions...")
            defines = generate_defines(config.ontology)
            await writer.write_operations(defines)
            log.info("pipeline.defines_emitted", count=len(defines))

        # 11. Set up conversation logging directory
        conversation_log_dir: Path | None = None
        if log_conversations:
            conversation_log_dir = Path("logs") / "conversations"
            conversation_log_dir.mkdir(parents=True, exist_ok=True)
    finally:
        if setup_live is not None:
            setup_live.stop()

    if console is not None:
        console.print("[green]✓[/green] Pipeline setup complete")

    # 12. Process data sources in configured order
    jobs_processed_total = 0
    cumulative_cost = 0.0
    all_failed_details: list[tuple[str, str]] = []
    total_completed = 0
    total_failed = 0
    total_jobs = 0

    # Estimate prompt overhead from actual prompt content (no magic numbers)
    prompt_overhead = (
        len(config.prompts.system_prompt) + len(config.prompts.job_description_template)
    ) // CHARS_PER_TOKEN

    available_tokens = compute_available_tokens(
        context_window, prompt_overhead, output_reservation, SAFETY_MARGIN
    )

    # Create progress tracker once at pipeline start (persists across data sources)
    progress: PipelineProgress | None = None
    if console is not None:
        progress = PipelineProgress(workers)

    for ds in config.data_sources:
        ds_path = Path(ds.path)
        log.info("pipeline.processing_source", data_source=ds.name)

        # Show spinner during job generation (part of setup phase)
        job_gen_live: Live | None = None
        if console is not None:
            job_gen_live = Live(
                Spinner("dots", text=f"Generating jobs for {ds.name}..."),
                console=console,
                transient=True,
            )
            job_gen_live.start()

        try:
            # Generate jobs for this source if fresh start or no jobs exist
            with session_factory() as session:
                existing_count = session.execute(
                    sa_text("SELECT COUNT(*) FROM jobs WHERE data_source = :ds"),
                    {"ds": ds.name},
                ).scalar()

            if existing_count == 0:
                files = discover_files(ds_path)
                file_infos = [
                    FileInfo(path=f.path, char_count=f.char_count) for f in files
                ]

                start_order = 0
                with session_factory() as session:
                    max_order_result = session.execute(
                        sa_text('SELECT COALESCE(MAX("order"), -1) FROM jobs')
                    ).scalar()
                    # COALESCE guarantees non-null; cast for type checker
                    max_order: int = int(max_order_result)  # type: ignore[arg-type]
                    start_order = max_order + 1

                jobs = create_jobs(file_infos, ds.name, available_tokens, start_order)
                with session_factory() as session:
                    for job in jobs:
                        session.add(job)
                    session.commit()
                log.info(
                    "pipeline.jobs_generated",
                    data_source=ds.name,
                    count=len(jobs),
                )
        finally:
            if job_gen_live is not None:
                job_gen_live.stop()

        # Count total and pending jobs for this source
        with session_factory() as session:
            source_total = (
                session.execute(
                    sa_text("SELECT COUNT(*) FROM jobs WHERE data_source = :ds"),
                    {"ds": ds.name},
                ).scalar()
                or 0
            )
            source_pending = (
                session.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM jobs "
                        "WHERE data_source = :ds AND status = :status"
                    ),
                    {"ds": ds.name, "status": JobStatus.PENDING.value},
                ).scalar()
                or 0
            )

        total_jobs += source_total

        # Count already-completed and already-failed jobs from prior runs
        with session_factory() as session:
            already_completed = (
                session.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM jobs "
                        "WHERE data_source = :ds AND status = :status"
                    ),
                    {"ds": ds.name, "status": JobStatus.COMPLETED.value},
                ).scalar()
                or 0
            )
            already_failed = (
                session.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM jobs "
                        "WHERE data_source = :ds AND status = :status"
                    ),
                    {"ds": ds.name, "status": JobStatus.FAILED.value},
                ).scalar()
                or 0
            )
        total_completed += already_completed
        total_failed += already_failed

        if source_pending == 0:
            log.info("pipeline.source_complete", data_source=ds.name)
            continue

        # Compute remaining max_jobs budget for this source
        source_max: int | None = None
        if max_jobs is not None:
            remaining = max_jobs - jobs_processed_total
            if remaining <= 0:
                log.info("pipeline.max_jobs_reached", max_jobs=max_jobs)
                break
            source_max = remaining

        # Launch workers for this source
        # Shared counter for global max_jobs enforcement across workers
        shared_counter: list[int] | None = None
        if source_max is not None:
            shared_counter = [0]

        worker_count = min(workers, source_pending)

        # Update progress tracker for this data source and start live dashboard
        live: Live | None = None
        if console is not None and progress is not None:
            progress.set_data_source(ds.name, source_total, source_pending)
            live = Live(
                render_dashboard(progress),
                console=console,
                refresh_per_second=2,
                transient=True,
            )
            live.start()

        try:
            worker_tasks = []
            for i in range(worker_count):
                worker_id = f"{i + 1:02d}"
                worker_tasks.append(
                    worker_loop(
                        worker_id=worker_id,
                        store=store,
                        ontology=ontology,
                        session_factory=session_factory,
                        config=config,
                        writer=writer,
                        data_source=ds.name,
                        source_path=ds_path,
                        conversation_log_dir=conversation_log_dir,
                        max_jobs=source_max,
                        shared_counter=shared_counter,
                        model_id=settings.model_id,
                        progress=progress,
                    )
                )

            if live is not None:
                assert progress is not None

                async def _refresh(lv: Live, pg: PipelineProgress) -> None:
                    while True:
                        await asyncio.sleep(0.5)
                        lv.update(render_dashboard(pg))

                refresh_task = asyncio.create_task(_refresh(live, progress))
                try:
                    gather_results = await asyncio.gather(
                        *worker_tasks, return_exceptions=True
                    )
                finally:
                    refresh_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await refresh_task
                # Final update before stopping
                live.update(render_dashboard(progress))  # type: ignore[arg-type]
                # Print final state as static output for scrollback
                console.print(render_dashboard(progress))  # type: ignore[arg-type]
            else:
                gather_results = await asyncio.gather(
                    *worker_tasks, return_exceptions=True
                )
        finally:
            if live is not None:
                live.stop()

        # Aggregate worker results, handling any unhandled exceptions
        worker_results: list[WorkerResult] = []
        for gr in gather_results:
            if isinstance(gr, BaseException):
                log.error(
                    "pipeline.worker_crashed",
                    error=str(gr),
                    data_source=ds.name,
                )
                continue
            worker_results.append(gr)

        for wr in worker_results:
            jobs_processed_total += wr.jobs_processed
            total_completed += wr.jobs_succeeded
            total_failed += wr.jobs_failed
            cumulative_cost += wr.cumulative_usage.cost_usd
            all_failed_details.extend(wr.failed_job_details)

        log.info(
            "pipeline.source_done",
            data_source=ds.name,
            completed=sum(wr.jobs_succeeded for wr in worker_results),
            failed=sum(wr.jobs_failed for wr in worker_results),
        )

    # 13. Build final result
    result.total_jobs = total_jobs
    result.completed_jobs = total_completed
    result.failed_jobs = total_failed
    result.failed_job_details = all_failed_details
    result.total_cost = cumulative_cost
    result.output_lines = _count_output_lines(output_path)

    return result
