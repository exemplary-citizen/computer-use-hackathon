"""Telegram CLI local NemoClaw configuration tests."""

import subprocess
from pathlib import Path

from automation_foundry.settings import AppSettings
from automation_foundry.surfaces.telegram import cli


def test_resolve_settings_discovers_masked_local_gateway_token(monkeypatch, tmp_path: Path) -> None:
    captured_command: tuple[str, ...] | None = None

    def fake_run(command, **kwargs):
        nonlocal captured_command
        captured_command = command
        assert kwargs == {"check": False, "capture_output": True, "text": True, "timeout": 30}
        return subprocess.CompletedProcess(command, 0, stdout="super-secret-token\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    workspace_mount = tmp_path / "workspace"

    resolved = cli.resolve_telegram_settings(AppSettings(workspace_mount=workspace_mount))

    assert captured_command == ("nemohermes", "hai-hermes", "gateway-token", "--quiet")
    assert resolved.nemoclaw_sandbox_name == "hai-hermes"
    assert resolved.workspace_mount == workspace_mount
    assert not resolved.workspace_require_mount
    assert resolved.hermes_api_key is not None
    assert resolved.hermes_api_key.get_secret_value() == "super-secret-token"
    assert "super-secret-token" not in repr(resolved)
    assert workspace_mount.is_dir()


def test_resolve_settings_reports_gateway_failure_without_command_output(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="provider-secret-error")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    try:
        cli.resolve_telegram_settings(AppSettings(workspace_mount=tmp_path / "workspace"))
    except RuntimeError as exc:
        assert str(exc) == "Could not read the local NemoClaw Hermes gateway credential"
        assert "provider-secret-error" not in str(exc)
    else:
        raise AssertionError("Gateway discovery failure was not reported")
