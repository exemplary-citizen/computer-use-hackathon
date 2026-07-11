"""Integration-owned tests for the verified HoloDesktop client bridge."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agp_types import TrajectoryStatus

from automation_foundry.execution.errors import ExecutionFault
from automation_foundry.execution.holo import HoloTaskSpec, LiveHoloAdapter


def _spec() -> HoloTaskSpec:
    return HoloTaskSpec(
        app="a",
        record_name="Sarah Chen",
        field_changes={"status": "Qualified", "owner": "Priya Shah"},
        skill_markdown="Stage, wait for approval, then save.",
        task_text="Update Sarah Chen.",
        max_steps=24,
        max_time_seconds=180,
    )


class FakeClient:
    """Async slice used by LiveHoloAdapter without spawning a desktop runtime."""

    def __init__(self) -> None:
        self.steps = 0
        self.status = TrajectoryStatus.IDLE
        self.paused: list[str] = []
        self.cancelled: list[str] = []
        self.closed = False

    async def get_status(self, session_id: str) -> object:
        assert session_id == "remote-session"
        return SimpleNamespace(status=self.status, steps=self.steps)

    async def pause(self, session_id: str) -> None:
        self.paused.append(session_id)

    async def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def aclose(self) -> None:
        self.closed = True


class LiveHoloAdapterTests(unittest.TestCase):
    """Keep budgets, continuation, liveness, and cancellation bound to one session."""

    def setUp(self) -> None:
        self.client = FakeClient()
        self.daemon = SimpleNamespace(
            base_url="http://127.0.0.1:18795",
            token="local-test-token",
            aclose=AsyncMock(),
        )
        self.settings = SimpleNamespace(
            runtime=SimpleNamespace(
                port=18795,
                model=None,
                base_url=None,
                fast=False,
                runs_dir=None,
            )
        )
        self.sessions: list[object] = []
        self.turn_status = TrajectoryStatus.IDLE
        self.run_turn = AsyncMock(side_effect=self._run_turn)
        self.patches = (
            patch("automation_foundry.execution.holo.load_holo_env"),
            patch("automation_foundry.execution.holo.load_holo_settings", return_value=self.settings),
            patch(
                "automation_foundry.execution.holo.ensure_running",
                new=AsyncMock(return_value=self.daemon),
            ),
            patch("automation_foundry.execution.holo.AgentApiClient", return_value=self.client),
            patch("automation_foundry.execution.holo.run_turn", new=self.run_turn),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def _run_turn(self, client: object, session: object, message: str, **_: object) -> object:
        assert client is self.client
        self.sessions.append(session)
        if getattr(session, "session_id") is None:
            session.session_id = "remote-session"
        self.client.steps += 4
        if len(self.sessions) == 1:
            answer = json.dumps(
                {
                    "record": "Sarah Chen",
                    "staged_fields": {"status": "Qualified", "owner": "Priya Shah"},
                    "visible_verification": "Values are staged and Save was not pressed.",
                }
            )
        else:
            answer = "Saved once and verified success."
        return SimpleNamespace(status=self.turn_status, answer=answer, error=None)

    def test_two_turns_share_session_and_preserve_budgets(self) -> None:
        adapter = LiveHoloAdapter(_spec())
        reference = adapter.start_session()

        first = adapter.send_message(reference, "stage")
        self.assertEqual(first.steps_used, 4)
        self.assertTrue(adapter.is_alive(reference))
        second = adapter.send_message(reference, "commit")

        self.assertEqual(second.steps_used, 4)
        self.assertIs(self.sessions[0], self.sessions[1])
        self.assertEqual(self.sessions[0].session_id, "remote-session")
        for call in self.run_turn.await_args_list:
            self.assertEqual(call.kwargs["max_steps"], 24)
            self.assertEqual(call.kwargs["max_time_s"], 180.0)
            self.assertEqual(call.kwargs["idle_timeout_s"], 1_800)
        self.assertTrue(self.client.closed)
        self.daemon.aclose.assert_awaited_once()

    def test_cancel_pauses_then_deletes_same_remote_session(self) -> None:
        adapter = LiveHoloAdapter(_spec())
        reference = adapter.start_session()
        adapter.send_message(reference, "stage")

        adapter.cancel(reference)

        self.assertEqual(self.client.paused, ["remote-session"])
        self.assertEqual(self.client.cancelled, ["remote-session"])
        self.assertFalse(adapter.is_alive(reference))
        self.assertTrue(self.client.closed)

    def test_budget_fault_cancels_and_closes_session(self) -> None:
        self.turn_status = TrajectoryStatus.TIMED_OUT
        self.client.status = TrajectoryStatus.TIMED_OUT
        adapter = LiveHoloAdapter(_spec())
        reference = adapter.start_session()

        with self.assertRaises(ExecutionFault) as caught:
            adapter.send_message(reference, "stage")

        self.assertEqual(caught.exception.spec.code, "budget_exceeded")
        self.assertEqual(self.client.paused, ["remote-session"])
        self.assertEqual(self.client.cancelled, ["remote-session"])
        self.assertTrue(self.client.closed)


if __name__ == "__main__":
    unittest.main()
