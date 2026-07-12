"""Hermes/Holo3 automation bundle generation and strict result parsing."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automation_foundry.authoring.evidence import EvidencePackage
from automation_foundry.authoring.workspace import GenerationJob, WorkspaceBridge
from automation_foundry.contracts import (
    EvidenceSourceType,
    InputDefinition,
    ProcedureCheck,
    ProcedureStep,
    ReviewConflict,
)

_SYSTEM_PROMPT = """You are the ingestion agent for Computer-Use Automation Foundry.
Convert the staged video/SOP evidence into an application-independent desktop workflow bundle.

Hard requirements:
- Analyze the provided video/SOP evidence directly, or read the staged paths when running inside a workspace agent.
- Return one JSON object satisfying the supplied schema; do not wrap it in Markdown.
- Cite video timestamps or SOP pages/sections for procedural claims when evidence exists.
- Mark unsupported claims as inferences that require review.
- Never emit screen coordinates, selectors, source-app geometry, secrets, or CRM-B-specific knowledge.
- Do not assume selecting a record makes its fields editable. After locating and selecting the exact record, instruct
  the agent to inspect whether an editor is open and, if needed, use a visible Open Record, Edit, or View Details action.
- Avoid source-layout phrases such as left-hand list, right pane, fixed table column, or modal position.
- Preserve source disagreements as explicit conflicts. Never silently choose one source.
- Include a mandatory stop-and-review boundary before Save, Commit, Submit, or an equivalent persistent action.
- SKILL Markdown must begin with YAML frontmatter containing a non-empty `description` field.
- Any step that clicks or invokes Save, Commit, Submit, or Apply must set `persistent_action=true` and
  `requires_confirmation_before=true`; the confirmation guard belongs on that exact action step.
- Generated Python may only parse, normalize, map, or validate JSON-compatible data. It may not use network,
  subprocess, arbitrary files, dynamic evaluation, or desktop control.
- `skill_markdown` is Markdown-only Holo procedure text; never put Python code in it.
- Ordinary visible desktop form workflows need no generated Python: set `tools_code` and `tool_tests_code` to empty
  strings and `tools` to an empty list unless deterministic non-UI data transformation is genuinely required.
- Choose exactly one canonical CRM input contract based on the demonstrated operation:
  * Update an existing record: require `lead_name` as the selector and `new_<field>` for changed values, such as
    `new_first_name`, `new_last_name`, `new_company`, `new_phone`, `new_email`, `new_lifecycle_status`,
    `new_owner_name`, or `new_notes`. Do not use bare `first_name`/`last_name` for an update.
  * Create a new record: require `first_name` and `last_name`; optionally use `company`, `phone`, `email`,
    `lifecycle_status`, `owner_name`, and `notes`. Do not use `lead_name` for creation.
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


@runtime_checkable
class DirectMediaClient(Protocol):
    """Hosted multimodal client that receives uploaded media directly."""

    async def complete_bundle(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        evidence_json: str,
        response_schema: dict[str, Any],
        video_paths: list[Path],
    ) -> str:
        """Return one schema-constrained bundle from text and uploaded videos."""
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


