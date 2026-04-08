"""Prompt composition and substitution logic for k-extract.

Implements the prompt generation system from specs/agent/prompt-generation.md.
Prompts are composed during `k-extract init` and stored in the config file.
At run time, only per-job variable substitution occurs (no LLM calls).
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from k_extract.config.schema import OntologyConfig


def load_template(name: str) -> str:
    """Load a template file from the templates directory.

    Args:
        name: Template filename (e.g., 'system_prompt.txt').

    Returns:
        The template content as a string.
    """
    templates = importlib.resources.files("k_extract.extraction") / "templates"
    return (templates / name).read_text(encoding="utf-8")


def build_guidance_prompt(ontology: OntologyConfig, problem_statement: str) -> str:
    """Build the LLM prompt for generating extraction guidance.

    Formats the ontology and problem statement into a prompt that asks an LLM
    to produce natural language extraction instructions per entity type and
    relationship type, with problem-statement-driven priorities.

    Args:
        ontology: The confirmed ontology from the config.
        problem_statement: The user's problem statement.

    Returns:
        A prompt string to send to the LLM.
    """
    entity_sections: list[str] = []
    for et in ontology.entity_types:
        props = (
            ", ".join(et.required_properties) if et.required_properties else "(none)"
        )
        opt_props = (
            ", ".join(et.optional_properties) if et.optional_properties else "(none)"
        )
        tags = (
            ", ".join(sorted(et.tag_definitions.keys()))
            if et.tag_definitions
            else "(none)"
        )
        entity_sections.append(
            f"- {et.label}: {et.description}\n"
            f"  Required properties: {props}\n"
            f"  Optional properties: {opt_props}\n"
            f"  Tags: {tags}"
        )

    rel_sections: list[str] = []
    for rt in ontology.relationship_types:
        props = (
            ", ".join(rt.required_properties) if rt.required_properties else "(none)"
        )
        rel_sections.append(
            f"- {rt.label}: {rt.description}\n"
            f"  Source: {rt.source_entity_type} -> Target: {rt.target_entity_type}\n"
            f"  Required properties: {props}"
        )

    return (
        "You are helping configure a knowledge extraction system. "
        "Given the following ontology and problem statement, generate natural language "
        "extraction guidance for an AI agent that will process source files.\n\n"
        "The guidance should describe:\n"
        "- For each entity type: what it represents, when to create one, "
        "what properties to capture, and any special considerations\n"
        "- For each relationship type: what connections to look for, "
        "directionality, and what properties to set\n"
        "- Problem-statement-driven priorities: what to focus on, "
        "what level of granularity, what to ignore\n\n"
        f"## Problem Statement\n\n{problem_statement}\n\n"
        "## Entity Types\n\n" + "\n".join(entity_sections) + "\n\n"
        "## Relationship Types\n\n" + "\n".join(rel_sections) + "\n\n"
        "Generate the extraction guidance as markdown sections. "
        "Use '## Entity Types' and '## Relationship Types' as top-level headers, "
        "with a subsection for each type. End with a '## Priorities' section "
        "driven by the problem statement."
    )


async def generate_extraction_guidance(
    ontology: OntologyConfig,
    problem_statement: str,
    llm_call: Callable[[str], Awaitable[str]],
) -> str:
    """Generate extraction guidance by calling an LLM.

    Builds a prompt from the ontology and problem statement, then sends it
    to an LLM via the provided callable.

    Args:
        ontology: The confirmed ontology from the config.
        problem_statement: The user's problem statement.
        llm_call: Async callable that sends a prompt string to an LLM
            and returns the generated text.

    Returns:
        Natural language extraction guidance for the agent.
    """
    prompt = build_guidance_prompt(ontology, problem_statement)
    return await llm_call(prompt)


def compose_system_prompt(extraction_guidance: str) -> str:
    """Compose the complete system prompt from template + extraction guidance.

    Loads the static system prompt template and inserts the extraction guidance
    at the designated placeholder.

    Args:
        extraction_guidance: LLM-generated extraction guidance text.

    Returns:
        The complete system prompt ready to store in the config.
    """
    template = load_template("system_prompt.txt")
    return template.format(extraction_guidance=extraction_guidance)


def load_job_description_template() -> str:
    """Load the job description template with variable placeholders.

    Returns the template with {job_id}, {file_count}, {total_characters},
    and {file_list} placeholders for per-job substitution at runtime.

    Returns:
        The job description template string.
    """
    return load_template("job_description.txt")


def substitute_job_variables(
    template: str,
    *,
    job_id: str,
    file_count: int,
    total_characters: int,
    file_list: str,
) -> str:
    """Substitute per-job variables into the job description template.

    Pure string substitution — no LLM call. Called at runtime for each job.

    Args:
        template: The job description template with placeholders.
        job_id: Job identifier.
        file_count: Number of files in the job.
        total_characters: Total character count across files.
        file_list: Formatted list of file paths to process.

    Returns:
        The job description with all variables substituted.
    """
    return template.format(
        job_id=job_id,
        file_count=file_count,
        total_characters=total_characters,
        file_list=file_list,
    )
