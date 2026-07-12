"""Shared builders for execution-lane tests.

``make_valid_bundle`` copies the repo's fixed bundle fixture into a temp
directory and rewrites its hashes so verification passes regardless of any
drift in the committed fixture (the committed copy currently has one stale
artifact hash — reported to Member 1).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

from desktop_fixtures.store import default_seed, state_path, write_state_atomic

from automation_foundry.contracts import RunState
from automation_foundry.execution.config import ExecutionSettings
from automation_foundry.execution.machine import RunCoordinator

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "approved_bundle_v1"

CANONICAL_INPUTS: dict[str, object] = {
    "lead_name": "Sarah Chen",
    "lifecycle_status": "Qualified",
    "owner_name": "Priya Shah",
}


def make_valid_bundle(target_dir: Path) -> Path:
    """Copy the fixed bundle fixture with internally consistent hashes.

    Args:
        target_dir: Temp directory to copy into.

    Returns:
        Path to the rewritten ``approved_bundle.json``.
    """
    root = target_dir / "approved_bundle_v1"
    shutil.copytree(BUNDLE_FIXTURE, root)
    bundle_path = root / "approved_bundle.json"
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    for artifact in data["version"]["artifacts"]:
        file_path = root / artifact["relative_path"]
        artifact["sha256"] = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if artifact["name"] == "skill":
            data["skill_markdown"] = file_path.read_text(encoding="utf-8")
        if artifact["name"] == "sop":
            data["sop_markdown"] = file_path.read_text(encoding="utf-8")
    payload = [
        {"name": artifact["name"], "path": artifact["relative_path"], "sha256": artifact["sha256"]}
        for artifact in sorted(data["version"]["artifacts"], key=lambda item: item["name"])
    ]
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    data["version"]["approval"]["payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    bundle_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return bundle_path


def make_generic_bundle(target_dir: Path) -> Path:
    """Create a hash-valid learned text-entry bundle for coordinator tests.

    Args:
        target_dir: Temp directory to copy into.

    Returns:
        Path to the rewritten generic ``approved_bundle.json``.
    """
    bundle_path = make_valid_bundle(target_dir)
    data = json.loads(bundle_path.read_text(encoding="utf-8"))
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"greeting_text": {"type": "string", "minLength": 1}},
        "required": ["greeting_text"],
    }
    data["input_schema"] = schema
    data["version"]["inputs"] = [
        {
            "name": "greeting_text",
            "json_type": "string",
            "description": "Exact text to type.",
            "required": True,
            "default": None,
            "examples": ["Hello from Foundry"],
        }
    ]
    inference = {
        "source_id": None,
        "source_type": "inference",
        "timestamp_seconds": None,
        "page": None,
        "section": None,
        "excerpt": None,
        "frame_path": None,
        "inference_reason": "Synthetic generic execution test.",
    }
    data["version"]["steps"] = [
        {
            "id": "open_editor",
            "instruction": "Open TextEdit and create a blank unsaved document.",
            "critical": True,
            "persistent_action": False,
            "requires_confirmation_before": False,
            "evidence": [inference],
        },
        {
            "id": "type_text",
            "instruction": "Type the exact greeting_text value into the document.",
            "critical": True,
            "persistent_action": True,
            "requires_confirmation_before": True,
            "evidence": [inference],
        },
    ]
    root = bundle_path.parent
    for artifact in data["version"]["artifacts"]:
        if artifact["name"] == "input_schema":
            (root / artifact["relative_path"]).write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        artifact["sha256"] = hashlib.sha256((root / artifact["relative_path"]).read_bytes()).hexdigest()
    payload = [
        {"name": artifact["name"], "path": artifact["relative_path"], "sha256": artifact["sha256"]}
        for artifact in sorted(data["version"]["artifacts"], key=lambda item: item["name"])
    ]
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    data["version"]["approval"]["payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    bundle_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return bundle_path


def make_settings(tmp: Path, script: str = "stage-ok", approval_timeout: float = 1.0) -> ExecutionSettings:
    """Build isolated execution settings over a temp directory.

    Args:
        tmp: Temp directory owning every path the coordinator touches.
        script: Scripted-fake behavior for this coordinator.
        approval_timeout: Short approval window for timeout tests.
    """
    fixtures_root = tmp / "fixtures"
    for app in ("a", "b"):
        write_state_atomic(state_path(app, fixtures_root), default_seed())
    return ExecutionSettings(
        holo_mode="mock",
        holo_mock_script=script,
        approval_timeout_seconds=approval_timeout,
        heartbeat_seconds=0.05,
        bundle_path=make_valid_bundle(tmp),
        runs_root=tmp / "runs",
        execution_database_path=tmp / "execution.sqlite3",
        fixture_data_root=fixtures_root,
    )


async def wait_for_state(coordinator: RunCoordinator, run_id: object, *states: RunState, timeout: float = 5.0) -> str:
    """Poll a run until it reaches one of the given states.

    Args:
        coordinator: Coordinator under test.
        run_id: Run identifier.
        states: Acceptable states to stop at.
        timeout: Max seconds to wait.

    Returns:
        The state value reached.

    Raises:
        AssertionError: If the timeout elapses first.
    """
    from uuid import UUID

    expected = {state.value for state in states}
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        status = coordinator.get_status(UUID(str(run_id)))
        if status["state"] in expected:
            return str(status["state"])
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"run stuck in {status['state']}, wanted one of {sorted(expected)}")
        await asyncio.sleep(0.01)