class OpenRouterVideoClientConfig:
    """Configuration for direct video ingestion through OpenRouter."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "google/gemini-3.5-flash",
        timeout_seconds: float = 600,
        max_video_bytes: int = 50_000_000,
    ):
        if not api_key:
            raise ValueError("OpenRouter API key is required")
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_video_bytes = max_video_bytes

    def make(self) -> OpenRouterVideoClient:
        """Build the hosted direct-video client."""
        return OpenRouterVideoClient(self)


class OpenRouterVideoClient:
    """Send the original uploaded video to Gemini through OpenRouter."""

    def __init__(self, config: OpenRouterVideoClientConfig):
        from openai import AsyncOpenAI

        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            default_headers={
                "HTTP-Referer": "https://github.com/exemplary-citizen/computer-use-hackathon",
                "X-Title": "Computer-Use Automation Foundry",
            },
        )

    async def complete_bundle(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        evidence_json: str,
        response_schema: dict[str, Any],
        video_paths: list[Path],
    ) -> str:
        """Generate a strict bundle from inline metadata and original video bytes."""
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{user_prompt}\n\n"
                    "The authoritative normalized evidence is included below. "
                    "Analyze every attached demonstration video in temporal order, including its audio. "
                    "Cite timestamps for claims grounded in video evidence. Return one JSON object matching "
                    "the required schema below; local validation will reject missing or extra fields.\n\n"
                    f"<required_json_schema>\n{json.dumps(response_schema, separators=(',', ':'))}\n"
                    "</required_json_schema>\n\n"
                    f"<evidence>\n{evidence_json}\n</evidence>"
                ),
            }
        ]
        media_types = {
            ".mp4": "video/mp4",
            ".mpeg": "video/mpeg",
            ".mpg": "video/mpeg",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
        }
        for video_path in video_paths:
            size = video_path.stat().st_size
            if size > self.config.max_video_bytes:
                raise ValueError(
                    f"Video exceeds the {self.config.max_video_bytes // 1_000_000} MB direct-ingestion limit"
                )
            media_type = media_types.get(video_path.suffix.casefold())
            if media_type is None:
                raise ValueError(f"Unsupported direct-video format: {video_path.suffix}")
            encoded = base64.b64encode(video_path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": f"data:{media_type};base64,{encoded}"},
                }
            )

        messages: Any = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]
        response = await self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=12_000,
            temperature=0.2,
        )
        result = response.choices[0].message.content
        if not result:
            raise RuntimeError("Gemini returned an empty generation response")
        return result


class BundleGenerator:
    """Stage evidence, call Hermes, and validate the untrusted bundle draft."""

    def __init__(self, workspace: WorkspaceBridge, client: HermesClient | DirectMediaClient):
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
        if isinstance(self.client, DirectMediaClient):
            response = await self.client.complete_bundle(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_generation_prompt(job),
                evidence_json=evidence.model_dump_json(indent=2),
                response_schema=schema,
                video_paths=_video_source_paths(self.workspace, automation_id),
            )
        else:
            response = await self.client.complete(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=_generation_prompt(job),
            )
        self.workspace.write_result(job, response)
        return _parse_result(self.workspace.read_result(job), evidence=evidence)


def _generation_prompt(job: GenerationJob) -> str:
    return (
        f"Generation job {job.id}\n"
        f"Evidence: {job.evidence_path}\n"
        f"Required JSON schema: {job.schema_path}\n"
        f"Write/return the final JSON bundle for: {job.output_path}\n"
        "The target must remain portable from the taught CRM to a visually different desktop CRM."
    )


def _video_source_paths(workspace: WorkspaceBridge, automation_id: UUID) -> list[Path]:
    """Resolve uploaded video bytes while refusing paths outside the automation root."""
    manifest = workspace.store.get_manifest(automation_id)
    automation_root = workspace.store.automation_root(automation_id).resolve()
    paths: list[Path] = []
    for source in manifest.sources:
        if source.source_type is not EvidenceSourceType.VIDEO:
            continue
        path = (automation_root / source.relative_path).resolve()
        if not path.is_relative_to(automation_root) or not path.is_file():
            raise ValueError(f"Unsafe or missing video source: {source.relative_path}")
        paths.append(path)
    return paths


def _parse_result(content: str, *, evidence: EvidencePackage | None = None) -> GeneratedBundleDraft:
    try:
        payload = json.loads(content)
        if evidence is not None and isinstance(payload, dict):
            _bind_unambiguous_source_ids(payload, evidence)
            _ensure_skill_frontmatter(payload)
            _guard_persistent_action_steps(payload)
            _ensure_skill_safety_procedure(payload)
        return GeneratedBundleDraft.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("Generation provider returned an invalid automation bundle") from exc


def _bind_unambiguous_source_ids(payload: dict[str, Any], evidence: EvidencePackage) -> None:
    """Bind model citations to exact staged sources and frame timestamps when unambiguous."""
    source_ids = {
        "video": [str(video.source_id) for video in evidence.videos],
        "sop": [str(document.source_id) for document in evidence.sop_documents],
    }
    video_frames = {
        frame_path: (
            str(video.source_id),
            min(float(index * video.frame_interval_seconds), video.duration_seconds),
        )
        for video in evidence.videos
        for index, frame_path in enumerate(video.frame_paths)
    }
    for collection_name in ("steps", "preconditions", "completion_checks", "conflicts"):
        collection = payload.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict) or not isinstance(item.get("evidence"), list):
                continue
            for reference in item["evidence"]:
                if not isinstance(reference, dict):
                    continue
                frame_path = reference.get("frame_path")
                frame = video_frames.get(frame_path) if isinstance(frame_path, str) else None
                if reference.get("source_type") == "video" and frame is not None:
                    reference.setdefault("source_id", frame[0])
                    reference.setdefault("timestamp_seconds", frame[1])
                if reference.get("source_id") is not None:
                    continue
                source_type = reference.get("source_type")
                candidates = source_ids.get(source_type, []) if isinstance(source_type, str) else []
                if len(candidates) == 1:
                    reference["source_id"] = candidates[0]


def _ensure_skill_frontmatter(payload: dict[str, Any]) -> None:
    """Add required metadata when a generated skill body omitted only its frontmatter."""
    skill = payload.get("skill_markdown")
    if not isinstance(skill, str) or skill.lstrip().startswith("---"):
        return
    body = skill.strip()
    title = next((line.lstrip("# ").strip() for line in body.splitlines() if line.strip()), "")
    description = (title or "Generated desktop automation skill.")[:240]
    payload["skill_markdown"] = f"---\ndescription: {json.dumps(description)}\n---\n\n{body}\n"


_PERSISTENT_ACTION_PATTERN = re.compile(
    r"\b(?:click|press|choose|select|invoke|trigger)\b[^.\n]{0,160}"
    r"\b(?:save|commit|submit|apply changes?)\b",
    flags=re.IGNORECASE,
)


def _guard_persistent_action_steps(payload: dict[str, Any]) -> None:
    """Conservatively add approval flags to explicit persistent UI actions."""
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        instruction = step.get("instruction")
        if isinstance(instruction, str) and _PERSISTENT_ACTION_PATTERN.search(instruction):
            step["persistent_action"] = True
            step["requires_confirmation_before"] = True


def _ensure_skill_safety_procedure(payload: dict[str, Any]) -> None:
    """Repair code-like or unguarded skill text from already-generated semantic steps."""
    skill = payload.get("skill_markdown")
    if not isinstance(skill, str):
        return
    parts = skill.split("---", 2)
    body = parts[2].strip() if len(parts) == 3 else skill.strip()
    code_like = bool(re.search(r"(?m)^\s*(?:import\s+\w+|from\s+\w+\s+import|def\s+\w+\s*\()", body))
    has_stop = bool(re.search(r"\b(?:stop|wait|pause|approval|approve|review)\b", body, re.IGNORECASE))
    has_commit = bool(re.search(r"\b(?:save|commit|submit|apply|add record)\b", body, re.IGNORECASE))
    if not code_like and has_stop and has_commit:
        return

    frontmatter = f"---{parts[1]}---" if len(parts) == 3 else "---\ndescription: Generated desktop workflow.\n---"
    procedure_lines = ["# Procedure", ""]
    steps = payload.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or not isinstance(step.get("instruction"), str):
                continue
            instruction = step["instruction"].strip()
            if step.get("persistent_action"):
                procedure_lines.append(
                    f"{index}. STOP and wait for explicit approval. Only after approval: {instruction}"
                )
            else:
                procedure_lines.append(f"{index}. {instruction}")
    procedure_lines.extend(
        [
            "",
            "## Safety boundary",
            "",
            "During staging, stop before Save, Commit, Submit, Apply, or Add Record. "
            "Perform the persistent action only after explicit human approval.",
        ]
    )
    payload["skill_markdown"] = f"{frontmatter}\n\n{'\n'.join(procedure_lines)}\n"
