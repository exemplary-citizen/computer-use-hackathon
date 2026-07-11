"""Voice intent resolution: final transcripts in, structured commands out.

Safety rules (FR-V02/V03): only FINAL transcripts are ever resolved; the
approval vocabulary is a closed set matched only when a run is actually
awaiting commit approval; cancellation words can never be read as approval;
anything ambiguous or incomplete resolves to a clarification, never a run.

Two resolvers behind one interface: a deterministic rule-based resolver
(default — reliable in a noisy demo room) and an LLM resolver that calls the
configured Hermes-compatible endpoint with a structured-output prompt and
falls back to the rules on any failure. Select with ``FOUNDRY_INTENT_MODE``.
"""

from __future__ import annotations

import json
import re
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from desktop_fixtures.store import STATUS_VALUES, AppKey

CommandKind = Literal[
    "run_command",
    "approval",
    "rejection",
    "cancellation",
    "status_query",
    "clarification",
]

_APPROVE_WORDS = ("approve", "approved", "confirm the change", "go ahead and save")
_REJECT_WORDS = ("reject", "rejected", "deny", "don't save", "do not save")
_CANCEL_WORDS = ("cancel", "stop", "abort", "never mind")
_STATUS_WORDS = ("status", "what's happening", "how is the run", "progress")
_APP_HINTS: tuple[tuple[str, AppKey], ...] = (
    ("crm b", "b"),
    ("crm_b", "b"),
    ("meridian", "b"),
    ("crm a", "a"),
    ("crm_a", "a"),
    ("northlight", "a"),
)
_OWNER_PATTERN = re.compile(r"(?:assign(?:ed)?\s+(?:it\s+)?to|owner\s+(?:to|is)|give\s+it\s+to)\s+([a-z' -]+)", re.I)


class VoiceContext(BaseModel):
    """What the resolver may know at resolution time."""

    awaiting_commit_approval: bool = False
    known_record_names: list[str] = Field(default_factory=list)
    automation_name: str = "Update CRM lead"


class ResolvedCommand(BaseModel):
    """Structured interpretation of one final utterance."""

    kind: CommandKind
    target_app: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    echo: str = ""
    """Confirmation sentence shown and spoken back before anything starts."""


class IntentResolver(Protocol):
    """Resolves one final transcript into a command."""

    def resolve(self, utterance: str, context: VoiceContext) -> ResolvedCommand:
        """Interpret a final transcript.

        Args:
            utterance: The FINAL transcript (never a partial).
            context: Approval state and known records.
        """
        ...


