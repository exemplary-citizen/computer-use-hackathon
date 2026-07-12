"""Live H adapter contract tests without desktop or provider access."""

import json
from types import SimpleNamespace

import pytest
from hai_agents import HaiAgentsEnvironment

from automation_foundry.execution.errors import ExecutionFault
from automation_foundry.execution.holo import HoloTaskSpec, LiveHoloAdapter


class FakeSessionHandle:
    """Retain one session identity across an approval-tool pause."""

    id = "remote-session-1"

    def __init__(self, sessions: "FakeSessions") -> None:
        self.sessions = sessions
        self.cancelled = False

    def wait_for_completion(self, **_kwargs):
        return SimpleNamespace(status="idle", outcome="success", answer="Saved and visibly verified.")

    def status(self):
        return self.sessions.get_session_status(self.id)

    def cancel(self) -> None:
        self.cancelled = True


class FakeClient:
    """Record that only the first turn creates a session."""

    def __init__(self) -> None:
        self.sessions = FakeSessions()
        self.handle = FakeSessionHandle(self.sessions)
        self.start_calls: list[dict[str, object]] = []

    def start_session(self, **kwargs):
        self.start_calls.append(kwargs)
        return self.handle


class FakeSessions:
    """Expose one pending approval request through the public sessions API."""

    def __init__(self) -> None:
        self.status_name = "awaiting_tool_results"
        self.steps = 7
        self.tool_results: list[object] = []
        self.stage_args = {
            "record": "Sarah Chen",
            "staged_fields": {"owner": "Priya Shah"},
            "visible_verification": "Owner is staged and Save was not pressed.",
        }

    def get_session_status(self, _session_id: str):
        return SimpleNamespace(status=self.status_name, steps=self.steps, error_code=None)

    def get_session_changes(self, _session_id: str, **_kwargs):
        event = SimpleNamespace(
            type="ActiveStateChangeEvent",
            data={
                "state": "awaiting_tool_results",
                "pending_tool_calls": [
                    {
                        "tool_name": "request_commit_approval",
                        "args": self.stage_args,
                        "id": "approval-call-1",
                    }
                ],
            },
        )
        return SimpleNamespace(new_events=[event])

    def send_session_tool_results(self, _session_id: str, *, request: object) -> None:
        self.tool_results.append(request)
        self.status_name = "idle"
        self.steps = 10


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
    assert "answer_schema" not in client.start_calls[0]
    agent = client.start_calls[0]["agent"]
    assert agent.tools[0].name == "request_commit_approval"
    assert len(client.sessions.tool_results) == 1
    assert client.sessions.tool_results[0].result["approved"] is True
    assert client.sessions.tool_results[0].result["instruction"] == "approved; commit"
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


def test_live_adapter_fails_closed_if_session_ends_without_approval_tool() -> None:
    client = FakeClient()
    client.sessions.status_name = "completed"
    adapter = LiveHoloAdapter(_spec(), lambda _environment: client)

    with pytest.raises(ExecutionFault) as caught:
        adapter.send_message(adapter.start_session(), "stage only")

    assert caught.value.spec.code == "malformed_stage_answer"


def test_live_adapter_reports_provider_rate_limit_without_retry() -> None:
    class RateLimitedClient:
        def start_session(self, **_kwargs):
            raise RuntimeError("local bridge received HTTP 429")

    adapter = LiveHoloAdapter(_spec(), lambda _environment: RateLimitedClient())

    with pytest.raises(ExecutionFault) as caught:
        adapter.send_message(adapter.start_session(), "stage only")

    assert caught.value.spec.code == "holo_rate_limited"


def test_live_adapter_returns_structured_stage_report_from_approval_tool() -> None:
    client = FakeClient()
    adapter = LiveHoloAdapter(_spec(), lambda _environment: client)

    staged = adapter.send_message(adapter.start_session(), "stage only")

    assert json.loads(staged.answer) == client.sessions.stage_args


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
