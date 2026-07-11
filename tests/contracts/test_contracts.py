"""Tests for shared authoring/execution contracts."""

import hashlib
import json
import unittest
from itertools import pairwise
from pathlib import Path

from pydantic import ValidationError

from automation_foundry.contracts import (
    ApprovedBundle,
    AutomationManifest,
    RunState,
    require_run_transition,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "approved_bundle_v1"


class ApprovedBundleFixtureTests(unittest.TestCase):
    """Prove the fixed Member 2 handoff is valid and immutable."""

    def test_fixture_parses_as_approved_bundle(self) -> None:
        bundle = ApprovedBundle.model_validate_json((FIXTURE_ROOT / "approved_bundle.json").read_text())

        self.assertEqual(bundle.manifest.approved_version, 1)
        self.assertTrue(bundle.version.is_approved)
        self.assertEqual(bundle.manifest.slug, "update-crm-lead")

    def test_artifact_hashes_match_fixture_files(self) -> None:
        bundle = ApprovedBundle.model_validate_json((FIXTURE_ROOT / "approved_bundle.json").read_text())

        for artifact in bundle.version.artifacts:
            content = (FIXTURE_ROOT / artifact.relative_path).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), artifact.sha256, artifact.name)

    def test_manifest_file_matches_embedded_manifest(self) -> None:
        bundle = ApprovedBundle.model_validate_json((FIXTURE_ROOT / "approved_bundle.json").read_text())
        manifest = AutomationManifest.model_validate_json((FIXTURE_ROOT / "manifest.json").read_text())

        self.assertEqual(manifest, bundle.manifest)

    def test_unknown_contract_fields_are_rejected(self) -> None:
        data = json.loads((FIXTURE_ROOT / "manifest.json").read_text())
        data["unexpected"] = True

        with self.assertRaises(ValidationError):
            AutomationManifest.model_validate(data)


class RunTransitionTests(unittest.TestCase):
    """Protect the mandatory start and commit approval gates."""

    def test_happy_path_transitions_are_allowed(self) -> None:
        states = (
            RunState.PREPARED,
            RunState.AWAITING_START_CONFIRMATION,
            RunState.EXECUTING,
            RunState.AWAITING_COMMIT_APPROVAL,
            RunState.COMMITTING,
            RunState.SUCCEEDED,
        )

        for current, target in pairwise(states):
            require_run_transition(current, target)

    def test_execution_cannot_skip_commit_approval(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid run transition"):
            require_run_transition(RunState.EXECUTING, RunState.COMMITTING)

    def test_terminal_state_cannot_restart(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid run transition"):
            require_run_transition(RunState.CANCELLED, RunState.EXECUTING)


if __name__ == "__main__":
    unittest.main()
