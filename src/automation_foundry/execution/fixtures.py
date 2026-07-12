"""Launch the selected native CRM fixture before a live Holo run."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from desktop_fixtures.store import AppKey

from automation_foundry.execution.errors import fault


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
    processes = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    matching_pids: list[int] = []
    for process_line in processes.stdout.splitlines():
        pid_text, _, command = process_line.strip().partition(" ")
        if module in command and pid_text.isdigit() and int(pid_text) != os.getpid():
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

    environment = os.environ.copy()
    if data_root is not None:
        environment["FOUNDRY_FIXTURE_DATA_ROOT"] = str(data_root)
    process = subprocess.Popen(
        [sys.executable, "-m", module],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(wait_seconds)
    if process.poll() is not None:
        raise fault("wrong_app_state", f"crm_{app} exited while its window was starting")
    return True


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
