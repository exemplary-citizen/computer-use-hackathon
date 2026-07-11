"""Hermes/Holo3 automation bundle generation and strict result parsing."""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automation_foundry.authoring.evidence import EvidencePackage
from automation_foundry.authoring.workspace import GenerationJob, WorkspaceBridge
from automation_foundry.contracts import InputDefinition, ProcedureCheck, ProcedureStep, ReviewConflict

_SYSTEM_PROMPT = """You are the ingestion agent for Computer-Use Automation Foundry.
Convert the staged video/SOP evidence into an application-independent desktop workflow bundle.

Hard requirements:
- Read the evidence and schema files at the exact sandbox paths supplied by the user.
- Return one JSON object satisfying the supplied schema; do not wrap it in Markdown.
- Cite video timestamps or SOP pages/sections for procedural claims when evidence exists.
- Mark unsupported claims as inferences that require review.
- Never emit screen coordinates, selectors, source-app geometry, secrets, or CRM-B-specific knowledge.
- Preserve source disagreements as explicit conflicts. Never silently choose one source.
- Include a mandatory stop-and-review boundary before Save, Commit, Submit, or an equivalent persistent action.
- Generated Python may only parse, normalize, map, or validate JSON-compatible data. It may not use network,
  subprocess, arbitrary files, dynamic evaluation, or desktop control.
"""


class DraftTool(BaseModel):
    """Generated pure-data tool before code hashing and approval."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=1_000)
    entrypoint: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class GeneratedBundleDraft(BaseModel):
    """Strict unapproved output expected from Hermes/Holo3."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    sop_markdown: str = Field(min_length=1)
    skill_markdown: str = Field(min_length=1)
    inputs: list[InputDefinition]
    input_schema: dict[str, Any]
    steps: list[ProcedureStep] = Field(min_length=1)
    preconditions: list[ProcedureCheck]
    completion_checks: list[ProcedureCheck] = Field(min_length=1)
    tools_code: str = ""
    tools: list[DraftTool] = Field(default_factory=list)
    tool_tests_code: str = ""
    eval_cases: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[ReviewConflict] = Field(default_factory=list)


class HermesClient(Protocol):
    """Conversation adapter implemented by the live OpenAI-compatible client."""

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return the assistant's final text response."""
        ...


class HermesClientConfig:
    """Configuration for NemoClaw Hermes' local API forward."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://127.0.0.1:8642/v1",
        model: str = "hermes",
        timeout_seconds: float = 600,
    ):
        """Store API settings without reading environment files.

        Args:
            api_key: Hermes bearer token supplied by application settings.
            base_url: Forwarded local OpenAI-compatible endpoint.
            model: Model selector accepted by the Hermes API.
            timeout_seconds: End-to-end generation timeout.
        """
        if not api_key:
            raise ValueError("Hermes API key is required")
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds

    def make(self) -> OpenAIHermesClient:
        """Build the live OpenAI-compatible client."""
        return OpenAIHermesClient(self)


class OpenAIHermesClient:
    """Call the local NemoClaw Hermes chat-completions endpoint."""

    def __init__(self, config: HermesClientConfig):
        """Initialize an OpenAI-compatible client.

        Args:
            config: Local endpoint, bearer token, model, and timeout.
        """
        from openai import AsyncOpenAI

        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return one JSON-mode Hermes response."""
        response = await self.client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Hermes returned an empty generation response")
        return content


class BundleGenerator:
    """Stage evidence, call Hermes, and validate the untrusted bundle draft."""

    def __init__(self, workspace: WorkspaceBridge, client: HermesClient):
        """Initialize generation dependencies.

        Args:
            workspace: Initialized NemoClaw shared-workspace bridge.
            client: Live or fake Hermes conversation client.
        """
        self.workspace = workspace
        self.client = client

    async def generate(self, automation_id: UUID, evidence: EvidencePackage) -> GeneratedBundleDraft:
        """Generate and strictly validate one bundle draft.

        Args:
            automation_id: Owning automation.
            evidence: Normalized evidence package.

        Returns:
            Schema-valid unapproved bundle draft.
        """
        schema = GeneratedBundleDraft.model_json_schema()
        job = self.workspace.stage_generation(automation_id, evidence, result_schema=schema)
        response = await self.client.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_generation_prompt(job),
        )
        self.workspace.write_result(job, response)
        return _parse_result(self.workspace.read_result(job))


def _generation_prompt(job: GenerationJob) -> str:
    return (
        f"Generation job {job.id}\n"
        f"Evidence: {job.evidence_path}\n"
        f"Required JSON schema: {job.schema_path}\n"
        f"Write/return the final JSON bundle for: {job.output_path}\n"
        "The target must remain portable from the taught CRM to a visually different desktop CRM."
    )


def _parse_result(content: str) -> GeneratedBundleDraft:
    try:
        payload = json.loads(content)
        return GeneratedBundleDraft.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("Hermes returned an invalid automation bundle") from exc
