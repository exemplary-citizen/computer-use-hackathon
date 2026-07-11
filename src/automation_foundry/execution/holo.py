"""HoloDesktop adapter boundary: one protocol, a scripted fake, a live shell.

All adapter calls are synchronous and run on a worker thread
(``asyncio.to_thread``) — the blocking client must never run on the event
loop. The scripted fake is the development and CI target; each script name
reproduces one failure mode the state machine must survive. The live adapter
is wired on the demo machine after the day-0 spike verifies the client's
real signatures (see ``spike.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from collections.abc import Coroutine
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from agp_types import TrajectoryStatus
from holo_desktop.agent_client import AgentApiClient, AgentDaemon, SpawnConfig, ensure_running
from holo_desktop.agent_client.session_runner import Session, run_turn
from holo_desktop.cli.bootstrap import load_holo_env
from holo_desktop.settings import load_holo_settings

from desktop_fixtures.store import AppKey, load_state, state_path, write_state_atomic

from automation_foundry.execution.errors import ExecutionFault, fault

_T = TypeVar("_T")
_LIVE_IDLE_TIMEOUT_SECONDS = 1_800

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
    """Synchronous bridge to HoloDesktop's authenticated async agent API.

    The verified 0.0.2 client creates the remote session with the first message,
    then continues it through ``send_message`` while ``idle_timeout_s`` keeps it
    alive across the approval pause. A local opaque reference lets the existing
    synchronous coordinator contract fail closed before that first message.
    """

    def __init__(self, spec: HoloTaskSpec):
        """Create a dedicated event-loop thread for the async Holo client.

        Args:
            spec: Resolved task for the session.
        """
        self.spec = spec
        self._reference = f"live-session-{uuid.uuid4().hex[:12]}"
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name=f"holo-client-{self._reference}",
            daemon=True,
        )
        self._state_lock = threading.Lock()
        self._started = False
        self._cancelled = False
        self._closed = False
        self._daemon: AgentDaemon | None = None
        self._client: AgentApiClient | None = None
        self._session: Session | None = None
        self._turns_sent = 0
        self._last_steps = 0
        self._loop_thread.start()

    def start_session(self) -> str:
        """Start or attach to the local runtime and return an opaque reference."""
        with self._state_lock:
            if self._cancelled or self._closed:
                raise fault("holo_unreachable", "adapter was already closed")
            if self._started:
                return self._reference
        try:
            self._call(self._start_async(), timeout=max(60.0, float(self.spec.max_time_seconds)))
        except ExecutionFault:
            self._best_effort_close(cancel_session=True)
            raise
        except Exception as error:
            self._best_effort_close(cancel_session=True)
            raise fault("holo_unreachable", type(error).__name__) from error
        with self._state_lock:
            self._started = True
        return self._reference

    def send_message(self, session_reference: str, message: str) -> TurnOutcome:
        """Run one bounded turn, creating or continuing the same remote session."""
        self._require_reference(session_reference)
        with self._state_lock:
            if not self._started or self._cancelled or self._closed:
                raise fault("session_lost", "live session is not available")
        try:
            outcome = self._call(
                self._send_async(message),
                timeout=max(60.0, float(self.spec.max_time_seconds) + 30.0),
            )
            self._stop_loop_if_closed()
            return outcome
        except ExecutionFault:
            self._best_effort_close(cancel_session=True)
            raise
        except Exception as error:
            self._best_effort_close(cancel_session=True)
            raise fault("session_lost", type(error).__name__) from error

    def is_alive(self, session_reference: str) -> bool:
        """Return true only for the verified idle state that accepts turn two."""
        try:
            self._require_reference(session_reference)
            with self._state_lock:
                if not self._started or self._cancelled or self._closed:
                    return False
            return self._call(self._is_alive_async(), timeout=15.0)
        except Exception:
            return False

    def cancel(self, session_reference: str) -> None:
        """Pause, cancel, and close the live session without raising teardown errors."""
        if session_reference != self._reference:
            return
        with self._state_lock:
            if self._cancelled or self._closed:
                return
            self._cancelled = True
        self._best_effort_close(cancel_session=True)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coroutine: Coroutine[Any, Any, _T], *, timeout: float) -> _T:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as error:
            future.cancel()
            raise fault("budget_exceeded", f"live client call exceeded {timeout:.0f}s") from error

    def _require_reference(self, session_reference: str) -> None:
        if session_reference != self._reference:
            raise fault("session_lost", "unknown live session reference")

    async def _start_async(self) -> None:
        load_holo_env()
        settings = load_holo_settings()
        config = SpawnConfig(
            port=settings.runtime.port,
            model=settings.runtime.model,
            base_url=settings.runtime.base_url,
            fake=False,
            fast=settings.runtime.fast,
            runs_dir=settings.runtime.runs_dir,
            require_fresh_for_config=False,
        )
        self._daemon = await ensure_running(config, settings=settings)
        self._client = AgentApiClient(self._daemon.base_url, self._daemon.token)
        self._session = Session()

    async def _send_async(self, message: str) -> TurnOutcome:
        client = self._client
        session = self._session
        if client is None or session is None:
            raise fault("session_lost", "live client was not initialized")

        async def discard_event(_: object) -> None:
            return

        outcome = await run_turn(
            client,
            session,
            message,
            max_steps=self.spec.max_steps,
            max_time_s=float(self.spec.max_time_seconds),
            idle_timeout_s=_LIVE_IDLE_TIMEOUT_SECONDS,
            on_event=discard_event,
        )
        if session.session_id is None or outcome.status is None:
            raise fault("session_lost", "runtime returned no session status")
        status = await client.get_status(session.session_id)
        steps_used = max(0, status.steps - self._last_steps)
        self._last_steps = status.steps
        if outcome.status is TrajectoryStatus.TIMED_OUT or status.status is TrajectoryStatus.TIMED_OUT:
            raise fault("budget_exceeded")
        if outcome.status not in (TrajectoryStatus.IDLE, TrajectoryStatus.COMPLETED):
            raise fault("session_lost", f"runtime ended turn as {outcome.status.value}")

        self._turns_sent += 1
        result = TurnOutcome(answer=outcome.answer, steps_used=steps_used)
        if self._turns_sent >= 2:
            await self._close_async(cancel_session=False)
        return result

    async def _is_alive_async(self) -> bool:
        client = self._client
        session = self._session
        if client is None or session is None or session.session_id is None:
            return False
        status = await client.get_status(session.session_id)
        return status.status is TrajectoryStatus.IDLE

    async def _close_async(self, *, cancel_session: bool) -> None:
        if self._closed:
            return
        client = self._client
        session = self._session
        if cancel_session and client is not None and session is not None and session.session_id is not None:
            with contextlib.suppress(Exception):
                await client.pause(session.session_id)
            with contextlib.suppress(Exception):
                await client.cancel(session.session_id)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()
        if self._daemon is not None:
            with contextlib.suppress(Exception):
                await self._daemon.aclose()
        self._client = None
        self._daemon = None
        self._closed = True

    def _best_effort_close(self, *, cancel_session: bool = False) -> None:
        with contextlib.suppress(Exception):
            self._call(self._close_async(cancel_session=cancel_session), timeout=30.0)
        self._stop_loop_if_closed()

    def _stop_loop_if_closed(self) -> None:
        if not self._closed or not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=1.0)
