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
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from desktop_fixtures.store import AppKey, load_state, state_path, write_state_atomic

from automation_foundry.execution.errors import fault

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


@dataclass(frozen=True)
class HoloTaskSpec:
    """Everything one run hands the adapter, resolved and validated up front."""

    app: AppKey
    record_name: str
    field_changes: dict[str, str]
    skill_markdown: str
    task_text: str
    max_steps: int
    max_time_seconds: int


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
        path = state_path(self.spec.app, self.data_root)
        state = load_state(path)
        for index, record in enumerate(state.records):
            if record.full_name == self.spec.record_name:
                state.records[index] = record.model_copy(update=dict(changes))
                write_state_atomic(path, state)
                return
        raise fault("wrong_app_state", f"record '{self.spec.record_name}' not found in crm_{self.spec.app}")


class LiveHoloAdapter:
    """Live HoloDesktop client shell; wired after the day-0 spike verifies signatures."""

    def __init__(self, spec: HoloTaskSpec):
        """Refuse cleanly until the verified client is available.

        Args:
            spec: Resolved task for the session.
        """
        self.spec = spec
        raise fault(
            "holo_unreachable",
            "live adapter not wired yet — run `python -m automation_foundry.execution.spike probe` "
            "and `holo-surface`, then implement LiveHoloAdapter against the verified signatures",
        )

    def start_session(self) -> str:
        """Unreachable until the live client is wired."""
        raise fault("holo_unreachable")

    def send_message(self, session_reference: str, message: str) -> TurnOutcome:
        """Unreachable until the live client is wired."""
        raise fault("holo_unreachable")

    def is_alive(self, session_reference: str) -> bool:
        """Unreachable until the live client is wired."""
        return False

    def cancel(self, session_reference: str) -> None:
        """Unreachable until the live client is wired."""
