"""HoloDesktop adapter boundary: one protocol, a scripted fake, a live shell.

All adapter calls are synchronous and run on a worker thread
(``asyncio.to_thread``) — the blocking client must never run on the event
loop. The scripted fake is the development and CI target; each script name
reproduces one failure mode the state machine must survive. The live adapter
is wired on the demo machine after the day-0 spike verifies the client's
real signatures (see ``spike.py``).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from hai_agents import Client, HaiAgentsEnvironment
from hai_agents.polling import SessionHandle
from hai_agents.types.agent import Agent
from hai_agents.types.environment import Environment_Desktop
from hai_agents.types.session_changes_answer import SessionChangesAnswer
from hai_agents.types.tool_definition import ToolDefinition
from hai_agents.types.tool_request import ToolRequest
from hai_agents.types.tool_result_event import ToolResultEvent

from desktop_fixtures.store import AppKey, load_state, state_path, write_state_atomic

from automation_foundry.execution.errors import ExecutionFault, fault

FAKE_SCRIPTS = (
    "stage-ok",
    "stage-commits-anyway",
    "malformed-answer",
    "dies-during-await",
    "dies-during-commit",
    "commit-wrong-value",
    "timeout",
    "wrong-app",
    "holo-unreachable",
)

_APPROVAL_TOOL_NAME = "request_commit_approval"
_TERMINAL_SESSION_STATUSES = ("completed", "failed", "idle", "interrupted", "timed_out")


@dataclass(frozen=True)
class HoloTaskSpec:
    """Everything one run hands the adapter, resolved and validated up front."""

    app: str
    record_name: str
    field_changes: dict[str, str]
    skill_markdown: str
    task_text: str
    max_steps: int
    max_time_seconds: int
    region: Literal["us", "eu"] = "us"
    stage_instructions: tuple[str, ...] = ()
    commit_instructions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnOutcome:
    """Result of one Holo turn."""

    answer: str
    steps_used: int


class HoloAdapter(Protocol):
    """Synchronous session contract the run coordinator drives from a thread."""

    def start_session(self) -> str:
        """Create a session and return its reference."""
        ...

    def send_message(self, session_reference: str, message: str) -> TurnOutcome:
        """Send one turn to the live session and block for its outcome."""
        ...

    def is_alive(self, session_reference: str) -> bool:
        """Cheaply report whether the session can still accept a turn."""
        ...

    def cancel(self, session_reference: str) -> None:
        """Best-effort session termination at the next action boundary."""
        ...


@dataclass
class ScriptedFakeHolo:
    """Deterministic Holo stand-in operating on the real fixture store.

    ``stage-ok`` fills nothing on disk (staging is in the app's memory in
    real life); the commit turn applies the requested changes to the fixture
    store exactly as the app's Save button would.
    """

    spec: HoloTaskSpec
    script: str
    data_root: Path | None = None
    _turns_sent: int = 0
    _cancelled: bool = False
    _session: str = field(default_factory=lambda: f"fake-session-{uuid.uuid4().hex[:12]}")
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start_session(self) -> str:
        """Return a fake session reference, or fail like a missing install."""
        if self.script == "holo-unreachable":
            raise fault("holo_unreachable", "scripted fake configured as unreachable")
        return self._session

    def send_message(self, session_reference: str, message: str) -> TurnOutcome:
        """Play the configured script for the stage (1st) or commit (2nd) turn."""
        del message
        with self._lock:
            if self._cancelled:
                raise fault("session_lost", "session was cancelled")
            self._turns_sent += 1
            turn = self._turns_sent
        if session_reference != self._session:
            raise fault("session_lost", "unknown session reference")
        if turn == 1:
            return self._stage_turn()
        return self._commit_turn()

    def is_alive(self, session_reference: str) -> bool:
        """Report session liveness per the configured script."""
        if session_reference != self._session or self._cancelled:
            return False
        return not (self.script == "dies-during-await" and self._turns_sent >= 1)

    def cancel(self, session_reference: str) -> None:
        """Mark the session dead; later turns raise ``session_lost``."""
        del session_reference
        with self._lock:
            self._cancelled = True

    def _stage_turn(self) -> TurnOutcome:
        if self.script == "timeout":
            raise fault("budget_exceeded", f"exceeded {self.spec.max_steps} steps (scripted)")
        if self.script == "wrong-app":
            raise fault("wrong_app_state", f"no window found for crm_{self.spec.app} (scripted)")
        if self.script == "malformed-answer":
            return TurnOutcome(answer="I did some things on the screen.", steps_used=3)
        if self.script == "stage-commits-anyway":
            self._apply_changes(self.spec.field_changes)
        answer = json.dumps(
            {
                "record": self.spec.record_name,
                "staged_fields": self.spec.field_changes,
                "visible_verification": (
                    f"Form for {self.spec.record_name} shows the new values; Save has not been pressed."
                ),
            }
        )
        return TurnOutcome(answer=answer, steps_used=9)

    def _commit_turn(self) -> TurnOutcome:
        if self.script == "dies-during-commit":
            raise fault("session_lost", "session terminated mid-commit (scripted)")
        changes = dict(self.spec.field_changes)
        if self.script == "commit-wrong-value":
            # Corrupt a free-text field so the store accepts the write and the
            # host's post-commit verification is what catches the mismatch.
            wrong_key = "owner" if "owner" in changes else next(iter(changes))
            changes[wrong_key] = changes[wrong_key] + " (typo)"
        self._apply_changes(changes)
        return TurnOutcome(
            answer=f"Pressed Save; {self.spec.record_name} now shows the requested values.",
            steps_used=4,
        )

    def _apply_changes(self, changes: dict[str, str]) -> None:
        if self.spec.app not in ("a", "b"):
            raise fault("wrong_app_state", f"scripted fixture does not support {self.spec.app}")
        path = state_path(cast(AppKey, self.spec.app), self.data_root)
        state = load_state(path)
        for index, record in enumerate(state.records):
            if record.full_name == self.spec.record_name:
                state.records[index] = record.model_copy(update=dict(changes))
                write_state_atomic(path, state)
                return
        raise fault("wrong_app_state", f"record '{self.spec.record_name}' not found in crm_{self.spec.app}")


class LiveHoloAdapter:
    """Two-turn H desktop adapter backed by one retained public-SDK session."""

    def __init__(
        self,
        spec: HoloTaskSpec,
        client_factory: Callable[[HaiAgentsEnvironment], Any] | None = None,
    ):
        """Initialize a deferred live session.

        Args:
            spec: Resolved task for the session.
            client_factory: Injectable H client factory for deterministic tests.
        """
        self.spec = spec
        self._client_factory = client_factory or (lambda environment: Client(environment=environment))
        self._reference = f"h-live-{uuid.uuid4().hex}"
        self._client: Any | None = None
        self._handle: SessionHandle[SessionChangesAnswer] | Any | None = None
        self._pending_approval: ToolRequest | None = None
        self._cancelled = False
        self._last_steps = 0
        self._turns_completed = 0
        self._agent = Agent(
            name="automation-foundry-live-executor",
            description="Executes one approved two-turn desktop automation on the local Mac.",
            environments=[Environment_Desktop(id="foundry-mac", host="user_device")],
            tools=[
                ToolDefinition(
                    name=_APPROVAL_TOOL_NAME,
                    description=(
                        "After completing and visually verifying every stage-only action, call this tool exactly once "
                        "with the staged record, exact proposed fields, and visible verification. This is the only "
                        "valid way to request commit approval. Wait for the tool result before taking any persistent "
                        "action; do not end or complete the session while approval is pending."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "record": {"type": "string"},
                            "staged_fields": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            },
                            "visible_verification": {"type": "string"},
                        },
                        "required": ["record", "staged_fields", "visible_verification"],
                        "additionalProperties": False,
                    },
                )
            ],
            instructions=(
                "Operate only the target application named by the approved task. Treat the first user message as a "
                "strict staging turn: never Save, Commit, Submit, send, publish, purchase, delete, or perform another "
                "persistent action. After visually verifying the staged state, call request_commit_approval exactly "
                "once with the requested record, exact staged fields, and visible verification. Do not answer, finish, "
                "or complete the session before calling the tool. Wait for its result. Perform a persistent action only "
                "when that tool result explicitly states that the staged change was approved."
            ),
        )

    def start_session(self) -> str:
        """Reserve a host reference; the first message creates the remote session."""
        return self._reference

    def send_message(self, session_reference: str, message: str) -> TurnOutcome:
        """Start staging or resolve its approval tool on the retained session."""
        self._require_reference(session_reference)
        try:
            if self._handle is None:
                environment = HaiAgentsEnvironment.US if self.spec.region == "us" else HaiAgentsEnvironment.EU
                self._client = self._client_factory(environment)
                self._handle = self._client.start_session(
                    agent=self._agent,
                    messages=message,
                    max_steps=self.spec.max_steps,
                    max_time_s=float(self.spec.max_time_seconds),
                )
                return self._wait_for_approval_request()
            self._resolve_approval(message)
            result = self._handle.wait_for_completion(timeout_seconds=float(self.spec.max_time_seconds + 30))
        except TimeoutError as exc:
            self.cancel(session_reference)
            raise fault("budget_exceeded") from exc
        except ExecutionFault:
            raise
        except Exception as exc:
            if _is_rate_limit(exc):
                raise fault("holo_rate_limited") from exc
            raise fault("holo_unreachable", type(exc).__name__) from exc
        status = str(result.status)
        outcome = str(result.outcome) if result.outcome is not None else None
        if status not in ("idle", "completed") or outcome != "success":
            code = "budget_exceeded" if status == "timed_out" else "wrong_app_state"
            error_code = getattr(result, "error_code", None)
            raise fault(code, f"status={status}, outcome={outcome or 'unknown'}, error_code={error_code or 'none'}")
        answer = result.answer
        if answer is None:
            raise fault("malformed_stage_answer", "live session returned no answer")
        status_snapshot = self._handle.status()
        total_steps = int(status_snapshot.steps or self._last_steps)
        turn_steps = max(0, total_steps - self._last_steps)
        self._last_steps = total_steps
        self._turns_completed += 1
        return TurnOutcome(
            answer=json.dumps(answer, sort_keys=True) if isinstance(answer, dict) else str(answer),
            steps_used=turn_steps,
        )

    def is_alive(self, session_reference: str) -> bool:
        """Return whether the retained session can accept the commit turn."""
        if (
            session_reference != self._reference
            or self._cancelled
            or self._handle is None
            or self._pending_approval is None
        ):
            return False
        try:
            return str(self._handle.status().status) == "awaiting_tool_results"
        except Exception:
            return False

    def cancel(self, session_reference: str) -> None:
        """Cancel the retained session without creating a replacement."""
        if session_reference != self._reference:
            return
        self._cancelled = True
        if self._handle is not None:
            try:
                self._handle.cancel()
            except Exception:
                pass

    def _require_reference(self, session_reference: str) -> None:
        if session_reference != self._reference or self._cancelled:
            raise fault("session_lost", "unknown or cancelled live session")

    def _wait_for_approval_request(self) -> TurnOutcome:
        if self._client is None or self._handle is None:
            raise fault("holo_unreachable", "live session was not initialized")
        deadline = time.monotonic() + self.spec.max_time_seconds + 30
        while time.monotonic() < deadline:
            status = self._client.sessions.get_session_status(self._handle.id)
            status_name = str(status.status)
            if status_name == "awaiting_tool_results":
                pending = self._approval_request()
                if pending is None:
                    time.sleep(0.25)
                    continue
                self._pending_approval = pending
                total_steps = int(status.steps or self._last_steps)
                turn_steps = max(0, total_steps - self._last_steps)
                self._last_steps = total_steps
                self._turns_completed += 1
                return TurnOutcome(answer=json.dumps(pending.args or {}, sort_keys=True), steps_used=turn_steps)
            if status_name in _TERMINAL_SESSION_STATUSES:
                if status_name == "timed_out":
                    raise fault("budget_exceeded")
                if status_name in ("failed", "interrupted"):
                    raise fault(
                        "wrong_app_state",
                        f"status={status_name}, error_code={getattr(status, 'error_code', None) or 'none'}",
                    )
                raise fault("malformed_stage_answer", "live session ended before requesting commit approval")
            time.sleep(0.25)
        raise TimeoutError("live session did not request commit approval before the deadline")

    def _approval_request(self) -> ToolRequest | None:
        if self._client is None or self._handle is None:
            return None
        changes = self._client.sessions.get_session_changes(
            self._handle.id,
            from_index=0,
            include_events=True,
            wait_for_seconds=0,
        )
        events = changes.new_events if changes is not None else []
        for event in reversed(events or []):
            if event.type != "ActiveStateChangeEvent":
                continue
            data = event.data.model_dump() if hasattr(event.data, "model_dump") else event.data
            if not isinstance(data, dict) or data.get("state") != "awaiting_tool_results":
                continue
            for raw_call in data.get("pending_tool_calls") or []:
                call = raw_call.model_dump() if hasattr(raw_call, "model_dump") else raw_call
                if isinstance(call, dict) and call.get("tool_name") == _APPROVAL_TOOL_NAME:
                    return ToolRequest.model_validate(call)
        return None

    def _resolve_approval(self, instruction: str) -> None:
        if self._client is None or self._handle is None or self._pending_approval is None:
            raise fault("session_lost", "session has no pending commit approval request")
        if str(self._handle.status().status) != "awaiting_tool_results":
            raise fault("session_lost", "session is no longer awaiting commit approval")
        self._client.sessions.send_session_tool_results(
            self._handle.id,
            request=ToolResultEvent(
                tool_req=self._pending_approval,
                result={"approved": True, "instruction": instruction},
            ),
        )
        self._pending_approval = None


def _is_rate_limit(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if getattr(current, "status_code", None) == 429 or getattr(response, "status_code", None) == 429:
            return True
        if "429" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False
