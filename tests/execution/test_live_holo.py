"""Live H adapter contract tests without desktop or provider access."""

import json
from types import SimpleNamespace

import pytest
from hai_agents import HaiAgentsEnvironment

from automation_foundry.execution.errors import ExecutionFault
from automation_foundry.execution.holo import HoloTaskSpec, LiveHoloAdapter


class FakeSessionHandle:
    """Settle two turns while retaining one session identity."""

    id = "remote-session-1"

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.wait_count = 0
        self.steps = 0
        self.cancelled = False
        self.first_outcome: str | None = "partial"
        self.answer_schema = None

    def send_message(self, message: str) -> None:
        self.messages.append(message)

    def wait_for_completion(self, **_kwargs):
        self.wait_count += 1
        if self.wait_count == 1:
            self.steps = 7
            answer = {
                "record": "Sarah Chen",
                "staged_fields": {"owner": "Priya Shah"},
                "visible_verification": "Owner is staged and Save was not pressed.",
            }
        else:
            self.steps = 10
            answer = {
                "record": "Sarah Chen",
                "staged_fields": {"owner": "Priya Shah"},
                "visible_verification": "Saved and visibly verified.",
            }
        if self.answer_schema is not None:
            answer = self.answer_schema(**answer)
        outcome = self.first_outcome if self.wait_count == 1 else "success"
        return SimpleNamespace(status="idle", outcome=outcome, answer=answer)

    def status(self):
        return SimpleNamespace(status="idle", steps=self.steps)

    def cancel(self) -> None:
        self.cancelled = True


class FakeClient:
    """Record that only the first turn creates a session."""

    def __init__(self) -> None:
        self.handle = FakeSessionHandle()
        self.start_calls: list[dict[str, object]] = []

    def start_session(self, **kwargs):
        self.start_calls.append(kwargs)
        self.handle.answer_schema = kwargs.get("answer_schema")
        return self.handle


def test_live_adapter_uses_one_session_for_stage_and_commit() -> None:
    client = FakeClient()
    environments: list[HaiAgentsEnvironment] = []
    adapter = LiveHoloAdapter(_spec(), lambda environment: environments.append(environment) or client)
    reference = adapter.start_session()

    staged = adapter.send_message(reference, "stage only")
    assert adapter.is_alive(reference)
    committed = adapter.send_message(reference, "approved; commit")

    assert environments == [HaiAgentsEnvironment.US]
    assert len(client.start_calls) == 1
    assert client.start_calls[0]["messages"] == "stage only"
    assert client.start_calls[0]["answer_schema"].__name__ == "_LiveTurnAnswer"
    assert client.handle.messages == ["approved; commit"]
    assert json.loads(staged.answer)["staged_fields"] == {"owner": "Priya Shah"}
    assert staged.steps_used == 7
    assert committed.steps_used == 3


def test_live_adapter_cancel_terminates_retained_session() -> None:
    client = FakeClient()
    adapter = LiveHoloAdapter(_spec(), lambda _environment: client)
    reference = adapter.start_session()
    adapter.send_message(reference, "stage only")

    adapter.cancel(reference)

    assert client.handle.cancelled
    assert not adapter.is_alive(reference)
    with pytest.raises(ExecutionFault, match="session_lost"):
        adapter.send_message(reference, "commit")


def test_live_adapter_accepts_missing_optional_stage_outcome_when_answer_is_structured() -> None:
    client = FakeClient()
    client.handle.first_outcome = None
    adapter = LiveHoloAdapter(_spec(), lambda _environment: client)

    staged = adapter.send_message(adapter.start_session(), "stage only")

    assert json.loads(staged.answer)["record"] == "Sarah Chen"


def _spec() -> HoloTaskSpec:
    return HoloTaskSpec(
        app="a",
        record_name="Sarah Chen",
        field_changes={"owner": "Priya Shah"},
        skill_markdown="Stage, wait, then save after approval.",
        task_text="Update Sarah Chen.",
        max_steps=20,
        max_time_seconds=180,
    )
