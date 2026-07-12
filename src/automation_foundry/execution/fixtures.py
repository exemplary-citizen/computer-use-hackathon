"""Launch the selected native CRM fixture before a live Holo run."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from desktop_fixtures.store import AppKey

from automation_foundry.execution.errors import fault


def ensure_fixture_running(app: AppKey, data_root: Path | None, wait_seconds: float) -> bool:
    """Start the selected CRM fixture when no matching process is running.

    Args:
        app: Fixture key, ``a`` or ``b``.
        data_root: Persisted fixture-state directory shared with execution verification.
        wait_seconds: Brief startup window before Holo receives its first turn.

    Returns:
        True when a new process was launched, otherwise False.

    Raises:
        ExecutionFault: When the fixture process exits during startup.
    """
    module = f"desktop_fixtures.crm_{app}"
    processes = subprocess.run(
        ["ps", "-axo", "command="],
        check=True,
        capture_output=True,
        text=True,
    )
    if any(module in command for command in processes.stdout.splitlines()):
        return False

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
