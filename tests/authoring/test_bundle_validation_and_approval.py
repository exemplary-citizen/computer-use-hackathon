"""Generated bundle security, versioning, and approval tests."""

import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from automation_foundry.authoring.bundles import BundleManagerConfig
from automation_foundry.authoring.generation import DraftTool, GeneratedBundleDraft
from automation_foundry.authoring.tool_testing import SandboxToolTestRunner
from automation_foundry.authoring.validation import GeneratedToolValidator
from automation_foundry.authoring.workspace import WorkspaceConfig
from automation_foundry.contracts import (
    AutomationStatus,
    ConflictSeverity,
    EvidenceReference,
    EvidenceSourceType,
    ProcedureCheck,
    ProcedureStep,
    ReviewConflict,
)
from automation_foundry.execution.bundles import load_verified_bundle
from automation_foundry.storage import ArtifactStoreConfig


SAFE_SKILL = """---
description: Update a CRM record and stop for human review before saving.
---

# Update record

Find the visible record, stage the requested fields, and stop before Save. Wait for explicit approval,
then re-check the record and commit the change.
"""


def valid_draft(*, tools_code: str = "", with_tool: bool = False) -> GeneratedBundleDraft:
    tools = []
    tests = ""
    if with_tool:
        tools = [
            DraftTool(
                name="normalize_name",
                description="Normalize a visible record name.",
                entrypoint="normalize_name",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )
        ]
        tests = "def test_normalize_name():\n    assert normalize_name({'name': ' A '}) == {'name': 'A'}"
    return GeneratedBundleDraft(
        sop_markdown="# Update record\n\nStage changes and obtain approval before Save.",
        skill_markdown=SAFE_SKILL,
        inputs=[],
        input_schema={"type": "object", "properties": {}, "required": []},
        steps=[
            ProcedureStep(
                id="stage_update",
                instruction="Stage the requested fields and verify the visible record.",
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.INFERENCE,
                        inference_reason="Test fixture represents reviewer-confirmed workflow evidence.",
                    )
                ],
            ),
            ProcedureStep(
                id="commit_update",
                instruction="After explicit approval, save the staged update once.",
                persistent_action=True,
                requires_confirmation_before=True,
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.INFERENCE,
                        inference_reason="Test fixture represents the annotated confirmation boundary.",
                    )
                ],
            ),
        ],
        preconditions=[
            ProcedureCheck(
                id="record_exists",
                description="The target record exists exactly once.",
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.INFERENCE,
                        inference_reason="Test fixture precondition confirmed by its reviewer.",
                    )
                ],
            )
        ],
        completion_checks=[
            ProcedureCheck(
                id="visible_success",
                description="The requested values and visible success state are present.",
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.INFERENCE,
                        inference_reason="Test fixture completion check confirmed by its reviewer.",
                    )
                ],
            )
        ],
        tools_code=tools_code,
        tools=tools,
        tool_tests_code=tests,
        eval_cases=[],
        conflicts=[],
    )


class GeneratedToolPolicyTests(unittest.TestCase):
    """Reject common generated-code escape capabilities statically."""

    def test_safe_pure_data_tool_is_allowed(self) -> None:
        code = "def normalize_name(payload):\n    return {'name': ' '.join(payload['name'].split())}\n"
        self.assertEqual(GeneratedToolValidator().validate(code, {"normalize_name"}), [])

    def test_adversarial_capabilities_are_rejected(self) -> None:
        cases = {
            "import os\ndef run(payload): return payload": "Forbidden import",
            "import subprocess\ndef run(payload): return payload": "Forbidden import",
            "import socket\ndef run(payload): return payload": "Forbidden import",
            "def run(payload): return open('/etc/passwd').read()": "Forbidden call",
            "def run(payload): return eval(payload['code'])": "Forbidden call",
            "def run(payload): return (1).__class__.__mro__": "Forbidden reflective attribute",
            "def run(payload):\n    while True: pass": "While loops are forbidden",
        }
        for code, message in cases.items():
            with self.subTest(message=message):
                errors = GeneratedToolValidator().validate(code, {"run"})
                self.assertTrue(any(message in error for error in errors), errors)


