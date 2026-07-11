"""Execution-lane evals: live-trial matrix, voice routing, evidence records.

Implements EVALS §3.6/§3.7 with the reviewed protocol: reset before every
trial, first-attempt results only, every trial log retained. ``--live``
drives a real HoloDesktop (demo machine); the default mock mode validates
the harness itself end-to-end through the public REST contract.

Run with:
    uv run python -m automation_foundry.evals.execution trials --app a
    uv run python -m automation_foundry.evals.execution voice-cases
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import tyro

from desktop_fixtures.store import AppKey, default_seed, load_state, state_path, write_state_atomic

from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.intent import ResolvedCommand, RuleBasedIntentResolver, VoiceContext


@dataclass(frozen=True)
class TrialScenario:
    """One deterministic record-update scenario."""

    name: str
    lead_name: str
    lifecycle_status: str
    owner_name: str


SCENARIOS = (
    TrialScenario("canonical_update", "Sarah Chen", "Qualified", "Priya Shah"),
    TrialScenario("reactivate_churned", "Diego Fuentes", "Active", "Ben Alvarez"),
    TrialScenario("qualify_expo_lead", "Jonas Berg", "Qualified", "Maya Okafor"),
    TrialScenario("churn_active", "Priya Raman", "Churned", "Priya Shah"),
    TrialScenario("activate_pilot", "Hana Sato", "Active", "Sarah Chen"),
)

VOICE_CASES: tuple[tuple[str, str, dict[str, str] | None], ...] = (
    # (utterance, expected kind, expected inputs when a run command)
    (
        "run update crm lead: set Sarah Chen to Qualified and assign to Priya Shah in CRM A",
        "run_command",
        {"lead_name": "Sarah Chen", "lifecycle_status": "Qualified", "owner_name": "Priya Shah"},
    ),
    (
        "move sarah chen over to qualified, owner is priya shah, in northlight",
        "run_command",
        {"lead_name": "Sarah Chen", "lifecycle_status": "Qualified", "owner_name": "Priya Shah"},
    ),
    (
        "set Diego Fuentes to Active and give it to Ben Alvarez in crm a",
        "run_command",
        {"lead_name": "Diego Fuentes", "lifecycle_status": "Active", "owner_name": "Ben Alvarez"},
    ),
    (
        "qualify Jonas Berg and assign to Maya Okafor in Meridian",
        "run_command",
        {"lead_name": "Jonas Berg", "lifecycle_status": "Qualified", "owner_name": "Maya Okafor"},
    ),
    ("set the lead to qualified", "clarification", None),  # missing record identity
    ("update Sarah Chen in CRM A", "clarification", None),  # missing field values
    ("update the contact record", "clarification", None),  # ambiguous automation reference
    ("set hana sato to active and assign to sarah chen", "clarification", None),  # missing target app
    ("cancel", "cancellation", None),
    ("approve", "approval", None),  # in awaiting-approval context
)


def trials(
    app: AppKey = "a",
    count: int = 5,
    live: bool = False,
    evidence_root: Path = Path("data/execution/evals"),
) -> None:
    """Run the reset-trial matrix for one CRM and record evidence (EVALS §3.7).

    Args:
        app: Target CRM fixture.
        count: Number of scenarios to run (first N of the deterministic set).
        live: Drive the live HoloDesktop adapter instead of the scripted mock.
        evidence_root: Where trial evidence is written.
    """
    os.environ["FOUNDRY_HOLO_MODE"] = "live" if live else "mock"
    os.environ.setdefault("FOUNDRY_HOLO_MOCK_SCRIPT", "stage-ok")

    from fastapi.testclient import TestClient

    from automation_foundry.api import create_app

    settings = ExecutionSettings()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = evidence_root / f"{stamp}-crm_{app}{'-live' if live else '-mock'}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "environment.json").write_text(
        json.dumps(_environment_record(settings), indent=2) + "\n", encoding="utf-8"
    )

    fixture_path = state_path(app, settings.fixture_data_root)
    results: list[dict[str, object]] = []
    with TestClient(create_app()) as client:
        token = client.get("/api/execution/csrf-token").json()["token"]
        headers = {"X-Foundry-Token": token}
        for scenario in SCENARIOS[:count]:
            results.append(_run_trial(client, headers, app, scenario, fixture_path, settings))
            (evidence_dir / "trials.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8"
            )

    passed = sum(1 for row in results if row["exact_success"])
    unchanged_before_approval = all(bool(row["pre_approval_unchanged"]) for row in results)
    summary = {
        "app": f"crm_{app}",
        "mode": "live" if live else "mock",
        "trials": len(results),
        "exact_successes": passed,
        "threshold": f">= {min(4, count)} of {count} (first attempt)",
        "threshold_met": passed >= min(4, count),
        "pre_approval_state_unchanged_in_all_trials": unchanged_before_approval,
        "evidence_dir": str(evidence_dir),
    }
    (evidence_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if not summary["threshold_met"] or not unchanged_before_approval:
        raise SystemExit(1)


def voice_cases() -> None:
    """Run the 10 voice-routing cases (EVALS §3.6); require >= 9 correct."""
    resolver = RuleBasedIntentResolver()
    known = [record.full_name for record in default_seed().records]
    correct = 0
    for utterance, expected_kind, expected_inputs in VOICE_CASES:
        context = VoiceContext(
            awaiting_commit_approval=expected_kind in ("approval", "rejection"),
            known_record_names=known,
        )
        command: ResolvedCommand = resolver.resolve(utterance, context)
        ok = command.kind == expected_kind and (expected_inputs is None or command.inputs == expected_inputs)
        correct += int(ok)
        print(f"{'PASS' if ok else 'FAIL'}  [{command.kind:>13}]  {utterance!r}")
    print(f"\nvoice routing: {correct}/{len(VOICE_CASES)} correct (threshold >= 9)")
    if correct < 9:
        raise SystemExit(1)


def _run_trial(
    client: object,
    headers: dict[str, str],
    app: AppKey,
    scenario: TrialScenario,
    fixture_path: Path,
    settings: ExecutionSettings,
) -> dict[str, object]:
    write_state_atomic(fixture_path, default_seed())
    baseline = fixture_path.read_bytes()
    started = time.monotonic()
    row: dict[str, object] = {"scenario": asdict(scenario), "app": f"crm_{app}"}

    prepared = client.post(  # type: ignore[attr-defined]
        "/api/execution/runs",
        json={
            "target_app": f"crm_{app}",
            "inputs": {
                "lead_name": scenario.lead_name,
                "lifecycle_status": scenario.lifecycle_status,
                "owner_name": scenario.owner_name,
            },
        },
        headers=headers,
    )
    if prepared.status_code != 201:
        return {**row, "exact_success": False, "pre_approval_unchanged": True, "failure_category": "prepare_rejected"}
    run_id = prepared.json()["request"]["id"]
    row["run_id"] = run_id
    client.post(f"/api/execution/runs/{run_id}/confirm-start", headers=headers)  # type: ignore[attr-defined]

    status = _poll(client, run_id, ("awaiting_commit_approval", "failed", "cancelled"), settings)
    pre_approval_unchanged = fixture_path.read_bytes() == baseline
    row["pre_approval_unchanged"] = pre_approval_unchanged
    if status["state"] != "awaiting_commit_approval":
        result = status.get("result") or {}
        assert isinstance(result, dict)
        return {**row, "exact_success": False, "failure_category": str(result.get("error_code", "no_staging"))}

    staged = status["staged_change"]
    assert isinstance(staged, dict)
    client.post(  # type: ignore[attr-defined]
        f"/api/execution/runs/{run_id}/confirm-commit",
        json={"payload_sha256": staged["payload_sha256"], "actor": "eval-harness"},
        headers=headers,
    )
    status = _poll(client, run_id, ("succeeded", "failed", "cancelled"), settings)
    row["duration_seconds"] = round(time.monotonic() - started, 2)
    result = status.get("result") or {}
    assert isinstance(result, dict)
    row["holo_steps"] = result.get("holo_steps", 0)

    exact = status["state"] == "succeeded" and _state_matches_exactly(fixture_path, scenario)
    row["exact_success"] = exact
    if not exact:
        row["failure_category"] = str(result.get("error_code") or f"terminal_{status['state']}")
    return row


def _state_matches_exactly(fixture_path: Path, scenario: TrialScenario) -> bool:
    expected_records = []
    for record in default_seed().records:
        if record.full_name == scenario.lead_name:
            expected_records.append(
                record.model_copy(update={"status": scenario.lifecycle_status, "owner": scenario.owner_name})
            )
        else:
            expected_records.append(record)
    actual = load_state(fixture_path)
    return actual.model_dump() == default_seed().model_copy(update={"records": expected_records}).model_dump()


def _poll(client: object, run_id: str, states: tuple[str, ...], settings: ExecutionSettings) -> dict[str, object]:
    deadline = time.monotonic() + settings.hard_max_time_seconds + settings.approval_timeout_seconds + 30
    while True:
        status = client.get(f"/api/execution/runs/{run_id}").json()  # type: ignore[attr-defined]
        if status["state"] in states:
            return dict(status)
        if time.monotonic() > deadline:
            return dict(status)
        time.sleep(0.1 if os.environ.get("FOUNDRY_HOLO_MODE") == "mock" else 1.0)


def _environment_record(settings: ExecutionSettings) -> dict[str, object]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        commit = "unknown"
    return {
        "commit": commit,
        "timestamp": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "holo_mode": settings.holo_mode,
        "bundle_path": str(settings.bundle_path),
        "approval_timeout_seconds": settings.approval_timeout_seconds,
        "budgets": {"hard_max_steps": settings.hard_max_steps, "hard_max_time_seconds": settings.hard_max_time_seconds},
    }


def main() -> None:
    """Dispatch the execution-eval CLI."""
    logging.basicConfig(level=logging.WARNING)
    tyro.extras.subcommand_cli_from_dict({"trials": trials, "voice-cases": voice_cases})


if __name__ == "__main__":
    main()
