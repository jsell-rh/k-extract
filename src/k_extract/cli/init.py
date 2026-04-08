"""CLI command and guided session orchestration for k-extract init.

Implements the interactive guided session from specs/process/guided-session.md.
Produces an extraction.yaml config file through 4 steps:
1. Problem statement capture
2. Data inventory scan and display
3. AI-driven ontology proposal
3b. Iterative refinement loop
4. Config file output with composed prompts
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import click
import yaml

from k_extract.config.loader import save_config
from k_extract.config.schema import (
    DataSourceConfig,
    ExtractionConfig,
    OntologyConfig,
    OutputConfig,
    PromptsConfig,
)
from k_extract.extraction.prompts import (
    compose_system_prompt,
    generate_extraction_guidance,
    load_job_description_template,
)
from k_extract.pipeline.sources import (
    DataSourceInventory,
    DiscoveredFile,
    build_inventory,
    discover_files,
)

_SAMPLE_MAX_CHARS = 50_000


@click.command()
@click.argument("data_sources", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--problem",
    default=None,
    help="Problem statement (skips interactive prompt for headless use).",
)
@click.option(
    "--output",
    default="extraction.yaml",
    show_default=True,
    help="Config file output path.",
)
def init(data_sources: tuple[str, ...], problem: str | None, output: str) -> None:
    """Guided session to produce an extraction.yaml config file.

    Interactively walks through problem statement, data inventory,
    AI-assisted ontology proposal, and config file output.

    Provide --problem for headless (non-interactive) use.
    """
    asyncio.run(
        run_guided_session(
            data_source_paths=list(data_sources),
            problem_statement=problem,
            output_path=output,
        )
    )


async def run_guided_session(
    *,
    data_source_paths: list[str],
    problem_statement: str | None = None,
    output_path: str = "extraction.yaml",
    llm_call: Callable[[str], Awaitable[str]] | None = None,
    input_func: Callable[[str], str] | None = None,
) -> ExtractionConfig:
    """Run the guided session flow to produce an extraction config.

    Args:
        data_source_paths: Paths to data source directories.
        problem_statement: If provided, skip interactive problem prompt (headless).
        output_path: Path for the output config file.
        llm_call: Optional async callable for LLM interaction (testing).
        input_func: Optional callable replacing interactive prompts (testing).

    Returns:
        The validated ExtractionConfig written to output_path.
    """
    if llm_call is None:
        llm_call = _create_default_llm_caller()

    headless = problem_statement is not None
    _input = input_func or _default_input

    # Step 1: Problem statement
    if problem_statement is None:
        while True:
            problem_statement = _input(
                "What problems are you trying to solve with this knowledge graph?"
            )
            if problem_statement.strip():
                break
            click.echo("Please provide a problem statement.")

    click.echo(f"\nProblem statement: {problem_statement}\n")

    # Step 2: Data inventory
    data_sources_config, all_files, inventories = _scan_data_sources(data_source_paths)
    _display_inventory(inventories)

    # Step 3: AI ontology proposal
    sample_content = _read_sample_files(all_files)
    click.echo("Generating ontology proposal...")
    ontology = await _propose_ontology(
        problem_statement=problem_statement,
        inventories=inventories,
        sample_content=sample_content,
        llm_call=llm_call,
    )

    # Step 3b: Iterative refinement loop (skip in headless mode)
    if not headless:
        ontology = await _refinement_loop(
            ontology=ontology,
            problem_statement=problem_statement,
            llm_call=llm_call,
            input_func=_input,
        )

    # Step 4: Config file output
    click.echo("Composing prompts...")
    config = await _build_config(
        problem_statement=problem_statement,
        data_sources=data_sources_config,
        ontology=ontology,
        llm_call=llm_call,
    )

    save_config(config, output_path)
    click.echo(f"\nConfig written to {output_path}")

    return config


def _default_input(prompt: str) -> str:
    """Default interactive input using click.prompt."""
    return click.prompt(prompt, default="", show_default=False)


def _scan_data_sources(
    data_source_paths: list[str],
) -> tuple[
    list[DataSourceConfig], dict[str, list[DiscoveredFile]], list[DataSourceInventory]
]:
    """Scan data source paths and build configs, file maps, and inventories.

    Returns:
        Tuple of (data source configs, files keyed by resolved path, inventories).
    """
    data_sources_config: list[DataSourceConfig] = []
    all_files: dict[str, list[DiscoveredFile]] = {}
    inventories: list[DataSourceInventory] = []
    used_names: set[str] = set()

    for source_path in data_source_paths:
        resolved = Path(source_path).resolve()
        name = _make_unique_name(Path(source_path).name, used_names)
        used_names.add(name)

        files = discover_files(source_path)
        inventory = build_inventory(name, source_path, files)

        all_files[str(resolved)] = files
        inventories.append(inventory)
        data_sources_config.append(DataSourceConfig(name=name, path=str(resolved)))

    return data_sources_config, all_files, inventories


def _make_unique_name(name: str, existing: set[str]) -> str:
    """Generate a unique data source name, appending a counter if needed."""
    if name not in existing:
        return name
    counter = 2
    while f"{name}-{counter}" in existing:
        counter += 1
    return f"{name}-{counter}"


def _display_inventory(inventories: list[DataSourceInventory]) -> None:
    """Display data inventory to the user."""
    click.echo("=" * 60)
    click.echo("Data Inventory")
    click.echo("=" * 60)

    for inv in inventories:
        click.echo(f"\n  Source: {inv.name}")
        click.echo(f"  Path: {inv.path}")
        click.echo(f"  Files: {inv.file_count}")
        click.echo(f"  Total size: {_format_size(inv.total_size)}")
        click.echo(f"  Total characters: {inv.total_chars:,}")

        if inv.file_type_counts:
            click.echo("  File types:")
            for ft, count in inv.file_type_counts.items():
                click.echo(f"    .{ft}: {count}")

        if inv.directories:
            click.echo(f"  Directories: {len(inv.directories)}")
            for d in inv.directories[:10]:
                click.echo(f"    {d}")
            if len(inv.directories) > 10:
                click.echo(f"    ... and {len(inv.directories) - 10} more")

        if inv.patterns:
            click.echo("  Patterns detected:")
            for p in inv.patterns:
                click.echo(f"    - {p}")

    click.echo()


def _format_size(size_bytes: int) -> str:
    """Format bytes as a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _read_sample_files(
    all_files: dict[str, list[DiscoveredFile]],
    max_chars: int = _SAMPLE_MAX_CHARS,
) -> str:
    """Read a representative sample of files for AI analysis.

    Divides the character budget equally across data sources to ensure
    each source is represented in the sample.

    Args:
        all_files: Dict mapping resolved source path to discovered files.
        max_chars: Maximum total characters to include.

    Returns:
        Formatted string containing sample file contents.
    """
    if not all_files:
        return "(no readable files found)"

    source_count = len(all_files)
    per_source_budget = max_chars // source_count

    samples: list[str] = []

    for root_path, files in all_files.items():
        root = Path(root_path)
        readable = [f for f in files if f.char_count > 0]
        source_chars = 0

        for f in readable:
            if source_chars >= per_source_budget:
                break
            file_path = root / f.path
            try:
                content = file_path.read_text(encoding="utf-8")
                remaining = per_source_budget - source_chars
                if len(content) > remaining:
                    content = content[:remaining]
                samples.append(f"### {f.path}\n\n```\n{content}\n```")
                source_chars += len(content)
            except (UnicodeDecodeError, OSError):
                continue

    return "\n\n".join(samples) if samples else "(no readable files found)"