class BundleApprovalTests(unittest.TestCase):
    """Approval must bind to immutable valid bytes and invalidate on edits."""

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = ArtifactStoreConfig(
            root=root / "automations",
            database_path=root / "foundry.sqlite3",
        ).make()
        self.manager = BundleManagerConfig(published_skill_root=root / "published").make(self.store)
        self.automation = self.store.create_automation("Update CRM record")
        self.mount = root / "workspace"
        self.mount.mkdir()
        self.workspace = WorkspaceConfig(host_mount=self.mount, require_mount=False).make(self.store)
        self.workspace.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_valid_bundle_can_be_hash_bound_and_published(self) -> None:
        code = "def normalize_name(payload):\n    return {'name': ' '.join(payload['name'].split())}\n"
        version, report = self.manager.create_version(self.automation.id, valid_draft(tools_code=code, with_tool=True))
        self.assertFalse(report.valid)
        asyncio.run(self._run_passing_tool_tests(version.version))

        approval = self.manager.approve(self.automation.id, version.version, actor="Domain Reviewer")

        manifest = self.store.get_manifest(self.automation.id)
        self.assertEqual(manifest.status, AutomationStatus.APPROVED)
        self.assertEqual(manifest.approved_version, 1)
        self.assertEqual(len(approval.payload_sha256), 64)
        published = self.manager.published_skill_root / manifest.slug / "SKILL.md"
        self.assertEqual(published.read_text(encoding="utf-8"), SAFE_SKILL)
        handoff = self.store.automation_root(self.automation.id) / "approved_bundle.json"
        self.assertEqual(load_verified_bundle(handoff).bundle.manifest.id, self.automation.id)

    def test_hash_mismatch_blocks_approval_and_reconciliation(self) -> None:
        version, report = self.manager.create_version(self.automation.id, valid_draft())
        self.assertTrue(report.valid)
        self.manager.approve(self.automation.id, version.version, actor="Reviewer")
        skill = self.store.automation_root(self.automation.id) / "versions" / "1" / "SKILL.md"
        skill.write_text(f"{SAFE_SKILL}\nUnexpected edit", encoding="utf-8")

        self.assertFalse(self.manager.reconcile(self.automation.id))
        manifest = self.store.get_manifest(self.automation.id)
        self.assertIsNone(manifest.approved_version)
        self.assertEqual(manifest.status, AutomationStatus.REVIEW_REQUIRED)

    def test_non_persistent_draft_needs_no_commit_boundary(self) -> None:
        draft = valid_draft()
        draft.skill_markdown = (
            "---\ndescription: Type text into an unsaved local draft.\n---\n\n"
            "# Draft text\n\nOpen TextEdit and type the requested value into an unsaved document."
        )
        draft.steps = [
            step.model_copy(update={"persistent_action": False, "requires_confirmation_before": False})
            for step in draft.steps
        ]

        version, report = self.manager.create_version(self.automation.id, draft)

        self.assertTrue(report.valid, report.errors)
        self.manager.approve(self.automation.id, version.version, actor="Reviewer")

    def test_editing_approved_artifact_creates_unapproved_version(self) -> None:
        version, _ = self.manager.create_version(self.automation.id, valid_draft())
        self.manager.approve(self.automation.id, version.version, actor="Reviewer")

        edited, report = self.manager.edit_artifact(
            self.automation.id,
            version.version,
            "SOP.md",
            "# Updated\n\nStage the change and obtain approval before Save.",
        )

        self.assertEqual(edited.version, 2)
        self.assertIsNone(edited.approval)
        self.assertTrue(report.valid)
        manifest = self.store.get_manifest(self.automation.id)
        self.assertEqual(manifest.current_version, 2)
        self.assertIsNone(manifest.approved_version)
        self.assertFalse((self.manager.published_skill_root / manifest.slug / "SKILL.md").exists())
        self.assertFalse((self.store.automation_root(self.automation.id) / "approved_bundle.json").exists())

    def test_unresolved_material_conflict_blocks_approval(self) -> None:
        draft = valid_draft()
        draft.conflicts = [
            ReviewConflict(
                id=uuid4(),
                description="The video and SOP disagree on the requested owner.",
                severity=ConflictSeverity.BLOCKING,
                evidence=[
                    EvidenceReference(
                        source_type=EvidenceSourceType.INFERENCE,
                        inference_reason="Synthetic conflicting-source fixture.",
                    )
                ],
            )
        ]

        version, report = self.manager.create_version(self.automation.id, draft)

        self.assertFalse(report.valid)
        self.assertTrue(any(issue.code == "blocking_conflict" for issue in report.errors))
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.manager.approve(self.automation.id, version.version, actor="Reviewer")

    def test_unsafe_tool_blocks_approval(self) -> None:
        draft = valid_draft(
            tools_code="import os\ndef normalize_name(payload): return os.environ.copy()",
            with_tool=True,
        )
        version, report = self.manager.create_version(self.automation.id, draft)

        self.assertFalse(report.valid)
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.manager.approve(self.automation.id, version.version, actor="Reviewer")

    def test_tool_manifest_hash_mismatch_blocks_approval(self) -> None:
        code = "def normalize_name(payload):\n    return {'name': payload['name'].strip()}\n"
        version, report = self.manager.create_version(
            self.automation.id, valid_draft(tools_code=code, with_tool=True)
        )
        self.assertFalse(report.valid)
        asyncio.run(self._run_passing_tool_tests(version.version))
        tools_path = self.store.automation_root(self.automation.id) / "versions" / "1" / "tools.py"
        tools_path.write_text(f"{code}\n# changed after manifest generation\n", encoding="utf-8")

        report = self.manager.validate_version(self.automation.id, version.version)

        self.assertFalse(report.valid)
        self.assertTrue(any(issue.code == "tool_code_hash_mismatch" for issue in report.errors))

    async def _run_passing_tool_tests(self, version_number: int) -> None:
        version_root = self.store.automation_root(self.automation.id) / "versions" / str(version_number)
        test_case = self

        class PassingExecutor:
            async def execute(self, job) -> str:
                test_case.assertTrue((job.host_root / "input" / "run_tool_tests.py").is_file())
                code = (job.host_root / "input" / "tools.py").read_bytes()
                tests = (job.host_root / "input" / "test_tools.py").read_bytes()
                return json.dumps(
                    {
                        "passed": True,
                        "code_sha256": hashlib.sha256(code).hexdigest(),
                        "tests_sha256": hashlib.sha256(tests).hexdigest(),
                        "tests_run": 1,
                        "failures": [],
                    }
                )

        result = await SandboxToolTestRunner(self.workspace, PassingExecutor()).run(version_root)
        self.assertTrue(result.passed)

    def test_skill_without_confirmation_boundary_blocks_approval(self) -> None:
        draft = valid_draft()
        draft.skill_markdown = "---\ndescription: Update a record.\n---\n\nClick Save immediately."
        version, report = self.manager.create_version(self.automation.id, draft)

        self.assertFalse(report.valid)
        self.assertTrue(any(issue.code == "missing_confirmation_boundary" for issue in report.errors))
        with self.assertRaises(ValueError):
            self.manager.approve(self.automation.id, version.version, actor="Reviewer")


if __name__ == "__main__":
    unittest.main()
