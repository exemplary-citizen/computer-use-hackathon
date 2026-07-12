"""Operator CLIs: end-to-end smoke run and environment doctor.

``run`` exercises the ENTIRE public execution contract (REST via an
in-process client): reset fixture -> prepare -> confirm start -> staged diff
-> approve or reject -> terminal result -> exact persisted-state check. It is
simultaneously Member 1's integration acceptance check, the demo rehearsal
tool, and the eval-harness core.

Run with: ``uv run python -m automation_foundry.execution.smoke run --app a``
(add ``--live`` on a machine with HoloDesktop installed; default is the
scripted mock). ``doctor`` checks the machine and prints remediation per gap.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Literal

import tyro
from fastapi.testclient import TestClient

from desktop_fixtures.store import default_seed, load_state, state_path, write_state_atomic

from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.errors import ExecutionFault
from automation_foundry.execution.spike import gradium_check, probe

_DEFAULT_INPUTS = {
    "lead_name": "Sarah Chen",
    "lifecycle_status": "Qualified",
    "owner_name": "Priya Shah",
}


def run(
    app: Literal["a", "b"] = "a",
    live: bool = False,
    decision: Literal["approve", "reject", "timeout"] = "approve",
    script: str = "stage-ok",
) -> None:
    """Execute the fixed approved bundle end-to-end through the public API.

    Args:
        app: Target CRM fixture.
        live: Use the live HoloDesktop adapter instead of the scripted mock.
        decision: What to do at the approval gate.
        script: Scripted-fake behavior when not live.
    """
    os.environ["FOUNDRY_HOLO_MODE"] = "live" if live else "mock"
    os.environ["FOUNDRY_HOLO_MOCK_SCRIPT"] = script
    if decision == "timeout":
        os.environ.setdefault("FOUNDRY_APPROVAL_TIMEOUT_SECONDS", "5")

    from automation_foundry.api import create_app

    settings = ExecutionSettings()
    fixture_path = state_path(app, settings.fixture_data_root)
    write_state_atomic(fixture_path, default_seed())
    baseline = fixture_path.read_bytes()
    print(f"reset crm_{app} -> {fixture_path}")

    with TestClient(create_app()) as client:
        token = client.get("/api/execution/csrf-token").json()["token"]
        headers = {"X-Foundry-Token": token}
        prepared = client.post(
            "/api/execution/runs",
            json={"target_app": f"crm_{app}", "inputs": _DEFAULT_INPUTS},
            headers=headers,
        )
        if prepared.status_code != 201:
            print(f"prepare FAILED ({prepared.status_code}): {json.dumps(prepared.json(), indent=2)}")
            raise SystemExit(1)
        prepared_request = prepared.json()["request"]
        run_id = prepared_request["id"]
        # The adapter's turn deadline adds 30 seconds to the runtime budget, then
        # performs fail-closed cancellation/transport cleanup. Keep the smoke
        # harness outside that envelope so it can observe the terminal result.
        live_turn_timeout = float(prepared_request["max_time_seconds"]) + 90.0
        print(f"prepared run {run_id}")
        client.post(f"/api/execution/runs/{run_id}/confirm-start", headers=headers)
        status = _wait_for(
            client,
            run_id,
            "awaiting_commit_approval",
            "failed",
            "cancelled",
            timeout=live_turn_timeout if live else 60.0,
        )
        if status["state"] != "awaiting_commit_approval":
            _print_terminal(status, fixture_path, baseline)
            raise SystemExit(1)

        staged = status["staged_change"]
        assert isinstance(staged, dict)
        print("\nSTAGED CHANGE (nothing saved yet):")
        print(f"  app:    {staged['target_app']}")
        print(f"  record: {staged['record_identity']}")
        for change in staged["changes"]:
            print(f"  {change['field']}: {change['before']!r} -> {change['after']!r}")
        assert fixture_path.read_bytes() == baseline, "SAFETY VIOLATION: state changed before approval"
        print("  persisted state verified unchanged before approval ✔")

        if decision == "approve":
            client.post(
                f"/api/execution/runs/{run_id}/confirm-commit",
                json={"payload_sha256": staged["payload_sha256"], "actor": "exec-smoke"},
                headers=headers,
            )
        elif decision == "reject":
            client.post(f"/api/execution/runs/{run_id}/reject-commit", json={}, headers=headers)
        else:
            print(f"  waiting out the approval window ({os.environ['FOUNDRY_APPROVAL_TIMEOUT_SECONDS']}s)…")

        status = _wait_for(
            client,
            run_id,
            "succeeded",
            "failed",
            "cancelled",
            timeout=live_turn_timeout if live and decision == "approve" else 120.0,
        )
        _print_terminal(status, fixture_path, baseline)
        expected_state = {"approve": "succeeded", "reject": "cancelled", "timeout": "cancelled"}[decision]
        if status["state"] != expected_state:
            raise SystemExit(1)
        print(f"\nexec-smoke PASSED (state={status['state']}, decision={decision})")


def doctor() -> None:
    """Check this machine end-to-end and print remediation for every gap."""
    print("== environment ==")
    try:
        probe()
        probe_ok = True
    except SystemExit:
        probe_ok = False

    print("\n== approved bundle ==")
    settings = ExecutionSettings()
    bundle_ok = True
    try:
        from automation_foundry.execution.bundles import load_verified_bundle

        loaded = load_verified_bundle(settings.bundle_path)
        print(f"bundle: OK ({loaded.bundle.manifest.name!r} v{loaded.bundle.version.version})")
    except ExecutionFault as error:
        bundle_ok = False
        payload = error.payload()
        print(f"bundle: FAIL — {payload['message']} ({payload.get('detail', '')})")
        print(f"  remediation: {payload['remediation']}")

    print("\n== fixtures ==")
    for app in ("a", "b"):
        path = state_path(app, settings.fixture_data_root)
        state = load_state(path)
        print(f"crm_{app}: OK ({len(state.records)} records at {path})")

    print("\n== runs storage ==")
    settings.runs_root.mkdir(parents=True, exist_ok=True)
    settings.execution_database_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"runs root writable: {settings.runs_root}")

    print("\n== knobs ==")
    print(f"FOUNDRY_HOLO_MODE={settings.holo_mode}  FOUNDRY_VOICE_ENABLED={settings.voice_enabled}")
    print(f"FOUNDRY_APPROVAL_TIMEOUT_SECONDS={settings.approval_timeout_seconds}")

    if not (probe_ok and bundle_ok):
        print("\nDOCTOR RESULT: gaps found — see remediation above")
        raise SystemExit(1)
    print("\nDOCTOR RESULT: READY")


def _wait_for(client: TestClient, run_id: str, *states: str, timeout: float = 60.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while True:
        status = client.get(f"/api/execution/runs/{run_id}").json()
        if status["state"] in states:
            return dict(status)
        if time.monotonic() > deadline:
            print(f"timed out waiting for {states}; run is {status['state']}", file=sys.stderr)
            raise SystemExit(1)
        time.sleep(0.1)


def _print_terminal(status: dict[str, object], fixture_path: object, baseline: bytes) -> None:
    result = status.get("result") or {}
    assert isinstance(result, dict)
    print(f"\nTERMINAL: {status['state']}")
    if result.get("answer"):
        print(f"  answer: {result['answer']}")
    if result.get("verification_summary"):
        print(f"  verification: {result['verification_summary']}")
    if result.get("error_code"):
        print(f"  error_code: {result['error_code']}")
    from pathlib import Path

    changed = Path(str(fixture_path)).read_bytes() != baseline
    print(f"  persisted state changed: {changed}")


def main() -> None:
    """Dispatch the smoke/doctor CLI."""
    logging.basicConfig(level=logging.WARNING)
    tyro.extras.subcommand_cli_from_dict({"run": run, "doctor": doctor, "gradium-check": gradium_check})


if __name__ == "__main__":
    main()
