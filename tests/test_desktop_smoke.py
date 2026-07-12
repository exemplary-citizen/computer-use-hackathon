"""Tests for the minimal public-SDK desktop-control probe."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from automation_foundry import desktop_smoke


def test_doctor_can_run_without_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.dict("os.environ", {}, clear=True):
        desktop_smoke.doctor()

    output = capsys.readouterr().out
    assert "SKIP HAI_API_KEY" in output
    assert "desktop-smoke doctor PASSED" in output


def test_doctor_validates_key_against_us_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    class FakeQuota:
        def get_token_quota(self) -> object:
            return object()

    class FakeClient:
        quota = FakeQuota()

        def __init__(self, *, environment: desktop_smoke.HaiAgentsEnvironment) -> None:
            assert environment is desktop_smoke.HaiAgentsEnvironment.US

    with (
        patch.dict("os.environ", {"HAI_API_KEY": "test-key"}, clear=True),
        patch.object(desktop_smoke, "Client", FakeClient),
    ):
        desktop_smoke.doctor(require_api_key=True)

    assert "PASS H agent authentication: US endpoint accepted the key" in capsys.readouterr().out


def test_run_requires_explicit_control_confirmation() -> None:
    with pytest.raises(SystemExit) as caught:
        desktop_smoke.run()

    assert caught.value.code == 2


def test_run_requires_api_key_after_confirmation() -> None:
    with patch.dict("os.environ", {}, clear=True), pytest.raises(SystemExit) as caught:
        desktop_smoke.run(confirm_control=True)

    assert caught.value.code == 1


def test_run_uses_inline_local_desktop_agent(capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, object] = {}

    class FakeSession:
        id = "session-123"

        def wait_for_completion(self, **kwargs: object) -> object:
            captured["wait"] = kwargs
            return SimpleNamespace(status="completed", outcome="success", answer="Done", error=None)

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs

        def start_session(self, **kwargs: object) -> FakeSession:
            captured["start"] = kwargs
            return FakeSession()

    with (
        patch.dict("os.environ", {"HAI_API_KEY": "test-key"}, clear=True),
        patch.object(desktop_smoke, "Client", FakeClient),
    ):
        desktop_smoke.run(confirm_control=True)

    start = captured["start"]
    assert isinstance(start, dict)
    agent = start["agent"]
    assert isinstance(agent, desktop_smoke.Agent)
    assert agent.environments[0].kind == "desktop"
    assert agent.environments[0].host == "user_device"
    assert start["messages"] == desktop_smoke.DEFAULT_TASK
    assert start["max_steps"] == 20
    assert captured["client"] == {"environment": desktop_smoke.HaiAgentsEnvironment.US}
    assert "region: us" in capsys.readouterr().out


def test_run_retries_eu_after_us_auth_rejection(capsys: pytest.CaptureFixture[str]) -> None:
    attempted: list[desktop_smoke.HaiAgentsEnvironment] = []

    class AuthError(Exception):
        pass

    class FakeSession:
        id = "session-eu"

        def wait_for_completion(self, **kwargs: object) -> object:
            return SimpleNamespace(status="completed", outcome="success", answer="Done", error=None)

    class FakeClient:
        def __init__(self, *, environment: desktop_smoke.HaiAgentsEnvironment) -> None:
            self.environment = environment
            attempted.append(environment)

        def start_session(self, **kwargs: object) -> FakeSession:
            if self.environment is desktop_smoke.HaiAgentsEnvironment.US:
                try:
                    raise AuthError("auth error checking channel (403)")
                except AuthError as error:
                    raise RuntimeError("local desktop bridge failed") from error
            return FakeSession()

    with (
        patch.dict("os.environ", {"HAI_API_KEY": "test-key"}, clear=True),
        patch.object(desktop_smoke, "Client", FakeClient),
    ):
        desktop_smoke.run(confirm_control=True)

    assert attempted == [desktop_smoke.HaiAgentsEnvironment.US, desktop_smoke.HaiAgentsEnvironment.EU]
    assert "region: eu" in capsys.readouterr().out


def test_run_reports_auth_rejection_without_traceback(caplog: pytest.LogCaptureFixture) -> None:
    class AuthError(Exception):
        pass

    class FakeClient:
        def __init__(self, *, environment: desktop_smoke.HaiAgentsEnvironment) -> None:
            pass

        def start_session(self, **kwargs: object) -> object:
            raise AuthError("auth error checking channel (403)")

    with (
        patch.dict("os.environ", {"HAI_API_KEY": "test-key"}, clear=True),
        patch.object(desktop_smoke, "Client", FakeClient),
        pytest.raises(SystemExit) as caught,
    ):
        desktop_smoke.run(confirm_control=True)

    assert caught.value.code == 1
    assert "rejected by the H US and EU agent endpoints" in caplog.text


def test_run_rejects_partial_outcome() -> None:
    class FakeSession:
        id = "session-partial"

        def wait_for_completion(self, **kwargs: object) -> object:
            return SimpleNamespace(status="completed", outcome="partial", answer="Not finished", error=None)

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def start_session(self, **kwargs: object) -> FakeSession:
            return FakeSession()

    with (
        patch.dict("os.environ", {"HAI_API_KEY": "test-key"}, clear=True),
        patch.object(desktop_smoke, "Client", FakeClient),
        pytest.raises(SystemExit) as caught,
    ):
        desktop_smoke.run(confirm_control=True, region="us")

    assert caught.value.code == 1
