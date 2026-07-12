"""Launch the selected native CRM fixture before a live Holo run."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from desktop_fixtures.store import AppKey

from automation_foundry.execution.errors import fault


@dataclass(frozen=True)
class FixtureAppBundle:
    """Stable macOS identity used for LaunchServices activation."""

    app_name: str
    bundle_id: str
    output_name: str

    @property
    def path(self) -> Path:
        """Return the locally built application-bundle path."""
        root = Path(os.getenv("FOUNDRY_DESKTOP_APP_ROOT", "/private/tmp/automation-foundry-desktop-apps"))
        return root / f"{self.output_name}.app"

    @property
    def executable(self) -> Path:
        """Return the bundle's GUI executable path."""
        return self.path / "Contents" / "MacOS" / self.output_name


FIXTURE_APP_BUNDLES: dict[AppKey, FixtureAppBundle] = {
    "a": FixtureAppBundle("Northlight CRM", "ai.automationfoundry.northlight", "NorthlightCRM"),
    "b": FixtureAppBundle("Meridian Contacts", "ai.automationfoundry.meridian", "MeridianContacts"),
}


def ensure_fixture_running(app: AppKey, data_root: Path | None, wait_seconds: float) -> bool:
    """Restart the selected CRM fixture into a fresh visible window.

    Args:
        app: Fixture key, ``a`` or ``b``.
        data_root: Persisted fixture-state directory shared with execution verification.
        wait_seconds: Brief startup window before Holo receives its first turn.

    Returns:
        True after the fresh process is launched.

    Raises:
        ExecutionFault: When the fixture process exits during startup.
    """
    module = f"desktop_fixtures.crm_{app}"
    bundle = FIXTURE_APP_BUNDLES[app]
    if not bundle.executable.is_file():
        raise fault(
            "wrong_app_state",
            f"missing {bundle.app_name} app bundle at {bundle.path}; run ./scripts/build_desktop_apps.sh",
        )
    processes = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    matching_pids: list[int] = []
    for process_line in processes.stdout.splitlines():
        pid_text, _, process_command = process_line.strip().partition(" ")
        if (
            (module in process_command or str(bundle.executable) in process_command)
            and pid_text.isdigit()
            and int(pid_text) != os.getpid()
        ):
            matching_pids.append(int(pid_text))
    for process_id in matching_pids:
        try:
            os.kill(process_id, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + 2
    while matching_pids and time.monotonic() < deadline:
        matching_pids = [process_id for process_id in matching_pids if _process_exists(process_id)]
        if matching_pids:
            time.sleep(0.05)
    if matching_pids:
        raise fault("wrong_app_state", f"crm_{app} did not close before a fresh window could be launched")

    launch_command = ["open", "-F", "-a", str(bundle.path)]
    if data_root is not None:
        launch_command.extend(("--args", "--data-root", str(data_root)))
    overlay_path = os.getenv("FOUNDRY_HOLO_OVERLAY_PATH", "").strip()
    if overlay_path:
        if "--args" not in launch_command:
            launch_command.append("--args")
        launch_command.extend(("--overlay-path", overlay_path))
    completed = subprocess.run(launch_command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise fault("wrong_app_state", completed.stderr.strip() or f"crm_{app} failed to launch")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if _bundle_process_is_running(bundle):
            activation = subprocess.run(
                ["open", "-a", str(bundle.path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if activation.returncode != 0:
                raise fault(
                    "wrong_app_state",
                    activation.stderr.strip() or f"crm_{app} could not be activated",
                )
            return True
        time.sleep(0.05)
    raise fault("wrong_app_state", f"crm_{app} exited while its window was starting")


def _process_exists(process_id: int) -> bool:
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(process_id)],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not status or status.startswith("Z"):
        return False
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _bundle_process_is_running(bundle: FixtureAppBundle) -> bool:
    processes = subprocess.run(
        ["ps", "-axo", "command="],
        check=True,
        capture_output=True,
        text=True,
    )
    return any(str(bundle.executable) in command for command in processes.stdout.splitlines())
