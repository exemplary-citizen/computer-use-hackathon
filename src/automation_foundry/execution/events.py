"""Per-run event log: one sequencer, canonical JSONL, replayable stream.

Ordering contract (SPEC §10/§11): sequences are monotonically increasing per
run and assigned by exactly one writer; ``events.jsonl`` under the run
directory is canonical; WebSocket/API consumers replay from any sequence and
then follow live. A torn final line (crash mid-append) is skipped on read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ValidationError

from automation_foundry.contracts import RunEvent, RunState

SUBSCRIBER_QUEUE_LIMIT = 256


class EventLogConfig(BaseModel):
    """Location of per-run directories."""

    runs_root: Path

    def make(self) -> EventLog:
        """Build an event log rooted at ``runs_root``."""
        return EventLog(self)


class EventLog:
    """Append-only, sequence-assigning event store with live fan-out."""

    def __init__(self, config: EventLogConfig):
        """Prepare in-memory sequencing state.

        Args:
            config: Validated run-directory root.
        """
        self.config = config
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._next_sequence: dict[UUID, int] = {}
        self._subscribers: dict[UUID, list[asyncio.Queue[RunEvent]]] = {}

    async def append(
        self,
        run_id: UUID,
        state: RunState,
        event_type: str,
        message: str,
        payload: Mapping[str, object] | None = None,
    ) -> RunEvent:
        """Assign the next sequence, persist the event, and fan it out.

        Args:
            run_id: Run the event belongs to.
            state: Run state at emission time.
            event_type: Stable machine-readable type.
            message: User-safe message.
            payload: Structured, user-safe details.

        Returns:
            The persisted event including its assigned sequence.
        """
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            sequence = self._next_sequence.get(run_id)
            if sequence is None:
                sequence = self._scan_next_sequence(run_id)
            event = RunEvent(
                run_id=run_id,
                sequence=sequence,
                state=state,
                event_type=event_type,
                message=message,
                payload=dict(payload or {}),  # type: ignore[arg-type]
            )
            path = self._events_path(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
            self._next_sequence[run_id] = sequence + 1
        for queue in list(self._subscribers.get(run_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop it; the client re-syncs via replay().
                self.unsubscribe(run_id, queue)
        return event

    def replay(self, run_id: UUID, after_sequence: int = -1) -> list[RunEvent]:
        """Read persisted events with sequence greater than ``after_sequence``.

        Args:
            run_id: Run to read.
            after_sequence: Last sequence the caller already has.
        """
        path = self._events_path(run_id)
        if not path.is_file():
            return []
        events: list[RunEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = RunEvent.model_validate_json(line)
            except ValidationError:
                continue  # torn or corrupt line (crash mid-append)
            if event.sequence > after_sequence:
                events.append(event)
        return events

    def subscribe(self, run_id: UUID) -> asyncio.Queue[RunEvent]:
        """Register a live-stream consumer for one run."""
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_LIMIT)
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: UUID, queue: asyncio.Queue[RunEvent]) -> None:
        """Remove a live-stream consumer."""
        queues = self._subscribers.get(run_id, [])
        if queue in queues:
            queues.remove(queue)

    def _events_path(self, run_id: UUID) -> Path:
        return self.config.runs_root / str(run_id) / "events.jsonl"

    def _scan_next_sequence(self, run_id: UUID) -> int:
        events = self.replay(run_id)
        return events[-1].sequence + 1 if events else 0
