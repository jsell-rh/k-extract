"""CLI command for `k-extract run`.

Executes the extraction pipeline: loads config, processes data sources,
runs agent workers, and produces JSONL output. Uses Rich console for
spinner during setup and live dashboard during extraction.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from k_extract.cli.display import get_console
from k_extract.extraction.logging import configure_logging


@click.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to extraction.yaml config file.",
)
@click.option(
    "--workers",
    default=3,
    type=int,
    show_default=True,
    help="Number of concurrent worker instances.",
)
@click.option(
    "--max-jobs",
    default=None,
    type=int,
    help="Cap on total jobs to process.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Discard previous state and start fresh.",
)
@click.option(
    "--log-conversations",
    is_flag=True,
    default=False,
    help="Enable agent conversation logging to JSONL.",
)
@click.option(
    "--db",
    "db_path",
    default=None,
    type=str,
    help="Override database path from config.",
)
def run(
    config_path: Path,
    workers: int,
    max_jobs: int | None,
    force: bool,
    log_conversations: bool,
    db_path: str | None,
) -> None:
    """Execute the extraction pipeline."""
    from k_extract.config.settings import get_settings

    settings = get_settings()
    configure_logging(json_output=settings.log_format == "json")

    console = get_console()

    from k_extract.pipeline.orchestrator import run_pipeline

    try:
        result = asyncio.run(
            run_pipeline(
                config_path=config_path,
                workers=workers,
                max_jobs=max_jobs,
                force=force,
                log_conversations=log_conversations,
                db_path=db_path,
                console=console,
            )
        )
    except SystemExit as e:
        raise click.ClickException(str(e)) from None

    # Print completion summary with Rich formatting
    console.print()
    if result.failed_jobs == 0:
        console.print(
            f"[green]Extraction complete.[/green] "
            f"{result.completed_jobs}/{result.total_jobs} jobs completed."
        )
    else:
        console.print(
            f"[yellow]Extraction complete.[/yellow] "
            f"{result.completed_jobs}/{result.total_jobs} "
            f"jobs completed, {result.failed_jobs} failed."
        )

    console.print(f"Output: {result.output_file} ({result.output_lines} lines)")
    console.print(f"Total cost: ${result.total_cost:.2f}")

    if result.failed_job_details:
        console.print()
        console.print("[bold]Failed jobs:[/bold]")
        for job_id, error in result.failed_job_details:
            console.print(f"  {job_id}: {error}")
        console.print()
        console.print("Re-run to retry failed jobs.")
