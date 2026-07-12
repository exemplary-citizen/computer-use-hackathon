"""Telegram review and two-turn execution over shared host services."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from automation_foundry.authoring.service import AuthoringService
from automation_foundry.contracts import (
    AutomationManifest,
    AutomationStatus,
    AutomationVersion,
    InvocationSource,
    RunState,
    SurfaceCallbackAction,
    SurfaceCallbackGrant,
)
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.errors import ExecutionFault
from automation_foundry.execution.machine import InputValidationError, RunCoordinator
from automation_foundry.surfaces.telegram.callbacks import TelegramCallbackRejectedError
from automation_foundry.surfaces.telegram.interactions import (
    TelegramButton,
    TelegramInboundUpdate,
    TelegramInteractionService,
    TelegramSurfaceResponse,
)

_TERMINAL_RUN_STATES = {RunState.SUCCEEDED.value, RunState.FAILED.value, RunState.CANCELLED.value}


@dataclass(frozen=True)
class TelegramExecutionResult:
    """Immediate Telegram response plus an optional run to monitor."""

    response: TelegramSurfaceResponse
    watch_run_id: UUID | None = None
    watch_terminal: bool = False


class TelegramExecutionCoordinator:
    """Route owner commands into authoring approval and the shared run state machine."""

    def __init__(
        self,
        authoring: AuthoringService,
        interactions: TelegramInteractionService,
        execution_settings: ExecutionSettings,
        coordinator_factory: Callable[[ExecutionSettings], RunCoordinator] | None = None,
    ):
        """Initialize Telegram execution state.

        Args:
            authoring: Shared authoring and approval service.
            interactions: Owner identity and callback vault.
            execution_settings: Trusted host execution configuration.
            coordinator_factory: Injectable run-coordinator factory for deterministic tests.
        """
        self.authoring = authoring
        self.interactions = interactions
        self.execution_settings = execution_settings
        self._coordinator_factory = coordinator_factory or RunCoordinator
        self._pending_inputs: dict[int, UUID] = {}
        self._coordinators: dict[UUID, RunCoordinator] = {}
        self._run_coordinators: dict[UUID, RunCoordinator] = {}
        self._auto_commit_run_ids: set[UUID] = set()

    def expects_input(self, telegram_user_id: int) -> bool:
        """Return whether a previous `/run` is collecting schema fields.

        Args:
            telegram_user_id: Authenticated Telegram owner ID.
        """
        return telegram_user_id in self._pending_inputs

    async def handle_message(self, update: TelegramInboundUpdate) -> TelegramExecutionResult:
        """Handle `/review`, `/run`, or a pending runtime-input reply.

        Args:
            update: Authorized owner direct-message update.
        """
        text = (update.text or "").strip()
        try:
            if text.startswith("/review "):
                return TelegramExecutionResult(self.review(update, text.removeprefix("/review ").strip()))
            if text.startswith("/run "):
                return TelegramExecutionResult(self.begin_run(update, text.removeprefix("/run ").strip()))
            if self.expects_input(update.user_id):
                return TelegramExecutionResult(await self.collect_inputs(update, text))
        except ValueError as exc:
            return TelegramExecutionResult(TelegramSurfaceResponse(text=str(exc)))
        return TelegramExecutionResult(
            TelegramSurfaceResponse(text="Use /review <automation> or /run <approved automation>.")
        )

    def review(self, update: TelegramInboundUpdate, selector: str) -> TelegramSurfaceResponse:
        """Create a hash-bound automation review button.

        Args:
            update: Authorized owner update.
            selector: Exact automation name or UUID.
        """
        manifest = self._resolve_automation(selector)
        if manifest.current_version is None:
            return TelegramSurfaceResponse(text="That automation has no generated version yet.")
        version = self.authoring.load_version(manifest.id, manifest.current_version)
        report = self.authoring.validation(manifest.id, version.version)
        input_names = ", ".join(item.name for item in version.inputs) or "none"
        persistent = next((step.instruction for step in version.steps if step.persistent_action), "none")
        summary = (
            f"Review `{manifest.name}` version {version.version}\n"
            f"Runtime inputs: {input_names}\n"
            f"Approval-gated action: {persistent}\n"
            f"Host validation: {'passed' if report.valid else 'blocked'}"
        )
        if manifest.status is AutomationStatus.APPROVED and manifest.approved_version == version.version:
            return TelegramSurfaceResponse(text=f"{summary}\nAlready approved. Use /run {manifest.name}")
        if not report.valid:
            return TelegramSurfaceResponse(text=f"{summary}\nResolve validation errors before approval.")
        payload_hash = _artifact_payload_hash(version)
        issued = self.interactions.callbacks.mint(
            action=SurfaceCallbackAction.APPROVE_AUTOMATION,
            telegram_user_id=update.user_id,
            telegram_chat_id=update.chat_id,
            payload_sha256=payload_hash,
            automation_id=manifest.id,
        )
        return TelegramSurfaceResponse(
            text=summary,
            buttons=(TelegramButton(label="Approve automation", callback_data=issued.callback_data),),
        )

    def begin_run(self, update: TelegramInboundUpdate, selector: str) -> TelegramSurfaceResponse:
        """Begin schema-driven runtime input collection.

        Args:
            update: Authorized owner update.
            selector: Exact approved automation name or UUID.
        """
        manifest = self._resolve_automation(selector)
        if manifest.status is not AutomationStatus.APPROVED or manifest.approved_version is None:
            return TelegramSurfaceResponse(text=f"Review and approve it first with /review {manifest.name}")
        version = self.authoring.load_version(manifest.id, manifest.approved_version)
        self._pending_inputs[update.user_id] = manifest.id
        fields = ["target_app"] + [item.name for item in version.inputs if item.required]
        example = "; ".join(f"{name}=..." for name in fields)
        return TelegramSurfaceResponse(text=f"Send runtime values for `{manifest.name}` in one reply:\n`{example}`")

    async def collect_inputs(self, update: TelegramInboundUpdate, text: str) -> TelegramSurfaceResponse:
        """Validate a runtime-input reply and mint Start.

        Args:
            update: Authorized owner update.
            text: Untrusted `key=value` pairs separated by semicolons or lines.
        """
        automation_id = self._pending_inputs[update.user_id]
        manifest = self.authoring.store.get_manifest(automation_id)
        try:
            values = _parse_runtime_values(text)
        except ValueError:
            return TelegramSurfaceResponse(text="Send all runtime inputs as `key=value` pairs separated by semicolons.")
        target_app = values.pop("target_app", "").strip()
        if not target_app:
            return TelegramSurfaceResponse(text="`target_app` is required. Send all values again.")
        coordinator = await self._coordinator(manifest)
        runtime_inputs: dict[str, object] = dict(values)
        try:
            preview = await coordinator.prepare(target_app, runtime_inputs, InvocationSource.TELEGRAM)
        except InputValidationError as exc:
            problems = "; ".join(f"{name}: {message}" for name, message in sorted(exc.field_errors.items()))
            return TelegramSurfaceResponse(text=f"Fix these inputs and send all values again: {problems}")
        except ExecutionFault as exc:
            return TelegramSurfaceResponse(text=exc.spec.message)
        self._pending_inputs.pop(update.user_id, None)
        self._run_coordinators[preview.request.id] = coordinator
        if self._auto_commit_enabled(target_app):
            self._auto_commit_run_ids.add(preview.request.id)
        payload_hash = _run_request_hash(preview.request.model_dump(mode="json"))
        issued = self.interactions.callbacks.mint(
            action=SurfaceCallbackAction.START_RUN,
            telegram_user_id=update.user_id,
            telegram_chat_id=update.chat_id,
            payload_sha256=payload_hash,
            automation_id=automation_id,
            run_id=preview.request.id,
        )
        formatted = "; ".join(f"{name}={value}" for name, value in sorted(values.items()))
        execution_message = (
            "The desktop agent will stage, verify, and complete the Atlas change automatically."
            if preview.request.id in self._auto_commit_run_ids
            else "The desktop agent will stage first and stop for a separate Commit approval."
        )
        return TelegramSurfaceResponse(
            text=(f"Ready to start `{manifest.name}` on `{target_app}`.\nInputs: {formatted}\n{execution_message}"),
            buttons=(TelegramButton(label="Start", callback_data=issued.callback_data),),
        )

    async def handle_callback(
        self,
        update: TelegramInboundUpdate,
        grant: SurfaceCallbackGrant,
    ) -> TelegramExecutionResult:
        """Consume and execute a non-disclosure Telegram action.

        Args:
            update: Authorized callback update.
            grant: Unconsumed callback binding returned by the vault.
        """
        try:
            if grant.action is SurfaceCallbackAction.APPROVE_AUTOMATION:
                return TelegramExecutionResult(self._approve_automation(update, grant))
            if grant.action is SurfaceCallbackAction.START_RUN:
                return TelegramExecutionResult(await self._start_run(update, grant), watch_run_id=grant.run_id)
            if grant.action is SurfaceCallbackAction.COMMIT_RUN:
                return TelegramExecutionResult(
                    await self._commit_run(update, grant),
                    watch_run_id=grant.run_id,
                    watch_terminal=True,
                )
            if grant.action is SurfaceCallbackAction.REJECT_RUN:
                return TelegramExecutionResult(
                    await self._reject_run(update, grant),
                    watch_run_id=grant.run_id,
                    watch_terminal=True,
                )
        except (TelegramCallbackRejectedError, ExecutionFault, KeyError, ValueError):
            return TelegramExecutionResult(TelegramSurfaceResponse(text="This button is invalid or unavailable."))
        return TelegramExecutionResult(TelegramSurfaceResponse(text="This button is invalid or unavailable."))

    async def wait_for_run(self, run_id: UUID) -> TelegramSurfaceResponse:
        """Wait for a staged change or terminal result and render the next controls.

        Args:
            run_id: Run started by this Telegram process.
        """
        coordinator = self._run_coordinators[run_id]
        while True:
            status = coordinator.get_status(run_id)
            state = str(status["state"])
            if state == RunState.AWAITING_COMMIT_APPROVAL.value:
                staged = coordinator.staged_change(run_id)
                if staged is None:
                    return TelegramSurfaceResponse(text="Run failed to produce a staged-change summary.")
                if run_id in self._auto_commit_run_ids:
                    await coordinator.approve_commit(
                        run_id,
                        staged.payload_sha256,
                        InvocationSource.TELEGRAM,
                        f"telegram:{self.interactions.config.owner_user_id}:start-authorized",
                    )
                    return await self.wait_for_terminal(run_id)
                owner = self.interactions.config.owner_user_id
                commit = self.interactions.callbacks.mint(
                    action=SurfaceCallbackAction.COMMIT_RUN,
                    telegram_user_id=owner,
                    telegram_chat_id=owner,
                    payload_sha256=staged.payload_sha256,
                    run_id=run_id,
                )
                reject = self.interactions.callbacks.mint(
                    action=SurfaceCallbackAction.REJECT_RUN,
                    telegram_user_id=owner,
                    telegram_chat_id=owner,
                    payload_sha256=staged.payload_sha256,
                    run_id=run_id,
                )
                changes = "; ".join(f"{item.field}: {item.before!r} → {item.after!r}" for item in staged.changes)
                return TelegramSurfaceResponse(
                    text=(
                        f"Staged on `{staged.target_app}` without commit.\n{changes}\n"
                        f"Visible check: {staged.visible_verification}"
                    ),
                    buttons=(
                        TelegramButton(label="Commit", callback_data=commit.callback_data),
                        TelegramButton(label="Reject", callback_data=reject.callback_data),
                    ),
                )
            if state in _TERMINAL_RUN_STATES:
                suppress_failure = state == RunState.FAILED.value and run_id in self._auto_commit_run_ids
                self._auto_commit_run_ids.discard(run_id)
                result = status.get("result")
                answer = result.get("answer") if isinstance(result, dict) else None
                return TelegramSurfaceResponse(
                    text=f"Run `{state}`. {answer or ''}".strip(),
                    silent=suppress_failure,
                )
            await asyncio.sleep(0.25)

    async def wait_for_terminal(self, run_id: UUID) -> TelegramSurfaceResponse:
        """Wait until a committed or rejected run reaches a terminal state.

        Args:
            run_id: Run whose decision was already submitted.
        """
        coordinator = self._run_coordinators[run_id]
        while True:
            status = coordinator.get_status(run_id)
            state = str(status["state"])
            if state in _TERMINAL_RUN_STATES:
                suppress_failure = state == RunState.FAILED.value and run_id in self._auto_commit_run_ids
                self._auto_commit_run_ids.discard(run_id)
                result = status.get("result")
                answer = result.get("answer") if isinstance(result, dict) else None
                return TelegramSurfaceResponse(
                    text=f"Run `{state}`. {answer or ''}".strip(),
                    silent=suppress_failure,
                )
            await asyncio.sleep(0.25)

    async def _coordinator(self, manifest: AutomationManifest) -> RunCoordinator:
        coordinator = self._coordinators.get(manifest.id)
        if coordinator is not None:
            return coordinator
        bundle_path = self.authoring.store.automation_root(manifest.id) / "approved_bundle.json"
        settings = self.execution_settings.model_copy(update={"bundle_path": bundle_path})
        coordinator = self._coordinator_factory(settings)
        await coordinator.startup()
        self._coordinators[manifest.id] = coordinator
        return coordinator

    def _approve_automation(
        self,
        update: TelegramInboundUpdate,
        grant: SurfaceCallbackGrant,
    ) -> TelegramSurfaceResponse:
        if grant.automation_id is None:
            raise ValueError("Approval callback has no automation")
        manifest = self.authoring.store.get_manifest(grant.automation_id)
        if manifest.current_version is None:
            raise ValueError("Automation has no version")
        version = self.authoring.load_version(manifest.id, manifest.current_version)
        payload_hash = _artifact_payload_hash(version)
        self._consume(update, grant, payload_hash)
        self.authoring.bundles.approve(
            manifest.id,
            version.version,
            actor=f"telegram:{update.user_id}",
            source=InvocationSource.TELEGRAM,
        )
        return TelegramSurfaceResponse(
            text=f"Approved `{manifest.name}` version {version.version}. Use /run {manifest.name}"
        )

    async def _start_run(self, update: TelegramInboundUpdate, grant: SurfaceCallbackGrant) -> TelegramSurfaceResponse:
        coordinator, run_id = self._bound_run(grant)
        status = coordinator.get_status(run_id)
        payload_hash = _run_request_hash(status["request"])
        self._consume(update, grant, payload_hash)
        await coordinator.confirm_start(run_id)
        message = (
            "Start approved. The desktop agent will stage, verify, and complete the Atlas change."
            if run_id in self._auto_commit_run_ids
            else "Start approved. The desktop agent is staging now; nothing will be committed."
        )
        return TelegramSurfaceResponse(text=message)

    async def _commit_run(self, update: TelegramInboundUpdate, grant: SurfaceCallbackGrant) -> TelegramSurfaceResponse:
        coordinator, run_id = self._bound_run(grant)
        staged = coordinator.staged_change(run_id)
        if staged is None:
            raise ValueError("Run has no staged change")
        self._consume(update, grant, staged.payload_sha256)
        await coordinator.approve_commit(
            run_id,
            staged.payload_sha256,
            InvocationSource.TELEGRAM,
            f"telegram:{update.user_id}",
        )
        return TelegramSurfaceResponse(text="Commit approved for the displayed staged hash.")

    async def _reject_run(self, update: TelegramInboundUpdate, grant: SurfaceCallbackGrant) -> TelegramSurfaceResponse:
        coordinator, run_id = self._bound_run(grant)
        staged = coordinator.staged_change(run_id)
        if staged is None:
            raise ValueError("Run has no staged change")
        self._consume(update, grant, staged.payload_sha256)
        await coordinator.reject_commit(run_id, InvocationSource.TELEGRAM, f"telegram:{update.user_id}")
        return TelegramSurfaceResponse(text="Rejected. The session is being cancelled without commit.")

    def _consume(self, update: TelegramInboundUpdate, grant: SurfaceCallbackGrant, payload_hash: str) -> None:
        callback_data = update.callback_data
        if callback_data is None:
            raise ValueError("Missing callback data")
        self.interactions.callbacks.consume(
            callback_data,
            telegram_user_id=update.user_id,
            telegram_chat_id=update.chat_id,
            expected_action=grant.action,
            expected_payload_sha256=payload_hash,
        )

    def _bound_run(self, grant: SurfaceCallbackGrant) -> tuple[RunCoordinator, UUID]:
        if grant.run_id is None:
            raise ValueError("Callback has no run")
        return self._run_coordinators[grant.run_id], grant.run_id

    def _auto_commit_enabled(self, target_app: str) -> bool:
        normalized_target = target_app.strip().casefold()
        return normalized_target in {
            configured_target.strip().casefold()
            for configured_target in self.execution_settings.telegram_auto_commit_targets
        }

    def _resolve_automation(self, selector: str) -> AutomationManifest:
        try:
            return self.authoring.store.get_manifest(UUID(selector))
        except (ValueError, KeyError):
            matches = [
                item for item in self.authoring.store.list_automations() if item.name.casefold() == selector.casefold()
            ]
        if len(matches) != 1:
            raise ValueError("Use an exact unique automation name or ID")
        return matches[0]


def _artifact_payload_hash(version: AutomationVersion) -> str:
    payload = [
        {"name": item.name, "path": item.relative_path, "sha256": item.sha256}
        for item in sorted(version.artifacts, key=lambda item: item.name)
    ]
    return _canonical_hash(payload)


def _run_request_hash(request: object) -> str:
    return _canonical_hash(request)


def _canonical_hash(payload: object) -> str:
    content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _parse_runtime_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in text.replace("\n", ";").split(";"):
        key, separator, value = item.partition("=")
        if not separator or not key.strip() or not value.strip():
            raise ValueError("Runtime inputs must use key=value pairs")
        values[key.strip()] = value.strip()
    return values
