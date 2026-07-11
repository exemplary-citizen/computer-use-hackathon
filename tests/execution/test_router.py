"""Execution API tests: authorization-by-state, origin/token guards, replay."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi.testclient import TestClient

from tests.execution.helpers import CANONICAL_INPUTS, make_settings

_ENV_KEYS = (
    "FOUNDRY_HOLO_MODE",
    "FOUNDRY_HOLO_MOCK_SCRIPT",
    "FOUNDRY_APPROVAL_TIMEOUT_SECONDS",
    "FOUNDRY_HEARTBEAT_SECONDS",
    "FOUNDRY_BUNDLE_PATH",
    "FOUNDRY_RUNS_ROOT",
    "FOUNDRY_EXECUTION_DATABASE_PATH",
    "FOUNDRY_FIXTURE_DATA_ROOT",
)


class RouterTestBase(unittest.TestCase):
    approval_timeout = "5"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        settings = make_settings(tmp)  # builds bundle + fixture files
        env = {
            "FOUNDRY_HOLO_MODE": "mock",
            "FOUNDRY_HOLO_MOCK_SCRIPT": "stage-ok",
            "FOUNDRY_APPROVAL_TIMEOUT_SECONDS": self.approval_timeout,
            "FOUNDRY_HEARTBEAT_SECONDS": "0.05",
            "FOUNDRY_BUNDLE_PATH": str(settings.bundle_path),
            "FOUNDRY_RUNS_ROOT": str(settings.runs_root),
            "FOUNDRY_EXECUTION_DATABASE_PATH": str(settings.execution_database_path),
            "FOUNDRY_FIXTURE_DATA_ROOT": str(settings.fixture_data_root),
        }
        self._saved = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ.update(env)
        self.addCleanup(self._restore_env)

        from automation_foundry.api import create_app

        # Context-managed client: one persistent event loop so background
        # execution tasks survive across requests.
        self.client = TestClient(create_app()).__enter__()
        self.addCleanup(lambda: TestClient.__exit__(self.client, None, None, None))
        self.token = self.client.get("/api/execution/csrf-token").json()["token"]

    def _restore_env(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def mutate(self, path: str, json_body: dict[str, object] | None = None, **headers: str) -> httpx.Response:
        response: httpx.Response = self.client.post(
            path, json=json_body, headers={"X-Foundry-Token": self.token, **headers}
        )
        return response

    def prepare_run(self) -> tuple[str, dict[str, object]]:
        response = self.mutate("/api/execution/runs", {"target_app": "CRM A", "inputs": dict(CANONICAL_INPUTS)})
        assert response.status_code == 201, response.text
        preview = response.json()
        return preview["request"]["id"], preview

    def wait_for_state(self, run_id: str, *states: str, timeout: float = 5.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            status = self.client.get(f"/api/execution/runs/{run_id}").json()
            if status["state"] in states:
                return dict(status)
            if time.monotonic() > deadline:
                raise AssertionError(f"run stuck in {status['state']}")
            time.sleep(0.02)


class GuardTests(RouterTestBase):
    def test_mutation_without_token_is_rejected(self) -> None:
        response = self.client.post(
            "/api/execution/runs", json={"target_app": "CRM A", "inputs": dict(CANONICAL_INPUTS)}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "missing_token")

    def test_cross_origin_mutation_is_rejected(self) -> None:
        response = self.client.post(
            "/api/execution/runs",
            json={"target_app": "CRM A", "inputs": dict(CANONICAL_INPUTS)},
            headers={"Origin": "https://evil.example", "X-Foundry-Token": self.token},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error_code"], "forbidden_origin")

    def test_cross_origin_token_fetch_is_rejected(self) -> None:
        response = self.client.get("/api/execution/csrf-token", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_local_origin_is_accepted(self) -> None:
        response = self.client.post(
            "/api/execution/runs",
            json={"target_app": "CRM A", "inputs": dict(CANONICAL_INPUTS)},
            headers={"Origin": "http://localhost:5173", "X-Foundry-Token": self.token},
        )
        self.assertEqual(response.status_code, 201)


class LifecycleTests(RouterTestBase):
    def test_validation_errors_are_field_level(self) -> None:
        response = self.mutate("/api/execution/runs", {"target_app": "CRM Z", "inputs": {"lead_name": ""}})
        self.assertEqual(response.status_code, 422)
        errors = response.json()["field_errors"]
        self.assertIn("target_app", errors)
        self.assertIn("lifecycle_status", errors)

    def test_full_lifecycle_over_the_public_contract(self) -> None:
        run_id, _ = self.prepare_run()
        self.assertEqual(self.mutate(f"/api/execution/runs/{run_id}/confirm-start").status_code, 200)
        status = self.wait_for_state(run_id, "awaiting_commit_approval")
        staged = status["staged_change"]
        assert isinstance(staged, dict)
        response = self.mutate(
            f"/api/execution/runs/{run_id}/confirm-commit",
            {"payload_sha256": staged["payload_sha256"], "actor": "router-test"},
        )
        self.assertEqual(response.status_code, 200)
        final = self.wait_for_state(run_id, "succeeded", "failed", "cancelled")
        self.assertEqual(final["state"], "succeeded")

    def test_approve_in_wrong_state_is_conflict(self) -> None:
        run_id, _ = self.prepare_run()
        response = self.mutate(f"/api/execution/runs/{run_id}/confirm-commit", {"payload_sha256": "0" * 64})
        self.assertEqual(response.status_code, 409)

    def test_second_concurrent_start_is_run_conflict(self) -> None:
        first, _ = self.prepare_run()
        self.mutate(f"/api/execution/runs/{first}/confirm-start")
        self.wait_for_state(first, "awaiting_commit_approval")
        second, _ = self.prepare_run()
        response = self.mutate(f"/api/execution/runs/{second}/confirm-start")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error_code"], "run_conflict")
        self.mutate(f"/api/execution/runs/{first}/cancel")

    def test_reject_then_status_shows_cancelled_and_nothing_saved(self) -> None:
        run_id, _ = self.prepare_run()
        self.mutate(f"/api/execution/runs/{run_id}/confirm-start")
        self.wait_for_state(run_id, "awaiting_commit_approval")
        self.mutate(f"/api/execution/runs/{run_id}/reject-commit", {})
        final = self.wait_for_state(run_id, "cancelled")
        result = final["result"]
        assert isinstance(result, dict)
        self.assertIn("nothing was saved", str(result["answer"]).lower())

    def test_unknown_run_is_404(self) -> None:
        response = self.client.get("/api/execution/runs/00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 404)


class EventStreamTests(RouterTestBase):
    def test_events_replay_after_sequence(self) -> None:
        run_id, _ = self.prepare_run()
        events = self.client.get(f"/api/execution/runs/{run_id}/events").json()["events"]
        self.assertGreaterEqual(len(events), 2)
        sequences = [event["sequence"] for event in events]
        self.assertEqual(sequences, sorted(sequences))
        tail = self.client.get(f"/api/execution/runs/{run_id}/events", params={"after_seq": sequences[0]}).json()
        self.assertEqual(tail["events"][0]["sequence"], sequences[1])

    def test_websocket_replays_then_streams(self) -> None:
        run_id, _ = self.prepare_run()
        with self.client.websocket_connect(f"/api/execution/runs/{run_id}/events/stream?since_seq=-1") as ws:
            import json as jsonlib

            first = jsonlib.loads(ws.receive_text())
            self.assertEqual(first["sequence"], 0)
            self.assertEqual(first["event_type"], "run_prepared")

    def test_websocket_rejects_cross_origin(self) -> None:
        run_id, _ = self.prepare_run()
        with self.assertRaises(Exception):
            with self.client.websocket_connect(
                f"/api/execution/runs/{run_id}/events/stream",
                headers={"Origin": "https://evil.example"},
            ) as ws:
                ws.receive_text()


if __name__ == "__main__":
    unittest.main()
