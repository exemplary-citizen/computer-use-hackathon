"""State-machine safety tests: every terminal path deterministic and fail-closed."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from desktop_fixtures.store import load_state, state_path

from automation_foundry.contracts import InvocationSource, RunState
from automation_foundry.contracts.transitions import require_run_transition
from automation_foundry.execution.errors import ExecutionFault
from automation_foundry.execution.holo import HoloTaskSpec, ScriptedFakeHolo
from automation_foundry.execution.machine import InputValidationError, RunCoordinator

from tests.execution.helpers import CANONICAL_INPUTS, make_settings, wait_for_state


class MachineTestBase(unittest.IsolatedAsyncioTestCase):
    script = "stage-ok"
    approval_timeout = 1.0

    async def asyncSetUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.settings = make_settings(self.tmp, script=self.script, approval_timeout=self.approval_timeout)
        self.coordinator = RunCoordinator(self.settings)
        await self.coordinator.startup()
        self.fixture_a = state_path("a", self.settings.fixture_data_root)
        self.baseline_a = self.fixture_a.read_bytes()

    async def start_run(self, target_app: str = "CRM A") -> UUID:
        preview = await self.coordinator.prepare(target_app, dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
        await self.coordinator.confirm_start(preview.request.id)
        return preview.request.id

    async def staged_run(self) -> UUID:
        run_id = await self.start_run()
        await wait_for_state(self.coordinator, run_id, RunState.AWAITING_COMMIT_APPROVAL)
        return run_id

    def staged_hash(self, run_id: UUID) -> str:
        staged = self.coordinator.staged_change(run_id)
        assert staged is not None
        return staged.payload_sha256

    async def finish(self, run_id: UUID) -> str:
        return await wait_for_state(self.coordinator, run_id, RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED)

    def assert_fixture_unchanged(self) -> None:
        self.assertEqual(self.fixture_a.read_bytes(), self.baseline_a)

    def result_error_code(self, run_id: UUID) -> str | None:
        result = self.coordinator.get_status(run_id)["result"]
        assert isinstance(result, dict)
        code = result["error_code"]
        return str(code) if code is not None else None


class HappyPathTests(MachineTestBase):
    async def test_live_run_ensures_target_fixture_is_running_before_holo(self) -> None:
        calls: list[tuple[str, Path | None, float]] = []
        task_texts: list[str] = []
        task_specs: list[HoloTaskSpec] = []
        settings = self.settings.model_copy(update={"holo_mode": "live"})

        def launch(app, data_root, wait_seconds):
            calls.append((app, data_root, wait_seconds))
            return True

        def adapter(spec):
            task_texts.append(spec.task_text)
            task_specs.append(spec)
            return ScriptedFakeHolo(
                spec=spec,
                script="stage-ok",
                data_root=settings.fixture_data_root,
            )

        coordinator = RunCoordinator(
            settings,
            adapter_factory=adapter,
            fixture_launcher=launch,
        )
        await coordinator.startup()
        preview = await coordinator.prepare("CRM A", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
        await coordinator.confirm_start(preview.request.id)
        await wait_for_state(coordinator, preview.request.id, RunState.AWAITING_COMMIT_APPROVAL)

        self.assertEqual(calls, [("a", settings.fixture_data_root, settings.fixture_launch_wait_seconds)])
        self.assertIn('visible window titled "Northlight CRM"', task_texts[0])
        self.assertIn("Do not open Spotlight", task_texts[0])
        self.assertIn("Click the exact matching name or row", task_texts[0])
        self.assertIn("Open Record, Edit, or View Details", task_texts[0])
        self.assertIn('Verify the opened editor still belongs to "Sarah Chen"', task_texts[0])
        stage_prompt = coordinator._stage_prompt(task_specs[0])
        self.assertIn("leave the editor visibly open with the unsaved staged values", stage_prompt)
        self.assertIn("do not press Escape", stage_prompt)
        self.assertIn("switch or minimize applications", stage_prompt)
        self.assertIn("Immediately return control", stage_prompt)
        events = coordinator.events.replay(preview.request.id)
        self.assertTrue(any(event.event_type == "target_app_ready" for event in events))
        await coordinator.cancel(preview.request.id)

    async def test_full_stage_approve_commit_flow(self) -> None:
        run_id = await self.staged_run()
        self.assert_fixture_unchanged()  # nothing persisted before approval
        staged = self.coordinator.staged_change(run_id)
        assert staged is not None
        self.assertEqual(staged.record_identity, "Sarah Chen")
        self.assertEqual(
            {change.field: (change.before, change.after) for change in staged.changes},
            {"status": ("Lead", "Qualified"), "owner": ("Unassigned", "Priya Shah")},
        )
        await self.coordinator.approve_commit(run_id, staged.payload_sha256, InvocationSource.DASHBOARD, "tester")
        self.assertEqual(await self.finish(run_id), "succeeded")
        state = load_state(self.fixture_a)
        sarah = next(record for record in state.records if record.full_name == "Sarah Chen")
        self.assertEqual((sarah.status, sarah.owner), ("Qualified", "Priya Shah"))
        self.assertEqual(sarah.notes, "Canonical eval-case lead (approved_bundle_v1).")  # untouched field

    async def test_events_have_monotonic_sequences_and_run_files_exist(self) -> None:
        run_id = await self.staged_run()
        await self.coordinator.approve_commit(run_id, self.staged_hash(run_id), InvocationSource.VOICE, "tester")
        await self.finish(run_id)
        events = self.coordinator.events.replay(run_id)
        self.assertEqual([event.sequence for event in events], list(range(len(events))))
        run_dir = self.settings.runs_root / str(run_id)
        for name in ("request.json", "events.jsonl", "staged_change.json", "approval.json", "result.json"):
            self.assertTrue((run_dir / name).is_file(), name)

    async def test_double_approve_is_idempotent_single_commit(self) -> None:
        run_id = await self.staged_run()
        digest = self.staged_hash(run_id)
        await self.coordinator.approve_commit(run_id, digest, InvocationSource.DASHBOARD, "tester")
        await self.coordinator.approve_commit(run_id, digest, InvocationSource.VOICE, "tester-2")
        self.assertEqual(await self.finish(run_id), "succeeded")
        state = load_state(self.fixture_a)
        sarah = next(record for record in state.records if record.full_name == "Sarah Chen")
        self.assertEqual(sarah.status, "Qualified")

    async def test_create_contact_stages_then_adds_exactly_one_record_after_approval(self) -> None:
        bundle_path = self.settings.bundle_path
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["input_schema"] = {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "company": {"type": "string"},
                "lifecycle_status": {"type": "string"},
                "owner_name": {"type": "string"},
            },
            "required": ["first_name", "last_name"],
            "additionalProperties": False,
        }
        bundle["version"]["inputs"] = [
            {
                "name": name,
                "json_type": "string",
                "description": name.replace("_", " "),
                "required": name in {"first_name", "last_name"},
                "default": None,
                "examples": [],
            }
            for name in ("first_name", "last_name", "company", "lifecycle_status", "owner_name")
        ]
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        inputs = {
            "first_name": "Amina",
            "last_name": "Diallo",
            "company": "Sunbird Labs",
            "lifecycle_status": "Qualified",
            "owner_name": "Priya Shah",
        }
        preview = await self.coordinator.prepare("CRM A", inputs, InvocationSource.DASHBOARD)
        baseline = load_state(self.fixture_a)
        await self.coordinator.confirm_start(preview.request.id)
        await wait_for_state(self.coordinator, preview.request.id, RunState.AWAITING_COMMIT_APPROVAL)
        self.assertEqual(load_state(self.fixture_a), baseline)
        staged = self.coordinator.staged_change(preview.request.id)
        assert staged is not None
        self.assertTrue(all(change.before is None for change in staged.changes))
        await self.coordinator.approve_commit(
            preview.request.id,
            staged.payload_sha256,
            InvocationSource.DASHBOARD,
            "tester",
        )
        self.assertEqual(await self.finish(preview.request.id), "succeeded")
        state = load_state(self.fixture_a)
        self.assertEqual(state.records[:-1], baseline.records)
        self.assertEqual(state.records[-1].full_name, "Amina Diallo")
        self.assertEqual(state.records[-1].company, "Sunbird Labs")


class ApprovalSafetyTests(MachineTestBase):
    approval_timeout = 0.25

    async def test_reject_cancels_without_saving(self) -> None:
        run_id = await self.staged_run()
        await self.coordinator.reject_commit(run_id, InvocationSource.DASHBOARD, "tester")
        self.assertEqual(await self.finish(run_id), "cancelled")
        self.assert_fixture_unchanged()

    async def test_approval_timeout_cancels_without_saving(self) -> None:
        run_id = await self.staged_run()
        self.assertEqual(await self.finish(run_id), "cancelled")
        self.assert_fixture_unchanged()
        with self.assertRaises(ExecutionFault) as caught:
            await self.coordinator.approve_commit(run_id, "0" * 64, InvocationSource.DASHBOARD, "late")
        self.assertEqual(caught.exception.spec.code, "stale_approval")

    async def test_wrong_hash_is_refused_and_run_still_awaiting(self) -> None:
        run_id = await self.staged_run()
        with self.assertRaises(ExecutionFault) as caught:
            await self.coordinator.approve_commit(run_id, "f" * 64, InvocationSource.DASHBOARD, "tester")
        self.assertEqual(caught.exception.spec.code, "approval_hash_mismatch")
        self.assertEqual(self.coordinator.get_status(run_id)["state"], "awaiting_commit_approval")

    async def test_cancel_during_awaiting_approval(self) -> None:
        run_id = await self.staged_run()
        await self.coordinator.cancel(run_id)
        self.assertEqual(await self.finish(run_id), "cancelled")
        self.assert_fixture_unchanged()


class AdversarialScriptTests(unittest.IsolatedAsyncioTestCase):
    async def run_script(self, script: str) -> tuple[RunCoordinator, UUID, str, bytes, Path]:
        tmp_holder = TemporaryDirectory()
        self.addCleanup(tmp_holder.cleanup)
        tmp = Path(tmp_holder.name)
        settings = make_settings(tmp, script=script, approval_timeout=0.5)
        coordinator = RunCoordinator(settings)
        await coordinator.startup()
        fixture = state_path("a", settings.fixture_data_root)
        baseline = fixture.read_bytes()
        preview = await coordinator.prepare("crm_a", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
        await coordinator.confirm_start(preview.request.id)
        state = await wait_for_state(
            coordinator,
            preview.request.id,
            RunState.AWAITING_COMMIT_APPROVAL,
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.CANCELLED,
        )
        return coordinator, preview.request.id, state, baseline, fixture

    async def test_stage_commits_anyway_fails_before_any_approval_prompt(self) -> None:
        coordinator, run_id, state, _, _ = await self.run_script("stage-commits-anyway")
        self.assertEqual(state, "failed")
        result = coordinator.get_status(run_id)["result"]
        assert isinstance(result, dict)
        self.assertEqual(result["error_code"], "unsafe_stage")
        event_types = [event.event_type for event in coordinator.events.replay(run_id)]
        self.assertNotIn("staged_change", event_types)  # approval prompt never shown

    async def test_malformed_answer_fails_closed(self) -> None:
        coordinator, run_id, state, baseline, fixture = await self.run_script("malformed-answer")
        self.assertEqual(state, "failed")
        result = coordinator.get_status(run_id)["result"]
        assert isinstance(result, dict)
        self.assertEqual(result["error_code"], "malformed_stage_answer")
        self.assertEqual(fixture.read_bytes(), baseline)

    async def test_session_dies_during_await_fails_closed_on_approve(self) -> None:
        coordinator, run_id, state, baseline, fixture = await self.run_script("dies-during-await")
        self.assertEqual(state, "awaiting_commit_approval")
        staged = coordinator.staged_change(run_id)
        assert staged is not None
        await coordinator.approve_commit(run_id, staged.payload_sha256, InvocationSource.DASHBOARD, "tester")
        final = await wait_for_state(coordinator, run_id, RunState.CANCELLED, RunState.FAILED)
        self.assertEqual(final, "cancelled")  # stale_session fail-closed: no new session, no commit
        self.assertEqual(fixture.read_bytes(), baseline)

    async def test_session_dies_during_commit_fails_without_retry(self) -> None:
        coordinator, run_id, state, baseline, fixture = await self.run_script("dies-during-commit")
        self.assertEqual(state, "awaiting_commit_approval")
        staged = coordinator.staged_change(run_id)
        assert staged is not None
        await coordinator.approve_commit(run_id, staged.payload_sha256, InvocationSource.DASHBOARD, "tester")
        final = await wait_for_state(coordinator, run_id, RunState.CANCELLED, RunState.FAILED)
        self.assertEqual(final, "failed")
        result = coordinator.get_status(run_id)["result"]
        assert isinstance(result, dict)
        self.assertEqual(result["error_code"], "session_lost")
        self.assertEqual(fixture.read_bytes(), baseline)

    async def test_commit_wrong_value_fails_verification(self) -> None:
        coordinator, run_id, _, _, _ = await self.run_script("commit-wrong-value")
        staged = coordinator.staged_change(run_id)
        assert staged is not None
        await coordinator.approve_commit(run_id, staged.payload_sha256, InvocationSource.DASHBOARD, "tester")
        await wait_for_state(coordinator, run_id, RunState.FAILED)
        result = coordinator.get_status(run_id)["result"]
        assert isinstance(result, dict)
        self.assertEqual(result["error_code"], "commit_verify_failed")

    async def test_budget_exhaustion_fails_without_commit(self) -> None:
        coordinator, run_id, state, baseline, fixture = await self.run_script("timeout")
        self.assertEqual(state, "failed")
        result = coordinator.get_status(run_id)["result"]
        assert isinstance(result, dict)
        self.assertEqual(result["error_code"], "budget_exceeded")
        self.assertEqual(fixture.read_bytes(), baseline)

    async def test_wrong_app_fails_safely(self) -> None:
        coordinator, run_id, state, baseline, fixture = await self.run_script("wrong-app")
        self.assertEqual(state, "failed")
        result = coordinator.get_status(run_id)["result"]
        assert isinstance(result, dict)
        self.assertEqual(result["error_code"], "wrong_app_state")
        self.assertEqual(fixture.read_bytes(), baseline)


class GuardsAndValidationTests(MachineTestBase):
    async def test_single_active_run_enforced(self) -> None:
        first = await self.staged_run()
        preview = await self.coordinator.prepare("crm_b", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
        with self.assertRaises(ExecutionFault) as caught:
            await self.coordinator.confirm_start(preview.request.id)
        self.assertEqual(caught.exception.spec.code, "run_conflict")
        await self.coordinator.cancel(first)

    async def test_field_level_validation_errors(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            await self.coordinator.prepare(
                "CRM Z", {"lead_name": "", "bogus": "x", "lifecycle_status": "Qualified"}, InvocationSource.DASHBOARD
            )
        errors = caught.exception.field_errors
        self.assertIn("target_app", errors)
        self.assertIn("lead_name", errors)
        self.assertIn("bogus", errors)
        self.assertIn("owner_name", errors)

    async def test_unknown_record_is_a_field_error(self) -> None:
        with self.assertRaises(InputValidationError) as caught:
            await self.coordinator.prepare(
                "crm_a", dict(CANONICAL_INPUTS, lead_name="Nobody Here"), InvocationSource.DASHBOARD
            )
        self.assertIn("lead_name", caught.exception.field_errors)

    async def test_tampered_bundle_refused_at_prepare(self) -> None:
        skill = self.settings.bundle_path.parent / "versions" / "1" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\ninjected\n", encoding="utf-8")
        with self.assertRaises(ExecutionFault) as caught:
            await self.coordinator.prepare("crm_a", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
        self.assertEqual(caught.exception.spec.code, "hash_mismatch")

    async def test_illegal_transition_rejected_by_contract(self) -> None:
        with self.assertRaises(ValueError):
            require_run_transition(RunState.EXECUTING, RunState.COMMITTING)
        with self.assertRaises(ValueError):
            require_run_transition(RunState.SUCCEEDED, RunState.EXECUTING)


class RestartReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_marks_committing_as_commit_state_unknown(self) -> None:
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            settings = make_settings(tmp)
            coordinator = RunCoordinator(settings)
            await coordinator.startup()
            preview = await coordinator.prepare("crm_a", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
            run_id = preview.request.id
            with sqlite3.connect(settings.execution_database_path) as connection:
                connection.execute("UPDATE runs SET state = 'committing', is_active = 1 WHERE id = ?", (str(run_id),))
            reborn = RunCoordinator(settings)
            await reborn.startup()
            status = reborn.get_status(run_id)
            self.assertEqual(status["state"], "failed")
            result = status["result"]
            assert isinstance(result, dict)
            self.assertEqual(result["error_code"], "commit_state_unknown")

    async def test_restart_marks_other_active_runs_interrupted(self) -> None:
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            settings = make_settings(tmp)
            coordinator = RunCoordinator(settings)
            await coordinator.startup()
            preview = await coordinator.prepare("crm_a", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
            run_id = preview.request.id
            with sqlite3.connect(settings.execution_database_path) as connection:
                connection.execute("UPDATE runs SET state = 'executing', is_active = 1 WHERE id = ?", (str(run_id),))
            reborn = RunCoordinator(settings)
            await reborn.startup()
            result = reborn.get_status(run_id)["result"]
            assert isinstance(result, dict)
            self.assertEqual(result["error_code"], "interrupted_restart")

    async def test_force_release_frees_the_single_run_slot(self) -> None:
        with TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            settings = make_settings(tmp, approval_timeout=30.0)
            coordinator = RunCoordinator(settings)
            await coordinator.startup()
            preview = await coordinator.prepare("crm_a", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
            await coordinator.confirm_start(preview.request.id)
            await wait_for_state(coordinator, preview.request.id, RunState.AWAITING_COMMIT_APPROVAL)
            released = await coordinator.force_release()
            self.assertEqual(released, [preview.request.id])
            second = await coordinator.prepare("crm_a", dict(CANONICAL_INPUTS), InvocationSource.DASHBOARD)
            await coordinator.confirm_start(second.request.id)  # slot is free again
            await coordinator.cancel(second.request.id)


if __name__ == "__main__":
    unittest.main()