def _format_inventory_for_prompt(inventories: list[DataSourceInventory]) -> str:
    """Format inventory data for inclusion in an LLM prompt."""
    sections: list[str] = []
    for inv in inventories:
        lines = [
            f"### {inv.name}",
            f"- Path: {inv.path}",
            f"- Files: {inv.file_count}",
            f"- Total size: {_format_size(inv.total_size)}",
            f"- Characters: {inv.total_chars:,}",
        ]
        if inv.file_type_counts:
            type_strs = [f".{t} ({c})" for t, c in inv.file_type_counts.items()]
            lines.append(f"- File types: {', '.join(type_strs)}")
        if inv.directories:
            lines.append(f"- Directories: {len(inv.directories)}")
        if inv.patterns:
            lines.append(f"- Patterns: {', '.join(inv.patterns)}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


async def _propose_ontology(
    *,
    problem_statement: str,
    inventories: list[DataSourceInventory],
    sample_content: str,
    llm_call: Callable[[str], Awaitable[str]],
) -> OntologyConfig:
    """Generate an ontology proposal using AI.

    Args:
        problem_statement: The user's problem statement.
        inventories: Data source inventory reports.
        sample_content: Representative sample of file contents.
        llm_call: Async callable for LLM interaction.

    Returns:
        Proposed OntologyConfig.
    """
    inventory_text = _format_inventory_for_prompt(inventories)

    prompt = (
        "You are designing a knowledge graph ontology. Based on the problem statement, "
        "data inventory, and sample data below, propose entity types and relationship "
        "types.\n\n"
        f"## Problem Statement\n\n{problem_statement}\n\n"
        f"## Data Inventory\n\n{inventory_text}\n\n"
        f"## Sample Data\n\n{sample_content}\n\n"
        "## Instructions\n\n"
        "Propose entity types (PascalCase labels) and relationship types "
        "(UPPER_SNAKE_CASE labels). For each type provide:\n"
        "- A description of what it represents\n"
        "- Required properties (list of property name strings)\n"
        "- Optional properties (list, may be empty)\n"
        "- For entity types: tag_definitions (dict mapping tag name to description, "
        "may be empty)\n"
        "- For relationship types: source_entity_type and target_entity_type\n\n"
        "Respond with a YAML block in this exact format:\n\n"
        "```yaml\n"
        "entity_types:\n"
        "  - label: EntityName\n"
        '    description: "What this entity represents"\n'
        "    required_properties:\n"
        "      - name\n"
        "    optional_properties: []\n"
        "    tag_definitions: {}\n"
        "relationship_types:\n"
        "  - label: RELATIONSHIP_NAME\n"
        '    description: "What this relationship represents"\n'
        "    source_entity_type: SourceEntity\n"
        "    target_entity_type: TargetEntity\n"
        "    required_properties: []\n"
        "    optional_properties: []\n"
        "```\n\n"
        "After the YAML block, provide brief reasoning for each type: "
        "why it supports the stated problem."
    )

    response = await llm_call(prompt)
    ontology, reasoning = _parse_ontology_response(response)
    _display_ontology(ontology)
    if reasoning:
        _display_reasoning(reasoning)
    return ontology


def _display_reasoning(reasoning: str) -> None:
    """Display the AI's reasoning for the ontology proposal."""
    click.echo("Reasoning:")
    click.echo(reasoning)
    click.echo()


def _display_ontology(ontology: OntologyConfig) -> None:
    """Display the current ontology to the user."""
    click.echo("\n" + "=" * 60)
    click.echo("Current Ontology")
    click.echo("=" * 60)

    click.echo("\nEntity Types:")
    for et in ontology.entity_types:
        click.echo(f"\n  {et.label}: {et.description}")
        if et.required_properties:
            click.echo(f"    Required: {', '.join(et.required_properties)}")
        if et.optional_properties:
            click.echo(f"    Optional: {', '.join(et.optional_properties)}")
        if et.tag_definitions:
            tags = ", ".join(
                f"{k} ({v})" for k, v in sorted(et.tag_definitions.items())
            )
            click.echo(f"    Tags: {tags}")

    click.echo("\nRelationship Types:")
    for rt in ontology.relationship_types:
        click.echo(f"\n  {rt.label}: {rt.description}")
        click.echo(f"    {rt.source_entity_type} -> {rt.target_entity_type}")
        if rt.required_properties:
            click.echo(f"    Required: {', '.join(rt.required_properties)}")
        if rt.optional_properties:
            click.echo(f"    Optional: {', '.join(rt.optional_properties)}")

    click.echo()


async def _refinement_loop(
    *,
    ontology: OntologyConfig,
    problem_statement: str,
    llm_call: Callable[[str], Awaitable[str]],
    input_func: Callable[[str], str],
) -> OntologyConfig:
    """Iterative refinement loop for the ontology.

    Displays current ontology, accepts user feedback, and updates
    until the user presses Enter with no input.

    Args:
        ontology: The current ontology proposal.
        problem_statement: The user's problem statement.
        llm_call: Async callable for LLM interaction.
        input_func: Callable for user input.

    Returns:
        The accepted OntologyConfig.
    """
    while True:
        _display_ontology(ontology)
        feedback = input_func("Provide feedback to refine, or press Enter to accept")
        if not feedback.strip():
            break
        click.echo("Updating ontology...")
        ontology = await _refine_ontology(
            ontology=ontology,
            feedback=feedback,
            problem_statement=problem_statement,
            llm_call=llm_call,
        )

    return ontology


async def _refine_ontology(
    *,
    ontology: OntologyConfig,
    feedback: str,
    problem_statement: str,
    llm_call: Callable[[str], Awaitable[str]],
) -> OntologyConfig:
    """Refine the ontology based on user feedback.

    Args:
        ontology: The current ontology.
        feedback: User's refinement feedback.
        problem_statement: The user's problem statement.
        llm_call: Async callable for LLM interaction.

    Returns:
        Updated OntologyConfig.
    """
    current_yaml = yaml.dump(
        ontology.model_dump(),
        default_flow_style=False,
        sort_keys=False,
    )

    prompt = (
        "You are refining a knowledge graph ontology based on user feedback.\n\n"
        f"## Problem Statement\n\n{problem_statement}\n\n"
        f"## Current Ontology\n\n```yaml\n{current_yaml}```\n\n"
        f"## User Feedback\n\n{feedback}\n\n"
        "## Instructions\n\n"
        "Update the ontology according to the user's feedback. "
        "Respond with the complete updated ontology as a YAML block:\n\n"
        "```yaml\n"
        "entity_types:\n"
        "  - label: ...\n"
        "    ...\n"
        "relationship_types:\n"
        "  - label: ...\n"
        "    ...\n"
        "```"
    )

    response = await llm_call(prompt)
    ontology, _ = _parse_ontology_response(response)
    return ontology


def _parse_ontology_response(response: str) -> tuple[OntologyConfig, str]:
    """Parse an LLM response containing a YAML ontology proposal and reasoning.

    Extracts a YAML code block from the response and validates it
    against the OntologyConfig schema. Also extracts reasoning text
    that appears after the YAML block.

    Args:
        response: Raw LLM response text.

    Returns:
        Tuple of (validated OntologyConfig, reasoning text after YAML block).

    Raises:
        click.ClickException: If YAML parsing or validation fails.
    """
    yaml_text = _extract_yaml_block(response)
    reasoning = _extract_reasoning(response)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        msg = f"Failed to parse ontology YAML from AI response: {exc}"
        raise click.ClickException(msg) from exc

    if not isinstance(data, dict):
        msg = "AI response did not contain valid ontology YAML"
        raise click.ClickException(msg)

    try:
        return OntologyConfig.model_validate(data), reasoning
    except Exception as exc:
        msg = f"AI-proposed ontology failed validation: {exc}"
        raise click.ClickException(msg) from exc


def _extract_yaml_block(text: str) -> str:
    """Extract a YAML code block from text.

    Looks for ```yaml ... ``` or ```yml ... ``` markers.
    Falls back to the entire text if no code block is found.

    Args:
        text: Text potentially containing a YAML code block.

    Returns:
        The extracted YAML content.
    """
    match = re.search(r"```(?:yaml|yml)\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1)
    return text


def _extract_reasoning(text: str) -> str:
    """Extract reasoning text that appears after the YAML code block.

    Args:
        text: Full LLM response text.

    Returns:
        The text after the closing ``` of the YAML block, stripped.
        Empty string if no YAML block is found.
    """
    match = re.search(r"```(?:yaml|yml)\s*\n.*?```", text, re.DOTALL)
    if match:
        return text[match.end() :].strip()
    return ""


async def _build_config(
    *,
    problem_statement: str,
    data_sources: list[DataSourceConfig],
    ontology: OntologyConfig,
    llm_call: Callable[[str], Awaitable[str]],
) -> ExtractionConfig:
    """Build the complete extraction config with composed prompts.

    Uses prompt generation from Task 008:
    - LLM-generated extraction guidance from ontology + problem statement
    - Static template + guidance composed into system_prompt
    - Job description template with variable placeholders

    Args:
        problem_statement: The user's problem statement.
        data_sources: Configured data sources.
        ontology: The confirmed ontology.
        llm_call: Async callable for LLM interaction.

    Returns:
        Complete, validated ExtractionConfig.
    """
    extraction_guidance = await generate_extraction_guidance(
        ontology=ontology,
        problem_statement=problem_statement,
        llm_call=llm_call,
    )

    system_prompt = compose_system_prompt(extraction_guidance)
    job_description_template = load_job_description_template()

    return ExtractionConfig(
        problem_statement=problem_statement,
        data_sources=data_sources,
        ontology=ontology,
        prompts=PromptsConfig(
            system_prompt=system_prompt,
            job_description_template=job_description_template,
        ),
        output=OutputConfig(file="graph.jsonl"),
    )


def _create_default_llm_caller() -> Callable[[str], Awaitable[str]]:
    """Create a default LLM caller using the Claude Agent SDK.

    Returns:
        Async callable that sends a prompt to Claude and returns the text response.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        UserMessage,
    )

    async def call(prompt: str) -> str:
        options = ClaudeAgentOptions(
            system_prompt="",
            allowed_tools=[],
            permission_mode="bypassPermissions",
        )
        text_parts: list[str] = []
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_messages():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(message, UserMessage):
                    pass  # No tools configured, no user messages expected
                elif isinstance(message, ResultMessage):
                    break
        return "".join(text_parts)

    return call
