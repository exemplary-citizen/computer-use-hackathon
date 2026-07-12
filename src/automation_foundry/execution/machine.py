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
from typing import Literal, cast
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
    load_verified_bundle,
    validate_inputs,
)
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.errors import ExecutionFault, fault
from automation_foundry.execution.events import EventLogConfig
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

    def __init__(self, settings: ExecutionSettings, adapter_factory: AdapterFactory | None = None):
        """Initialize storage, the event log, and the boot identity.

        Args:
            settings: Execution-lane configuration.
            adapter_factory: Builds the Holo adapter per run; defaults to the
                scripted fake or live adapter per ``settings.holo_mode``.
        """
        self.settings = settings
        self.events = EventLogConfig(runs_root=settings.runs_root).make()
        self.boot_id = uuid.uuid4().hex
        self._adapter_factory = adapter_factory or self._default_adapter_factory
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
        fixture_bundle = _uses_fixture_contract(loaded)
        app = _APP_ALIASES.get(target_app.strip().lower()) if fixture_bundle else None
        if fixture_bundle and app is None:
            field_errors["target_app"] = "Unknown application; use CRM A or CRM B."
        record_name = str(inputs.get(RECORD_SELECTOR_INPUT, "")).strip()
        if fixture_bundle and app is not None and record_name and RECORD_SELECTOR_INPUT not in field_errors:
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
            target_app=f"crm_{app}" if fixture_bundle else target_app.strip(),
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
        task = runtime.task
        if task is not None and task is not asyncio.current_task():
            await task

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

    async def _execute(self, run_id: UUID) -> None:
        runtime = self._runtimes[run_id]
        if runtime.cancel_requested:
            await self._finalize(
                run_id,
                RunState.CANCELLED,
                answer="Cancelled before Holo started; nothing saved.",
                from_states=(RunState.EXECUTING,),
            )
            return
        request = self._request(run_id)
        try:
            loaded = load_verified_bundle(self.settings.bundle_path)  # re-verify immediately pre-session (N8)
            fixture_bundle = _uses_fixture_contract(loaded)
            app = _APP_ALIASES.get(request.target_app.lower()) if fixture_bundle else None
            if fixture_bundle and app is None:
                raise fault("wrong_app_state", f"unsupported fixture app: {request.target_app}")
            spec = self._task_spec(loaded, request, app)
            pre_state = load_state(self._fixture_path(app)) if app is not None else None
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
            post_stage = load_state(self._fixture_path(app)) if app is not None else None
            if pre_state is not None and post_stage != pre_state:
                await asyncio.to_thread(runtime.adapter.cancel, runtime.session_reference)
                raise fault("unsafe_stage")
            staged = self._build_staged_change(run_id, spec, pre_state, runtime.session_reference, stage_outcome)
            self._store_staged(run_id, staged)
            if _is_no_matching_email(staged):
                await self._finalize(
                    run_id,
                    RunState.SUCCEEDED,
                    answer="No return email is present in the three-message Inbox snapshot.",
                    verification=staged.visible_verification,
                    steps=stage_outcome.steps_used,
                    from_states=(RunState.EXECUTING,),
                )
                return
            if not spec.requires_commit:
                await self._finalize(
                    run_id,
                    RunState.SUCCEEDED,
                    answer=f"Completed non-persistent workflow in {staged.target_app}; no commit approval was required.",
                    verification=staged.visible_verification,
                    steps=stage_outcome.steps_used,
                    from_states=(RunState.EXECUTING,),
                )
                return
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
                asyncio.to_thread(
                    runtime.adapter.send_message,
                    runtime.session_reference,
                    self._commit_prompt(spec, staged),
                ),
            )
            verification = self._verify_commit(spec, pre_state, staged, commit_outcome)
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
        finally:
            if runtime.adapter is not None and runtime.session_reference is not None:
                try:
                    await asyncio.to_thread(runtime.adapter.cancel, runtime.session_reference)
                except Exception:
                    pass

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
            parsed = _parse_json_object(outcome.answer)
        except ValueError as error:
            if spec.field_changes and _prose_matches_stage(spec, outcome.answer):
                return
            raise fault("malformed_stage_answer", "stage report did not contain the exact requested values") from error
        if "staged_fields" not in parsed or "record" not in parsed:
            if spec.field_changes and _prose_matches_stage(spec, outcome.answer):
                return
            raise fault("malformed_stage_answer", "stage answer missing record/staged_fields")
        if not isinstance(parsed["record"], str) or not parsed["record"].strip():
            raise fault("malformed_stage_answer", "stage answer has no record identity")
        staged_fields = parsed["staged_fields"]
        if not isinstance(staged_fields, dict) or not staged_fields:
            if spec.field_changes:
                raise fault("malformed_stage_answer", "agent-reported fields differ from the requested change")
            raise fault("malformed_stage_answer", "dynamic workflow reported no staged fields")
        if not all(isinstance(name, str) and name and isinstance(value, str) for name, value in staged_fields.items()):
            raise fault("malformed_stage_answer", "staged fields must contain named string values")
        if spec.field_changes and staged_fields != spec.field_changes:
            raise fault("malformed_stage_answer", "agent-reported fields differ from the requested change")
        if spec.app.strip().casefold() == "atlas returns desk":
            _validate_atlas_stage_report(parsed["record"], staged_fields)

    def _build_staged_change(
        self,
        run_id: UUID,
        spec: HoloTaskSpec,
        pre_state: CrmState | None,
        session_reference: str,
        outcome: TurnOutcome,
    ) -> StagedChange:
        reported_fields = spec.field_changes
        reported_record = spec.record_name
        if not reported_fields:
            parsed = _parse_json_object(outcome.answer)
            reported_fields = {str(name): str(value) for name, value in parsed["staged_fields"].items()}
            reported_record = str(parsed["record"]).strip()
        if pre_state is None:
            changes = [
                FieldChange(field=field_name, before=None, after=after)
                for field_name, after in sorted(reported_fields.items())
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
            verification = str(_parse_json_object(outcome.answer).get("visible_verification", ""))
        except ValueError:  # already rejected by _check_stage_answer
            verification = outcome.answer[:4_000]
        return StagedChange(
            run_id=run_id,
            target_app=f"crm_{spec.app}" if spec.app in ("a", "b") else spec.app,
            record_identity=reported_record,
            changes=changes,
            visible_verification=verification or "Agent reported the form shows the staged values.",
            session_reference=session_reference,
            payload_sha256=digest,
        )

    def _verify_commit(
        self,
        spec: HoloTaskSpec,
        pre_state: CrmState | None,
        staged: StagedChange,
        outcome: TurnOutcome,
    ) -> str:
        if pre_state is None:
            return f"Live session reported visible completion: {outcome.answer}"
        if spec.app not in ("a", "b"):
            raise fault("commit_verify_failed", f"unsupported fixture app: {spec.app}")
        post_state = load_state(self._fixture_path(cast(AppKey, spec.app)))
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

    def _task_spec(self, loaded: LoadedBundle, request: RunRequest, app: AppKey | None) -> HoloTaskSpec:
        fixture_bundle = app is not None
        record_name = str(request.inputs[RECORD_SELECTOR_INPUT]) if fixture_bundle else request.target_app
        field_changes = (
            {INPUT_FIELD_MAP[name]: str(value) for name, value in request.inputs.items() if name in INPUT_FIELD_MAP}
            if fixture_bundle
            else {name: str(value) for name, value in request.inputs.items()}
        )
        persistent_index = next(
            (index for index, step in enumerate(loaded.bundle.version.steps) if step.persistent_action),
            len(loaded.bundle.version.steps),
        )
        stage_instructions = tuple(step.instruction for step in loaded.bundle.version.steps[:persistent_index])
        commit_instructions = tuple(step.instruction for step in loaded.bundle.version.steps[persistent_index:])
        task_text = self._stage_prompt_text(loaded, request, record_name, field_changes)
        return HoloTaskSpec(
            app=app or request.target_app,
            record_name=record_name,
            field_changes=field_changes,
            skill_markdown=loaded.bundle.skill_markdown,
            task_text=task_text,
            max_steps=request.max_steps,
            max_time_seconds=request.max_time_seconds,
            region=self.settings.holo_region,
            stage_instructions=stage_instructions,
            commit_instructions=commit_instructions,
            requires_commit=fixture_bundle or bool(commit_instructions),
        )

    def _stage_prompt_text(
        self, loaded: LoadedBundle, request: RunRequest, record_name: str, field_changes: dict[str, str]
    ) -> str:
        changes = "; ".join(f"{name} -> {value}" for name, value in sorted(field_changes.items()))
        return (
            f"{loaded.bundle.skill_markdown}\n\n"
            f"Target application: {request.target_app}. Record/context: {record_name}. Runtime inputs: {changes}."
        )

    def _stage_prompt(self, spec: HoloTaskSpec) -> str:
        staged_fields_instruction = (
            f"`staged_fields` exactly equal to {json.dumps(spec.field_changes, sort_keys=True)}"
            if spec.field_changes
            else "`staged_fields` containing every exact field/value derived and staged during this workflow; it must "
            "contain at least one field"
        )
        if spec.stage_instructions and not spec.requires_commit:
            stage_steps = "\n".join(f"- {instruction}" for instruction in spec.stage_instructions)
            return (
                f"{spec.task_text}\n\nNON-PERSISTENT WORKFLOW — execute and visually verify all steps:\n"
                f"{stage_steps}\n\nNo Save, Commit, Submit, Send, or other persistent action is part of this workflow. "
                "After completing every step, call `request_commit_approval` exactly once as a structured completion "
                f"report with `record` equal to {json.dumps(spec.record_name)}, {staged_fields_instruction}, and "
                "`visible_verification` describing the observed "
                "final state. Do not interact further while waiting for the tool result."
            )
        if spec.stage_instructions or spec.commit_instructions:
            stage_steps = (
                "\n".join(f"- {instruction}" for instruction in spec.stage_instructions) or "- Prepare the app."
            )
            atlas_workflow = spec.app.strip().casefold() == "atlas returns desk"
            if atlas_workflow:
                stage_steps += (
                    "\n\nATLAS THREE-MESSAGE LOOP: Ignore every prior run, processed-case history, and the message "
                    "currently selected in Mail. Activate Apple Mail Inbox and take one immutable, ordered snapshot "
                    "of exactly the three newest messages, newest to oldest. Inspect all three snapshot subjects. "
                    "Build `case_queue` from every snapshot subject containing an `RTN-####` case ID, preserving that "
                    "exact newest-to-oldest order. Skip regular mail, deduplicate repeated case IDs only within this "
                    "snapshot, and never inspect a fourth message. If `case_queue` is empty, do not open Atlas. Call "
                    "`request_commit_approval` with record `NO_MATCHING_EMAIL`, "
                    'staged_fields exactly {"workflow_status":"no_matching_email"}, and visible verification '
                    "that none of the three snapshot subjects contains a return case. Otherwise, read the first "
                    "queued message's customer context and stage only that first case in Atlas. Clear the search "
                    "field, enter its exact case ID, click `Run Search`, wait for the filtered result, select the exact "
                    "matching row, visually confirm the case heading, read the Return Intake Narrative, then enter "
                    "and visually verify the internal decision note. Do not call the approval tool before all of "
                    "those checkpoints are complete. The approval report's `staged_fields` must contain `case_queue` "
                    "as one comma-separated string of every queued ID, `case_id` as the first queued ID, and "
                    "`internal_decision_note` as the exact staged note."
                )
            blocked_steps = "\n".join(f"- {instruction}" for instruction in spec.commit_instructions)
            record_instruction = (
                "`record` equal to the first queued `case_id` (or `NO_MATCHING_EMAIL` for an empty queue)"
                if atlas_workflow
                else f"`record` equal to {json.dumps(spec.record_name)}"
            )
            return (
                f"{spec.task_text}\n\nTURN 1 OF 2 — STAGE ONLY. Execute only these non-persistent setup steps:\n"
                f"{stage_steps}\n\nDo not execute these approval-gated steps yet:\n{blocked_steps}\n"
                "Do not Save, Commit, Submit, type approval-gated content, or perform any equivalent persistent action. "
                "Visually verify the app is ready, then call `request_commit_approval` exactly once with "
                f"{record_instruction}, {staged_fields_instruction}, and "
                "`visible_verification` describing readiness. "
                "Do not answer or end the session; wait for the approval tool result."
            )
        return (
            f"{spec.task_text}\n\nTURN 1 OF 2 — STAGE ONLY: fill the requested values in the form, visually "
            "verify them. Do NOT press Save, Commit, Submit, or any equivalent. Call `request_commit_approval` exactly "
            f"once with `record`, {staged_fields_instruction}, and `visible_verification`. Do not answer or end the session; "
            "wait for the approval tool result."
        )

    def _commit_prompt(self, spec: HoloTaskSpec, staged: StagedChange) -> str:
        target_guard = (
            f"The approval was clicked outside the target app, so the current foreground window is untrusted. Before "
            f"any data-entry keystroke or persistent action, explicitly activate {json.dumps(spec.app)} and visually "
            f"verify the expected {json.dumps(spec.record_name)} context. If it cannot be verified, stop without typing "
            "or committing and report failure. Never type workflow content into the approval surface. "
        )
        approved_fields = {change.field: change.after for change in staged.changes}
        if spec.app.strip().casefold() == "atlas returns desk":
            case_queue = str(approved_fields.get("case_queue", approved_fields.get("case_id", "")))
            return (
                "TURN 2 OF 2 — START-AUTHORIZED ATLAS QUEUE COMMIT. This one retained turn must finish the immutable "
                f"queue captured in Turn 1, exactly in this order: {json.dumps(case_queue)}. Do not rebuild, reorder, "
                "or extend that queue; ignore prior-run history, never inspect a fourth Inbox message, and never "
                "process one queued ID twice. "
                f"{target_guard}The first queued case is already staged. Reactivate Atlas Returns Desk, verify the "
                "visible case heading equals the first queued ID and the internal note is staged, click the green "
                "`Apply Resolution` button exactly once, and wait until red `UPDATED!` appears directly beneath it. "
                "For each remaining queued ID, in order: activate Apple Mail and select that exact queued snapshot "
                "message; read its customer context; reactivate Atlas Returns Desk and wait for its canonical queue "
                "reset if necessary; clear the search field; enter the exact queued ID; click `Run Search`; wait for "
                "the filtered result; select the exact matching row; verify the case heading equals that ID; read the "
                "Return Intake Narrative; enter and visually verify the exact approved internal decision note; click "
                "the green `Apply Resolution` button exactly once; and wait for red `UPDATED!` before advancing. "
                "Do not return to an already completed queued message. When the captured queue is exhausted, end the "
                "workflow immediately without another Mail or Atlas action. Report `record` as the comma-separated "
                "completed queue, `staged_fields` as these exact approved values: "
                f"{json.dumps(approved_fields, sort_keys=True)}, and `visible_verification` confirming `UPDATED!` for "
                "every queued ID."
            )
        if spec.commit_instructions:
            commit_steps = "\n".join(f"- {instruction}" for instruction in spec.commit_instructions)
            return (
                "TURN 2 OF 2 — APPROVED COMMIT. The displayed staged plan was explicitly approved. "
                f"{target_guard}Re-check the same "
                f"application and execute only these approval-gated steps once:\n{commit_steps}\n"
                f"Use these exact approved values: {json.dumps(approved_fields, sort_keys=True)}. "
                "Visually verify completion. Report `record` as the same target context, `staged_fields` as those exact "
                "approved values, and `visible_verification` as the observed completion state."
            )
        return (
            f"TURN 2 OF 2 — COMMIT: the staged change has been approved. {target_guard}Re-check the staged values are still "
            "visible, press the Save/Commit control once, and verify the application shows success. Report `record` "
            "and `staged_fields` exactly as approved plus a `visible_verification` summary."
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


def _uses_fixture_contract(loaded: LoadedBundle) -> bool:
    properties = loaded.bundle.input_schema.get("properties", {})
    return RECORD_SELECTOR_INPUT in properties and any(name in properties for name in INPUT_FIELD_MAP)


def _is_no_matching_email(staged: StagedChange) -> bool:
    if staged.record_identity == "NO_MATCHING_EMAIL":
        return True
    return any(change.field == "workflow_status" and change.after == "no_matching_email" for change in staged.changes)


def _validate_atlas_stage_report(record: object, staged_fields: dict[object, object]) -> None:
    if staged_fields == {"workflow_status": "no_matching_email"}:
        if record != "NO_MATCHING_EMAIL":
            raise fault("malformed_stage_answer", "empty Atlas queue has an invalid record sentinel")
        return
    required_fields = {"case_queue", "case_id", "internal_decision_note"}
    if set(staged_fields) != required_fields:
        raise fault("malformed_stage_answer", "Atlas stage report has an invalid field set")
    raw_queue = staged_fields["case_queue"]
    if not isinstance(raw_queue, str):
        raise fault("malformed_stage_answer", "Atlas case queue is not a string")
    case_queue = tuple(case_id.strip().upper() for case_id in raw_queue.split(",") if case_id.strip())
    if not 1 <= len(case_queue) <= 3:
        raise fault("malformed_stage_answer", "Atlas case queue must contain one to three case IDs")
    if len(case_queue) != len(set(case_queue)) or not all(_is_atlas_case_id(case_id) for case_id in case_queue):
        raise fault("malformed_stage_answer", "Atlas case queue contains invalid or duplicate case IDs")
    if staged_fields["case_id"] != case_queue[0] or record != case_queue[0]:
        raise fault("malformed_stage_answer", "Atlas staged case does not match the first queued case")
    if not str(staged_fields["internal_decision_note"]).strip():
        raise fault("malformed_stage_answer", "Atlas internal decision note is empty")


def _is_atlas_case_id(value: str) -> bool:
    return value.startswith("RTN-") and len(value) == 8 and value[4:].isdigit()


def _parse_json_object(content: str) -> dict[str, object]:
    candidates = [content.strip()]
    first_brace = content.find("{")
    last_brace = content.rfind("}")
    if 0 <= first_brace < last_brace:
        candidates.append(content[first_brace : last_brace + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Stage answer contains no JSON object")


def _prose_matches_stage(spec: HoloTaskSpec, content: str) -> bool:
    normalized = " ".join(content.casefold().replace("_", " ").split())
    required = [spec.record_name, *spec.field_changes.keys(), *spec.field_changes.values()]
    return all(" ".join(str(value).casefold().replace("_", " ").split()) in normalized for value in required)


def _now() -> str:
    return datetime.now(UTC).isoformat()
