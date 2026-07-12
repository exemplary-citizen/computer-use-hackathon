"""Run coordinator: the safety state machine around two-turn Holo execution.

Design decisions (from the reviewed plan):

- Transitions go through ``require_run_transition`` (shared contract) plus a
  compare-and-swap SQL update — a stale caller loses the race, never
  double-fires.
- One active Holo run, enforced by a partial unique index; a wedged run has
  an explicit ``force_release`` escape (no DB surgery).
- The blocking adapter runs via ``asyncio.to_thread`` only; cancel/status
  stay responsive during multi-minute turns; heartbeats are emitted while a
  turn is in flight.
- Structural stage guard: the host snapshots fixture persisted state before
  the stage turn and fails the run (``unsafe_stage``) if anything changed
  before approval — safety does not depend on the model behaving.
- The staged change is built by the host from the validated inputs; the
  agent's answer is cross-checked evidence, never the source of truth.
- Approval binds to the staged payload hash; late approvals get
  ``stale_approval``; a dead session fails closed (``stale_session``) —
  a replacement session is never created to click Save.
- Restart reconciliation: crash mid-``committing`` becomes terminal
  ``failed(commit_state_unknown)`` and refuses automatic retry.

State machine (terminal states on the right):

    prepared -> awaiting_start_confirmation -> executing -> awaiting_commit_approval
        -> committing -> succeeded | failed
    any nonterminal -> cancelled | failed
    committing is never skipped and never entered except by approval CAS.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from desktop_fixtures.store import AppKey, CrmState, load_state, state_path

from automation_foundry.contracts import (
    ApprovalDecision,
    ApprovalRecord,
    FieldChange,
    InvocationSource,
    RunEvent,
    RunPreview,
    RunRequest,
    RunResult,
    RunState,
    StagedChange,
)
from automation_foundry.contracts.transitions import require_run_transition
from automation_foundry.execution.bundles import (
    INPUT_FIELD_MAP,
    RECORD_SELECTOR_INPUT,
    LoadedBundle,
    execution_operation,
    load_verified_bundle,
    validate_inputs,
)
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.errors import ExecutionFault, fault
from automation_foundry.execution.events import EventLogConfig
from automation_foundry.execution.fixtures import ensure_fixture_running
from automation_foundry.execution.holo import HoloAdapter, HoloTaskSpec, ScriptedFakeHolo, TurnOutcome

_ACTIVE_STATES = (RunState.EXECUTING, RunState.AWAITING_COMMIT_APPROVAL, RunState.COMMITTING)
_TERMINAL_STATES = (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED)
_APP_ALIASES: dict[str, AppKey] = {
    "a": "a",
    "crm_a": "a",
    "crm a": "a",
    "northlight crm": "a",
    "b": "b",
    "crm_b": "b",
    "crm b": "b",
    "meridian contacts": "b",
}

AdapterFactory = Callable[[HoloTaskSpec], HoloAdapter]
FixtureLauncher = Callable[[AppKey, Path | None, float], bool]
Decision = Literal["approve", "reject", "cancel"]


class InputValidationError(ValueError):
    """Raised at prepare time; carries field-level error messages."""

    def __init__(self, field_errors: dict[str, str]):
        """Store per-field messages for the API's 422 response.

        Args:
            field_errors: Input name -> user-facing message.
        """
        super().__init__(f"invalid inputs: {sorted(field_errors)}")
        self.field_errors = field_errors


@dataclass
class _RunRuntime:
    """In-memory companion state for one run owned by this process."""

    decision: Decision | None = None
    approval: ApprovalRecord | None = None
    decision_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False
    commit_dispatched: bool = False
    adapter: HoloAdapter | None = None
    session_reference: str | None = None
    task: asyncio.Task[None] | None = None


class RunCoordinator:
    """Owns every run transition; the only writer of run state."""

    def __init__(
        self,
        settings: ExecutionSettings,
        adapter_factory: AdapterFactory | None = None,
        fixture_launcher: FixtureLauncher | None = None,
    ):
        """Initialize storage, the event log, and the boot identity.

        Args:
            settings: Execution-lane configuration.
            adapter_factory: Builds the Holo adapter per run; defaults to the
                scripted fake or live adapter per ``settings.holo_mode``.
            fixture_launcher: Ensures the selected bundled CRM is visible for live runs.
        """
        self.settings = settings
        self.events = EventLogConfig(runs_root=settings.runs_root).make()
        self.boot_id = uuid.uuid4().hex
        self._adapter_factory = adapter_factory or self._default_adapter_factory
        self._fixture_launcher = fixture_launcher or ensure_fixture_running
        self._runtimes: dict[UUID, _RunRuntime] = {}
        self._lock = asyncio.Lock()
        self._bundle: LoadedBundle | None = None
        settings.execution_database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    async def startup(self) -> None:
        """Reconcile runs left non-terminal by a previous process (SPEC §12)."""
        for run_id, state in self._non_terminal_rows():
            if state is RunState.COMMITTING:
                error = fault("commit_state_unknown")
            else:
                error = fault("interrupted_restart")
            await self._finalize(run_id, RunState.FAILED, error=error, from_states=(state,))

    async def prepare(
        self,
        target_app: str,
        inputs: dict[str, object],
        invocation_source: InvocationSource,
        max_steps: int | None = None,
        max_time_seconds: int | None = None,
    ) -> RunPreview:
        """Validate a run request against the verified bundle and stage a preview.

        Args:
            target_app: Requested application (aliases like "CRM A" accepted).
            inputs: Raw runtime inputs.
            invocation_source: Dashboard or voice.
            max_steps: Optional lower step budget.
            max_time_seconds: Optional lower wall-clock budget.

        Raises:
            InputValidationError: With field-level messages on invalid input.
            ExecutionFault: ``hash_mismatch`` when the bundle fails verification.
        """
        loaded = self._verified_bundle()
        field_errors = validate_inputs(loaded.bundle, inputs)
        try:
            operation = execution_operation(loaded.bundle)
        except ValueError as error:
            operation = None
            field_errors["inputs"] = str(error)
        app = _APP_ALIASES.get(target_app.strip().lower())
        if app is None:
            field_errors["target_app"] = "Unknown application; use CRM A or CRM B."
        record_name = str(inputs.get(RECORD_SELECTOR_INPUT, "")).strip()
        if operation == "create":
            for name in ("first_name", "last_name"):
                if not str(inputs.get(name, "")).strip():
                    field_errors[name] = "This field is required to create a contact."
        if operation == "update" and app is not None and record_name and RECORD_SELECTOR_INPUT not in field_errors:
            state = load_state(self._fixture_path(app))
            if not any(record.full_name.lower() == record_name.lower() for record in state.records):
                field_errors[RECORD_SELECTOR_INPUT] = f"No record named '{record_name}' in the target CRM."
        if field_errors:
            raise InputValidationError(field_errors)

        defaults = RunRequest.model_fields
        steps, seconds = self.settings.clamp_budgets(
            max_steps or int(defaults["max_steps"].default),
            max_time_seconds or int(defaults["max_time_seconds"].default),
        )
        request = RunRequest(
            id=uuid.uuid4(),
            automation_id=loaded.bundle.manifest.id,
            version=loaded.bundle.version.version,
            target_app=f"crm_{app}",
            inputs=dict(inputs),  # type: ignore[arg-type]
            invocation_source=invocation_source,
            max_steps=steps,
            max_time_seconds=seconds,
        )
        self._insert_run(request)
        self._write_run_file(request.id, "request.json", request.model_dump_json(indent=2))
        await self.events.append(request.id, RunState.PREPARED, "run_prepared", "Run prepared and inputs validated.")
        await self._transition(
            request.id,
            RunState.PREPARED,
            RunState.AWAITING_START_CONFIRMATION,
            "awaiting_start_confirmation",
            "Review the preview and confirm start.",
        )
        return RunPreview(
            request=request,
            automation_name=loaded.bundle.manifest.name,
            normalized_inputs=dict(inputs),  # type: ignore[arg-type]
            missing_fields=[],
            requires_confirmation=True,
        )

    async def confirm_start(self, run_id: UUID) -> None:
        """Record start confirmation and launch the execution task.

        Raises:
            ExecutionFault: ``run_conflict`` when another run is active, or a
                transition conflict when the run is not awaiting confirmation.
        """
        async with self._lock:
            try:
                await self._transition(
                    run_id,
                    RunState.AWAITING_START_CONFIRMATION,
                    RunState.EXECUTING,
                    "start_confirmed",
                    "Start confirmed; Holo is taking over the desktop.",
                    activate=True,
                )
            except sqlite3.IntegrityError as error:
                raise fault("run_conflict") from error
            runtime = self._runtimes.setdefault(run_id, _RunRuntime())
            runtime.task = asyncio.create_task(self._execute(run_id))

    async def approve_commit(self, run_id: UUID, payload_sha256: str, source: InvocationSource, actor: str) -> None:
        """Record explicit commit approval bound to the staged payload hash.

        Raises:
            ExecutionFault: ``stale_approval`` after timeout/terminal state,
                ``approval_hash_mismatch`` when the hash does not match.
        """
        state = self._current_state(run_id)
        if state in _TERMINAL_STATES:
            raise fault("stale_approval")
        if state is RunState.COMMITTING:
            return  # idempotent: an approval already landed
        if state is not RunState.AWAITING_COMMIT_APPROVAL:
            raise fault("approval_hash_mismatch", f"run is {state.value}, not awaiting approval")
        staged = self.staged_change(run_id)
        if staged is None or staged.payload_sha256 != payload_sha256:
            raise fault("approval_hash_mismatch")
        runtime = self._runtimes.get(run_id)
        if runtime is None:
            raise fault("stale_approval", "run is not owned by this process")
        if runtime.decision is not None:
            return  # idempotent double-approve
        runtime.approval = ApprovalRecord(
            id=uuid.uuid4(),
            decision=ApprovalDecision.APPROVED,
            source=source,
            payload_sha256=payload_sha256,
            actor=actor,
        )
        runtime.decision = "approve"
        runtime.decision_event.set()

    async def reject_commit(self, run_id: UUID, source: InvocationSource, actor: str) -> None:
        """Record commit rejection; the run cancels without saving."""
        state = self._current_state(run_id)
        if state is not RunState.AWAITING_COMMIT_APPROVAL:
            raise fault("stale_approval", f"run is {state.value}, not awaiting approval")
        runtime = self._runtimes.get(run_id)
        if runtime is None or runtime.decision is not None:
            return
        staged = self.staged_change(run_id)
        runtime.approval = ApprovalRecord(
            id=uuid.uuid4(),
            decision=ApprovalDecision.REJECTED,
            source=source,
            payload_sha256=staged.payload_sha256 if staged is not None else "0" * 64,
            actor=actor,
        )
        runtime.decision = "reject"
        runtime.decision_event.set()

    async def cancel(self, run_id: UUID, reason: str = "Cancelled by user.") -> None:
        """Cancel from any nonterminal state; never relabels a dispatched commit."""
        state = self._current_state(run_id)
        if state in _TERMINAL_STATES:
            return
        runtime = self._runtimes.get(run_id)
        if state in (RunState.PREPARED, RunState.AWAITING_START_CONFIRMATION):
            await self._finalize(run_id, RunState.CANCELLED, answer=reason, from_states=(state,))
            return
        if runtime is None:
            await self._finalize(run_id, RunState.CANCELLED, answer=reason, from_states=(state,))
            return
        runtime.cancel_requested = True
        if runtime.adapter is not None and runtime.session_reference is not None and not runtime.commit_dispatched:
            await asyncio.to_thread(runtime.adapter.cancel, runtime.session_reference)
        if runtime.decision is None:
            runtime.decision = "cancel"
            runtime.decision_event.set()

    async def force_release(self) -> list[UUID]:
        """Admin escape hatch: fail every active run and free the single-run slot."""
        released: list[UUID] = []
        for run_id, state in self._non_terminal_rows():
            if state in _ACTIVE_STATES:
                error = fault("commit_state_unknown") if state is RunState.COMMITTING else fault("interrupted_restart")
                await self._finalize(run_id, RunState.FAILED, error=error, from_states=(state,))
                released.append(run_id)
        return released

    def list_runs(self) -> list[dict[str, object]]:
        """Return run summaries, newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, state, request_json, created_at, updated_at FROM runs ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "id": row[0],
                "state": row[1],
                "request": json.loads(row[2]),
                "created_at": row[3],
                "updated_at": row[4],
            }
            for row in rows
        ]

    def get_status(self, run_id: UUID) -> dict[str, object]:
        """Return one run's state, staged change, result, and last sequence.

        Raises:
            KeyError: If the run does not exist.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, request_json, staged_json, result_json FROM runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        events = self.events.replay(run_id)
        return {
            "id": str(run_id),
            "state": row[0],
            "request": json.loads(row[1]),
            "staged_change": json.loads(row[2]) if row[2] else None,
            "result": json.loads(row[3]) if row[3] else None,
            "last_sequence": events[-1].sequence if events else -1,
            "approval_timeout_seconds": self.settings.approval_timeout_seconds,
        }

    def staged_change(self, run_id: UUID) -> StagedChange | None:
        """Load the staged change persisted for a run, if any."""
        with self._connect() as connection:
            row = connection.execute("SELECT staged_json FROM runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None or not row[0]:
            return None
        return StagedChange.model_validate_json(row[0])

    def automation_metadata(self) -> dict[str, object]:
        """Return the approved automation and its runtime input definitions."""
        loaded = self._verified_bundle()
        return {
            "name": loaded.bundle.manifest.name,
            "version": loaded.bundle.version.version,
            "operation": execution_operation(loaded.bundle),
            "inputs": [definition.model_dump(mode="json") for definition in loaded.bundle.version.inputs],
            "input_schema": loaded.bundle.input_schema,
        }

    async def _execute(self, run_id: UUID) -> None:
        runtime = self._runtimes[run_id]
        request = self._request(run_id)
        app = _APP_ALIASES[request.target_app.lower()]
        try:
            loaded = load_verified_bundle(self.settings.bundle_path)  # re-verify immediately pre-session (N8)
            spec = self._task_spec(loaded, request, app)
            pre_state = load_state(self._fixture_path(app))
            if self.settings.holo_mode == "live" and self.settings.launch_fixture_on_run:
                launched = await asyncio.to_thread(
                    self._fixture_launcher,
                    app,
                    self.settings.fixture_data_root,
                    self.settings.fixture_launch_wait_seconds,
                )
                await self.events.append(
                    run_id,
                    RunState.EXECUTING,
                    "target_app_ready",
                    f"CRM {app.upper()} {'launched' if launched else 'is already running'} for live execution.",
                    {"target_app": f"crm_{app}", "launched": launched},
                )
            runtime.adapter = self._adapter_factory(spec)
            runtime.session_reference = await asyncio.to_thread(runtime.adapter.start_session)
            await self.events.append(
                run_id,
                RunState.EXECUTING,
                "session_started",
                "Holo session started.",
                {"session_reference": runtime.session_reference},
            )
            stage_outcome = await self._with_heartbeat(
                run_id,
                RunState.EXECUTING,
                asyncio.to_thread(runtime.adapter.send_message, runtime.session_reference, self._stage_prompt(spec)),
            )
            if runtime.cancel_requested:
                await self._finalize(run_id, RunState.CANCELLED, answer="Cancelled during staging; nothing saved.")
                return
            self._check_stage_answer(spec, stage_outcome)
            post_stage = load_state(self._fixture_path(app))
            if post_stage != pre_state:
                await asyncio.to_thread(runtime.adapter.cancel, runtime.session_reference)
                raise fault("unsafe_stage")
            staged = self._build_staged_change(run_id, spec, pre_state, runtime.session_reference, stage_outcome)
            self._store_staged(run_id, staged)
            await self._transition(
                run_id,
                RunState.EXECUTING,
                RunState.AWAITING_COMMIT_APPROVAL,
                "staged_change",
                "Change staged; explicit approval required before anything is saved.",
                payload={
                    "staged_change": json.loads(staged.model_dump_json()),
                    "approval_timeout_seconds": self.settings.approval_timeout_seconds,
                },
            )
            decision = await self._await_decision(runtime)
            if decision is None:
                await asyncio.to_thread(runtime.adapter.cancel, runtime.session_reference)
                await self._finalize(
                    run_id,
                    RunState.CANCELLED,
                    answer="Approval window expired; nothing was saved.",
                    payload=fault("stale_approval").payload(),
                )
                return
            if decision != "approve":
                await asyncio.to_thread(runtime.adapter.cancel, runtime.session_reference)
                code = "commit_rejected" if decision == "reject" else "cancelled_by_user"
                self._store_approval(run_id, runtime.approval)
                await self._finalize(
                    run_id,
                    RunState.CANCELLED,
                    answer=fault(code).spec.message,
                    payload=fault(code).payload(),
                )
                return
            alive = await asyncio.to_thread(runtime.adapter.is_alive, runtime.session_reference)
            if not alive:
                await self._finalize(
                    run_id,
                    RunState.CANCELLED,
                    answer="The Holo session was lost while waiting; nothing was saved.",
                    payload=fault("stale_session").payload(),
                )
                return
            self._store_approval(run_id, runtime.approval)
            await self._transition(
                run_id,
                RunState.AWAITING_COMMIT_APPROVAL,
                RunState.COMMITTING,
                "commit_approved",
                "Approval recorded; committing through the same Holo session.",
            )
            runtime.commit_dispatched = True
            commit_outcome = await self._with_heartbeat(
                run_id,
                RunState.COMMITTING,
                asyncio.to_thread(runtime.adapter.send_message, runtime.session_reference, self._commit_prompt(spec)),
            )
            verification = self._verify_commit(spec, pre_state, staged)
            await self._finalize(
                run_id,
                RunState.SUCCEEDED,
                answer=commit_outcome.answer,
                verification=verification,
                steps=stage_outcome.steps_used + commit_outcome.steps_used,
            )
        except ExecutionFault as error:
            await self._finalize(run_id, RunState.FAILED, error=error)
        except Exception as error:  # a crashed worker must still finalize the run
            await self._finalize(run_id, RunState.FAILED, error=fault("internal_error", repr(error)))

    async def _await_decision(self, runtime: _RunRuntime) -> Decision | None:
        try:
            await asyncio.wait_for(runtime.decision_event.wait(), timeout=self.settings.approval_timeout_seconds)
        except TimeoutError:
            if runtime.decision is None:
                return None
        return runtime.decision

    async def _with_heartbeat(
        self, run_id: UUID, state: RunState, awaitable: Coroutine[object, object, TurnOutcome]
    ) -> TurnOutcome:
        task: asyncio.Task[TurnOutcome] = asyncio.ensure_future(awaitable)
        while True:
            done, _ = await asyncio.wait({task}, timeout=self.settings.heartbeat_seconds)
            if done:
                return task.result()
            await self.events.append(run_id, state, "heartbeat", "Still working; the session is active.")

    def _check_stage_answer(self, spec: HoloTaskSpec, outcome: TurnOutcome) -> None:
        try:
            parsed = json.loads(outcome.answer)
        except json.JSONDecodeError as error:
            raise fault("malformed_stage_answer", "stage answer was not structured") from error
        if not isinstance(parsed, dict) or "staged_fields" not in parsed or "record" not in parsed:
            raise fault("malformed_stage_answer", "stage answer missing record/staged_fields")
        if parsed["record"] != spec.record_name:
            raise fault("malformed_stage_answer", "agent-reported record differs from the requested record")
        if parsed["staged_fields"] != spec.field_changes:
            raise fault("malformed_stage_answer", "agent-reported fields differ from the requested change")

    def _build_staged_change(
        self,
        run_id: UUID,
        spec: HoloTaskSpec,
        pre_state: CrmState,
        session_reference: str,
        outcome: TurnOutcome,
    ) -> StagedChange:
        if spec.operation == "create":
            changes = [
                FieldChange(field=field_name, before=None, after=after)
                for field_name, after in sorted(spec.field_changes.items())
            ]
        else:
            record = next(item for item in pre_state.records if item.full_name == spec.record_name)
            changes = [
                FieldChange(field=field_name, before=getattr(record, field_name), after=after)
                for field_name, after in sorted(spec.field_changes.items())
            ]
        payload = [{"field": change.field, "before": change.before, "after": change.after} for change in changes]
        digest = hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
        verification = ""
        try:
            verification = str(json.loads(outcome.answer).get("visible_verification", ""))
        except json.JSONDecodeError:  # already rejected by _check_stage_answer
            pass
        return StagedChange(
            run_id=run_id,
            target_app=f"crm_{spec.app}",
            record_identity=spec.record_name,
            changes=changes,
            visible_verification=verification or "Agent reported the form shows the staged values.",
            session_reference=session_reference,
            payload_sha256=digest,
        )

    def _verify_commit(self, spec: HoloTaskSpec, pre_state: CrmState, staged: StagedChange) -> str:
        post_state = load_state(self._fixture_path(spec.app))
        if spec.operation == "create":
            if post_state.records[: len(pre_state.records)] != pre_state.records:
                raise fault("commit_verify_failed", "an existing record changed while creating a contact")
            created_records = post_state.records[len(pre_state.records) :]
            if len(created_records) != 1:
                raise fault("commit_verify_failed", "contact creation did not add exactly one record")
            created = created_records[0]
            if any(getattr(created, field) != value for field, value in spec.field_changes.items()):
                raise fault("commit_verify_failed", "the created contact does not match the approved values")
            summary = ", ".join(f"{change.field}: {change.after!r}" for change in staged.changes)
            return f"Persisted state contains exactly one approved new contact ({summary})."
        expected_records = []
        for record in pre_state.records:
            if record.full_name == spec.record_name:
                expected_records.append(record.model_copy(update=dict(spec.field_changes)))
            else:
                expected_records.append(record)
        expected = pre_state.model_copy(update={"records": expected_records})
        if post_state.model_dump() != expected.model_dump():
            raise fault("commit_verify_failed")
        summary = ", ".join(f"{change.field}: {change.before!r} -> {change.after!r}" for change in staged.changes)
        return f"Persisted state matches the approved change exactly ({summary})."

    def _task_spec(self, loaded: LoadedBundle, request: RunRequest, app: AppKey) -> HoloTaskSpec:
        operation = execution_operation(loaded.bundle)
        if operation == "create":
            record_name = f"{request.inputs['first_name']} {request.inputs['last_name']}"
        else:
            record_name = str(request.inputs[RECORD_SELECTOR_INPUT])
        field_changes = {
            INPUT_FIELD_MAP[name]: str(value)
            for name, value in request.inputs.items()
            if name in INPUT_FIELD_MAP and str(value).strip()
        }
        task_text = self._stage_prompt_text(loaded, request, operation, record_name, field_changes)
        return HoloTaskSpec(
            app=app,
            record_name=record_name,
            field_changes=field_changes,
            skill_markdown=loaded.bundle.skill_markdown,
            task_text=task_text,
            max_steps=request.max_steps,
            max_time_seconds=request.max_time_seconds,
            operation=operation,
        )

    def _stage_prompt_text(
        self,
        loaded: LoadedBundle,
        request: RunRequest,
        operation: Literal["update", "create"],
        record_name: str,
        field_changes: dict[str, str],
    ) -> str:
        changes = "; ".join(f"{name} -> {value}" for name, value in sorted(field_changes.items()))
        action = "Create a new contact record" if operation == "create" else f"Update record {record_name}"
        return (
            f"{loaded.bundle.skill_markdown}\n\n"
            f"Target application: {request.target_app}. {action}. Requested values: {changes}."
        )

    def _stage_prompt(self, spec: HoloTaskSpec) -> str:
        interaction = (
            "open the Add Record form and fill the requested values"
            if spec.operation == "create"
            else "find the requested record and fill the requested values in its form"
        )
        return (
            f"{spec.task_text}\n\nTURN 1 OF 2 — STAGE ONLY: {interaction}, visually "
            "verify them, then END YOUR TURN. Do NOT press Save, Commit, Submit, or any equivalent. "
            'Answer with JSON: {"record": ..., "staged_fields": {...}, "visible_verification": ...}.'
        )

    def _commit_prompt(self, spec: HoloTaskSpec) -> str:
        persistent_control = "Add Record" if spec.operation == "create" else "Save/Commit"
        return (
            "TURN 2 OF 2 — COMMIT: the staged change has been approved. Re-check the staged values are still "
            f"visible, press the {persistent_control} control once, and verify the application shows success."
        )

    def _default_adapter_factory(self, spec: HoloTaskSpec) -> HoloAdapter:
        if self.settings.holo_mode == "live":
            from automation_foundry.execution.holo import LiveHoloAdapter

            return LiveHoloAdapter(spec)
        return ScriptedFakeHolo(
            spec=spec, script=self.settings.holo_mock_script, data_root=self.settings.fixture_data_root
        )

    def _verified_bundle(self) -> LoadedBundle:
        self._bundle = load_verified_bundle(self.settings.bundle_path)
        return self._bundle

    def _fixture_path(self, app: AppKey) -> Path:
        return state_path(app, self.settings.fixture_data_root)

    async def _transition(
        self,
        run_id: UUID,
        from_state: RunState,
        to_state: RunState,
        event_type: str,
        message: str,
        payload: Mapping[str, object] | None = None,
        activate: bool = False,
    ) -> RunEvent:
        require_run_transition(from_state, to_state)
        is_active = 1 if (activate or to_state in _ACTIVE_STATES) and to_state not in _TERMINAL_STATES else None
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET state = ?, is_active = ?, updated_at = ? WHERE id = ? AND state = ?",
                (to_state.value, is_active, _now(), str(run_id), from_state.value),
            )
            if cursor.rowcount != 1:
                raise fault(
                    "approval_hash_mismatch" if to_state is RunState.COMMITTING else "run_conflict",
                    f"stale transition {from_state.value} -> {to_state.value}",
                )
        return await self.events.append(run_id, to_state, event_type, message, payload)

    async def _finalize(
        self,
        run_id: UUID,
        to_state: RunState,
        error: ExecutionFault | None = None,
        answer: str | None = None,
        verification: str | None = None,
        steps: int = 0,
        payload: Mapping[str, object] | None = None,
        from_states: tuple[RunState, ...] | None = None,
    ) -> None:
        current = self._current_state(run_id)
        if current in _TERMINAL_STATES:
            return
        if from_states is not None and current not in from_states:
            return
        try:
            require_run_transition(current, to_state)
        except ValueError:
            return
        request = self._request(run_id)
        result = RunResult(
            run_id=run_id,
            state=to_state,
            answer=answer or (error.spec.message if error else None),
            verification_summary=verification,
            error_code=error.spec.code if error and to_state is RunState.FAILED else None,
            error_message=str(error) if error else None,
            started_at=request.created_at,
            holo_steps=steps,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET state = ?, is_active = NULL, result_json = ?, updated_at = ? WHERE id = ? AND state = ?",
                (to_state.value, result.model_dump_json(), _now(), str(run_id), current.value),
            )
            if cursor.rowcount != 1:
                return  # another finalizer won the race
        self._write_run_file(run_id, "result.json", result.model_dump_json(indent=2))
        event_payload: dict[str, object] = dict(payload or {})
        if error is not None:
            event_payload.update(error.payload())
        await self.events.append(
            run_id,
            to_state,
            "run_completed",
            result.answer or f"Run ended: {to_state.value}.",
            event_payload,
        )

    def _current_state(self, run_id: UUID) -> RunState:
        with self._connect() as connection:
            row = connection.execute("SELECT state FROM runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return RunState(row[0])

    def _request(self, run_id: UUID) -> RunRequest:
        with self._connect() as connection:
            row = connection.execute("SELECT request_json FROM runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return RunRequest.model_validate_json(row[0])

    def _insert_run(self, request: RunRequest) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs (id, state, is_active, request_json, boot_id, created_at, updated_at)"
                " VALUES (?, ?, NULL, ?, ?, ?, ?)",
                (str(request.id), RunState.PREPARED.value, request.model_dump_json(), self.boot_id, _now(), _now()),
            )

    def _store_staged(self, run_id: UUID, staged: StagedChange) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET staged_json = ?, updated_at = ? WHERE id = ?",
                (staged.model_dump_json(), _now(), str(run_id)),
            )
        self._write_run_file(run_id, "staged_change.json", staged.model_dump_json(indent=2))

    def _store_approval(self, run_id: UUID, approval: ApprovalRecord | None) -> None:
        if approval is not None:
            self._write_run_file(run_id, "approval.json", approval.model_dump_json(indent=2))

    def _write_run_file(self, run_id: UUID, name: str, content: str) -> None:
        path = self.settings.runs_root / str(run_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")

    def _non_terminal_rows(self) -> list[tuple[UUID, RunState]]:
        terminal = tuple(state.value for state in _TERMINAL_STATES)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT id, state FROM runs WHERE state NOT IN ({','.join('?' * len(terminal))})", terminal
            ).fetchall()
        return [(UUID(row[0]), RunState(row[1])) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.settings.execution_database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    is_active INTEGER,
                    request_json TEXT NOT NULL,
                    staged_json TEXT,
                    result_json TEXT,
                    boot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_one_active ON runs (is_active) WHERE is_active = 1"
            )


def _now() -> str:
    return datetime.now(UTC).isoformat()
