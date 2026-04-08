"""Tests for prompt composition and substitution logic."""

from __future__ import annotations

import pytest

from k_extract.config.schema import (
    EntityTypeConfig,
    OntologyConfig,
    RelationshipTypeConfig,
)
from k_extract.extraction.prompts import (
    build_guidance_prompt,
    compose_system_prompt,
    generate_extraction_guidance,
    load_job_description_template,
    load_template,
    substitute_job_variables,
)


@pytest.fixture
def sample_ontology() -> OntologyConfig:
    """A minimal ontology for testing."""
    return OntologyConfig(
        entity_types=[
            EntityTypeConfig(
                label="TestCase",
                description="Individual test function/method",
                required_properties=["name", "framework"],
                optional_properties=["timeout"],
                tag_definitions={
                    "unit": "Unit test",
                    "integration": "Integration test",
                },
            ),
            EntityTypeConfig(
                label="Component",
                description="A software module or subsystem",
                required_properties=["name"],
                optional_properties=[],
                tag_definitions={},
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
    )


@pytest.fixture
def problem_statement() -> str:
    return "I need to understand my testing inventory and find coverage gaps."


# --- load_template ---


class TestLoadTemplate:
    def test_load_system_prompt_template(self) -> None:
        template = load_template("system_prompt.txt")
        assert "{extraction_guidance}" in template
        assert "# Role" in template

    def test_load_job_description_template(self) -> None:
        template = load_template("job_description.txt")
        assert "{job_id}" in template
        assert "{file_count}" in template
        assert "{total_characters}" in template
        assert "{file_list}" in template

    def test_load_nonexistent_template_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_template("nonexistent.txt")


# --- System prompt template content ---


class TestSystemPromptTemplate:
    """Verify the static template covers all universal sections from spec."""

    def test_has_role_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Role" in template
        assert "knowledge extraction agent" in template

    def test_has_task_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Task" in template
        assert "job description" in template

    def test_has_access_permissions_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Access Permissions" in template
        assert "READ access" in template
        assert "MUST NOT" in template

    def test_has_available_tools_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Available Tools" in template
        assert "search_entities" in template
        assert "search_relationships" in template
        assert "manage_entity" in template
        assert "manage_relationship" in template
        assert "validate_and_commit" in template

    def test_has_workflow_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Workflow" in template

    def test_has_extraction_guidance_placeholder(self) -> None:
        template = load_template("system_prompt.txt")
        assert "{extraction_guidance}" in template

    def test_has_efficiency_rules_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Efficiency Rules" in template
        assert "autonomously" in template
        assert "Do not narrate" in template
        assert "Do not explain your reasoning" in template
        assert "Do not summarize" in template
        assert "batch processing" in template

    def test_has_quality_rules_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Quality Rules" in template
        assert "duplicate" in template
        assert "consistent slug" in template
        assert "one at a time" in template

    def test_has_completion_section(self) -> None:
        template = load_template("system_prompt.txt")
        assert "# Completion" in template
        assert "validate_and_commit" in template


# --- build_guidance_prompt ---


class TestBuildGuidancePrompt:
    def test_includes_problem_statement(
        self, sample_ontology: OntologyConfig, problem_statement: str
    ) -> None:
        prompt = build_guidance_prompt(sample_ontology, problem_statement)
        assert problem_statement in prompt

    def test_includes_entity_types(self, sample_ontology: OntologyConfig) -> None:
        prompt = build_guidance_prompt(sample_ontology, "test problem")
        assert "TestCase" in prompt
        assert "Component" in prompt
        assert "Individual test function/method" in prompt
        assert "A software module or subsystem" in prompt

    def test_includes_required_properties(
        self, sample_ontology: OntologyConfig
    ) -> None:
        prompt = build_guidance_prompt(sample_ontology, "test problem")
        assert "name, framework" in prompt

    def test_includes_optional_properties(
        self, sample_ontology: OntologyConfig
    ) -> None:
        prompt = build_guidance_prompt(sample_ontology, "test problem")
        assert "timeout" in prompt

    def test_includes_tag_definitions(self, sample_ontology: OntologyConfig) -> None:
        prompt = build_guidance_prompt(sample_ontology, "test problem")
        assert "integration" in prompt
        assert "unit" in prompt

    def test_includes_relationship_types(self, sample_ontology: OntologyConfig) -> None:
        prompt = build_guidance_prompt(sample_ontology, "test problem")
        assert "TESTS" in prompt
        assert "TestCase" in prompt
        assert "Component" in prompt

    def test_includes_relationship_optional_properties(self) -> None:
        ontology = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Service",
                    description="A service",
                    required_properties=["name"],
                    optional_properties=[],
                    tag_definitions={},
                ),
            ],
            relationship_types=[
                RelationshipTypeConfig(
                    label="CALLS",
                    description="Service calls another",
                    source_entity_type="Service",
                    target_entity_type="Service",
                    required_properties=["protocol"],
                    optional_properties=["latency", "timeout"],
                ),
            ],
        )
        prompt = build_guidance_prompt(ontology, "test")
        assert "Optional properties: latency, timeout" in prompt

    def test_no_properties_shows_none(self) -> None:
        ontology = OntologyConfig(
            entity_types=[
                EntityTypeConfig(
                    label="Simple",
                    description="A simple type",
                    required_properties=[],
                    optional_properties=[],
                    tag_definitions={},
                ),
            ],
            relationship_types=[],
        )
        prompt = build_guidance_prompt(ontology, "test")
        assert "(none)" in prompt

    def test_asks_for_guidance_output(self, sample_ontology: OntologyConfig) -> None:
        prompt = build_guidance_prompt(sample_ontology, "test problem")
        assert "## Entity Types" in prompt
        assert "## Relationship Types" in prompt
        assert "## Priorities" in prompt