class RuleBasedIntentResolver:
    """Deterministic resolver over the demo bundle's three inputs."""

    def resolve(self, utterance: str, context: VoiceContext) -> ResolvedCommand:
        """Interpret a final transcript with closed-vocabulary rules.

        Args:
            utterance: The final transcript.
            context: Approval state and known records.
        """
        text = " ".join(utterance.lower().split())
        wants_cancel = any(word in text for word in _CANCEL_WORDS)
        wants_reject = any(word in text for word in _REJECT_WORDS)
        wants_approve = any(word in text for word in _APPROVE_WORDS)

        if context.awaiting_commit_approval:
            if wants_cancel and not wants_reject:
                # Cancellation can never be read as approval (FR-V02).
                return ResolvedCommand(kind="cancellation", echo="Cancelling the run; nothing will be saved.")
            if wants_reject and not wants_approve:
                return ResolvedCommand(kind="rejection", echo="Rejecting the staged change; nothing will be saved.")
            if wants_approve and not (wants_reject or wants_cancel):
                return ResolvedCommand(kind="approval", echo="Approving the staged change.")
            if wants_approve:
                return ResolvedCommand(
                    kind="clarification",
                    missing=["decision"],
                    echo="I heard both approval and cancellation words. Say exactly 'approve', 'reject', or 'cancel'.",
                )
        if wants_cancel:
            return ResolvedCommand(kind="cancellation", echo="Cancelling.")
        if any(word in text for word in _STATUS_WORDS):
            return ResolvedCommand(kind="status_query", echo="Checking the run status.")

        inputs: dict[str, str] = {}
        record = self._match_record(text, context.known_record_names)
        if record:
            inputs["lead_name"] = record
        status = next((value for value in STATUS_VALUES if value.lower() in text), None)
        if status:
            inputs["lifecycle_status"] = status
        owner_match = _OWNER_PATTERN.search(utterance)
        if owner_match:
            # Trim trailing location phrases ("… to Priya Shah in Meridian").
            owner = re.split(r"\s+(?:in|on|for|at|inside|using)\s+", owner_match.group(1).strip(), maxsplit=1)[0]
            inputs["owner_name"] = owner.strip().title()

        if not inputs:
            return ResolvedCommand(
                kind="clarification",
                missing=["lead_name", "lifecycle_status", "owner_name"],
                echo="Tell me the lead, the new lifecycle status, and the owner — for example: "
                "'set Sarah Chen to Qualified and assign to Priya Shah in CRM A'.",
            )

        target_app = next((f"crm_{app}" for hint, app in _APP_HINTS if hint in text), None)
        missing = [name for name in ("lead_name", "lifecycle_status", "owner_name") if name not in inputs]
        if target_app is None:
            missing.append("target_app")
        if missing:
            spoken = ", ".join(missing).replace("_", " ")
            return ResolvedCommand(
                kind="clarification",
                target_app=target_app,
                inputs=inputs,
                missing=missing,
                echo=f"I still need: {spoken}.",
            )
        assert target_app is not None  # guarded by the missing check above
        echo = (
            f"Run '{context.automation_name}' on {target_app.replace('_', ' ').upper()}: "
            f"set {inputs['lead_name']} to {inputs['lifecycle_status']}, owner {inputs['owner_name']}. "
            "Say 'confirm' to start."
        )
        return ResolvedCommand(kind="run_command", target_app=target_app, inputs=inputs, echo=echo)

    def _match_record(self, text: str, known_names: list[str]) -> str | None:
        for name in known_names:
            if name.lower() in text:
                return name
        return None


class LlmIntentResolver:
    """Structured-output resolver against the Hermes-compatible endpoint.

    Falls back to the rule-based resolver on any failure — the demo must
    survive the model endpoint being down.
    """

    def __init__(self, base_url: str, api_key: str | None, model: str):
        """Configure the OpenAI-compatible client lazily.

        Args:
            base_url: Hermes-compatible chat-completions endpoint.
            api_key: Bearer token, if the endpoint requires one.
            model: Model name to request.
        """
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._fallback = RuleBasedIntentResolver()

    def resolve(self, utterance: str, context: VoiceContext) -> ResolvedCommand:
        """Interpret via the LLM; fall back to rules on any failure.

        Args:
            utterance: The final transcript.
            context: Approval state and known records.
        """
        try:
            return self._resolve_llm(utterance, context)
        except Exception:
            return self._fallback.resolve(utterance, context)

    def _resolve_llm(self, utterance: str, context: VoiceContext) -> ResolvedCommand:
        from openai import OpenAI

        client = OpenAI(base_url=self._base_url, api_key=self._api_key or "unused")
        schema = ResolvedCommand.model_json_schema()
        completion = client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Resolve a voice command for a CRM automation runner. Respond with ONLY JSON matching "
                        f"this schema: {json.dumps(schema)}. Known records: {context.known_record_names}. "
                        f"Awaiting commit approval: {context.awaiting_commit_approval}. Valid lifecycle statuses: "
                        f"{list(STATUS_VALUES)}. Valid target_app values: crm_a, crm_b. Ambiguous or incomplete "
                        "commands MUST resolve to kind='clarification'; never guess missing values."
                    ),
                },
                {"role": "user", "content": utterance},
            ],
            response_format={"type": "json_object"},
            timeout=8,
        )
        content = completion.choices[0].message.content or "{}"
        return ResolvedCommand.model_validate_json(content)