# --- generate_extraction_guidance ---


class TestGenerateExtractionGuidance:
    @pytest.mark.asyncio
    async def test_calls_llm_with_built_prompt(
        self, sample_ontology: OntologyConfig, problem_statement: str
    ) -> None:
        captured_prompt: list[str] = []

        async def fake_llm(prompt: str) -> str:
            captured_prompt.append(prompt)
            return "Generated guidance text"

        result = await generate_extraction_guidance(
            sample_ontology, problem_statement, fake_llm
        )

        assert result == "Generated guidance text"
        assert len(captured_prompt) == 1
        # Verify the prompt contains expected content
        assert problem_statement in captured_prompt[0]
        assert "TestCase" in captured_prompt[0]

    @pytest.mark.asyncio
    async def test_returns_llm_output(self, sample_ontology: OntologyConfig) -> None:
        guidance = "## Entity Types\n### TestCase\nExtract individual tests..."

        async def fake_llm(_prompt: str) -> str:
            return guidance

        result = await generate_extraction_guidance(
            sample_ontology, "test problem", fake_llm
        )
        assert result == guidance


# --- compose_system_prompt ---


class TestComposeSystemPrompt:
    def test_inserts_extraction_guidance(self) -> None:
        guidance = "## Entity Types\n### TestCase\nExtract test functions."
        result = compose_system_prompt(guidance)
        assert guidance in result

    def test_includes_static_template_sections(self) -> None:
        result = compose_system_prompt("Test guidance")
        assert "# Role" in result
        assert "# Workflow" in result
        assert "# Efficiency Rules" in result
        assert "# Quality Rules" in result
        assert "# Completion" in result

    def test_placeholder_is_replaced(self) -> None:
        result = compose_system_prompt("My custom guidance")
        assert "{extraction_guidance}" not in result
        assert "My custom guidance" in result

    def test_guidance_with_literal_braces(self) -> None:
        """LLM-generated guidance containing braces must not crash."""
        guidance = 'Use format {"type": "Entity"} for output.'
        result = compose_system_prompt(guidance)
        assert guidance in result
        assert "{extraction_guidance}" not in result


# --- load_job_description_template ---


class TestLoadJobDescriptionTemplate:
    def test_returns_template_with_placeholders(self) -> None:
        template = load_job_description_template()
        assert "{job_id}" in template
        assert "{file_count}" in template
        assert "{total_characters}" in template
        assert "{file_list}" in template


# --- substitute_job_variables ---


class TestSubstituteJobVariables:
    def test_substitutes_all_variables(self) -> None:
        template = load_job_description_template()
        result = substitute_job_variables(
            template,
            job_id="job-42",
            file_count=10,
            total_characters=50000,
            file_list="- file1.py\n- file2.py",
        )
        assert "job-42" in result
        assert "10" in result
        assert "50000" in result
        assert "- file1.py" in result
        assert "- file2.py" in result

    def test_no_placeholders_remain(self) -> None:
        template = load_job_description_template()
        result = substitute_job_variables(
            template,
            job_id="j1",
            file_count=1,
            total_characters=100,
            file_list="- test.py",
        )
        assert "{job_id}" not in result
        assert "{file_count}" not in result
        assert "{total_characters}" not in result
        assert "{file_list}" not in result

    def test_custom_template(self) -> None:
        template = (
            "Job {job_id}: {file_count} files, {total_characters} chars\n{file_list}"
        )
        result = substitute_job_variables(
            template,
            job_id="abc",
            file_count=3,
            total_characters=999,
            file_list="a.py\nb.py\nc.py",
        )
        assert result == ("Job abc: 3 files, 999 chars\na.py\nb.py\nc.py")

    def test_literal_braces_in_user_edited_template(self) -> None:
        """Templates with literal braces (e.g., JSON examples) must not crash."""
        template = (
            "Job {job_id}: process {file_count} files.\n"
            'Example output: {"entity": "test"}\n'
            "{file_list}\n"
            "Total: {total_characters} chars"
        )
        result = substitute_job_variables(
            template,
            job_id="j1",
            file_count=2,
            total_characters=500,
            file_list="- a.py",
        )
        assert "j1" in result
        assert '{"entity": "test"}' in result
        assert "- a.py" in result
        assert "500" in result
