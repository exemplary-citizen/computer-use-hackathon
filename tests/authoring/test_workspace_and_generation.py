"""NemoClaw workspace and Hermes generation boundary tests."""

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automation_foundry.authoring.evidence import EvidencePackage, VideoEvidence
from automation_foundry.authoring.generation import BundleGenerator, GeneratedBundleDraft
from automation_foundry.authoring.workspace import (
    NemoClawWorkspaceTransport,
    NemoClawWorkspaceTransportConfig,
    WorkspaceBridge,
    WorkspaceConfig,
)
from automation_foundry.storage import ArtifactStoreConfig


class FakeHermesClient:
    """Return a deterministic schema-valid bundle response."""

    def __init__(self, response: str):
        self.response = response
        self.system_prompt = ""
        self.user_prompt = ""

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return self.response


class WorkspaceAndGenerationTests(unittest.IsolatedAsyncioTestCase):
    """Verify narrow evidence staging and strict result parsing."""

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = ArtifactStoreConfig(
            root=root / "automations",
            database_path=root / "foundry.sqlite3",
        ).make()
        self.mount = root / "workspace"
        self.mount.mkdir()
        self.bridge = WorkspaceConfig(host_mount=self.mount, require_mount=False).make(self.store)
        self.bridge.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_workspace_marker_fails_closed(self) -> None:
        marker = self.mount / "automation-foundry" / ".workspace-version"
        marker.unlink()
        automation = self.store.create_automation("Update lead")
        evidence = EvidencePackage(automation_id=automation.id, sop_documents=[])

        with self.assertRaisesRegex(RuntimeError, "marker"):
            self.bridge.stage_generation(
                automation.id,
                evidence,
                result_schema=GeneratedBundleDraft.model_json_schema(),
            )

    def test_staging_copies_frames_but_not_audio_or_originals(self) -> None:
        automation = self.store.create_automation("Update lead")
        automation_root = self.store.automation_root(automation.id)
        frame = automation_root / "evidence" / "frames" / "source" / "frame-000001.jpg"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"jpeg")
        audio = automation_root / "evidence" / "audio.wav"
        audio.write_bytes(b"private-audio")
        evidence = EvidencePackage(
            automation_id=automation.id,
            videos=[
                VideoEvidence(
                    source_id="98bb77c7-a0fc-4c8d-9854-669cb9857f7a",
                    duration_seconds=2,
                    frame_paths=[frame.relative_to(automation_root).as_posix()],
                    audio_path=audio.relative_to(automation_root).as_posix(),
                )
            ],
        )

        job = self.bridge.stage_generation(
            automation.id,
            evidence,
            result_schema=GeneratedBundleDraft.model_json_schema(),
        )

        staged_files = [
            path.relative_to(job.host_root).as_posix() for path in job.host_root.rglob("*") if path.is_file()
        ]
        self.assertIn("input/frames/98bb77c7-a0fc-4c8d-9854-669cb9857f7a/frame-000001.jpg", staged_files)
        self.assertFalse(any("audio" in path for path in staged_files))
        self.assertFalse(any("source/" in path for path in staged_files))

    def test_cli_transport_publishes_only_the_staged_job(self) -> None:
        commands: list[tuple[str, ...]] = []

        def run_command(command, **_kwargs):
            commands.append(tuple(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        transport = NemoClawWorkspaceTransport(
            NemoClawWorkspaceTransportConfig(sandbox_name="hai-hermes"),
            runner=run_command,
        )
        bridge = WorkspaceBridge(
            WorkspaceConfig(host_mount=self.mount, require_mount=False),
            self.store,
            transport,
        )
        bridge.initialize()
        automation = self.store.create_automation("Update lead")
        evidence = EvidencePackage(automation_id=automation.id)

        job = bridge.stage_generation(
            automation.id,
            evidence,
            result_schema=GeneratedBundleDraft.model_json_schema(),
        )

        upload_commands = [command for command in commands if len(command) > 2 and command[2] == "upload"]
        self.assertEqual(len(upload_commands), 1)
        self.assertEqual(upload_commands[0][3], str(job.host_root))
        self.assertEqual(upload_commands[0][4], "/sandbox/workspace/automation-foundry/jobs/")
        self.assertNotIn(str(self.store.config.root), upload_commands[0])
        self.assertTrue(any("--timeout" in command and "120" in command for command in commands))

    def test_cli_transport_failure_stops_generation_before_hermes(self) -> None:
        def run_command(command, **_kwargs):
            return_code = 1 if len(command) > 2 and command[2] == "upload" else 0
            return subprocess.CompletedProcess(command, return_code, "", "provider details")

        transport = NemoClawWorkspaceTransport(
            NemoClawWorkspaceTransportConfig(sandbox_name="hai-hermes"),
            runner=run_command,
        )
        bridge = WorkspaceBridge(
            WorkspaceConfig(host_mount=self.mount, require_mount=False),
            self.store,
            transport,
        )
        bridge.initialize()
        automation = self.store.create_automation("Update lead")

        with self.assertRaisesRegex(RuntimeError, "workspace upload failed"):
            bridge.stage_generation(
                automation.id,
                EvidencePackage(automation_id=automation.id),
                result_schema=GeneratedBundleDraft.model_json_schema(),
            )

    def test_cli_transport_round_trips_tool_test_result(self) -> None:
        commands: list[tuple[str, ...]] = []

        def run_command(command, **_kwargs):
            commands.append(tuple(command))
            if len(command) > 2 and command[2] == "download":
                Path(command[4]).write_text('{"passed": true}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        transport = NemoClawWorkspaceTransport(
            NemoClawWorkspaceTransportConfig(sandbox_name="hai-hermes"),
            runner=run_command,
        )
        bridge = WorkspaceBridge(
            WorkspaceConfig(host_mount=self.mount, require_mount=False),
            self.store,
            transport,
        )
        bridge.initialize()
        job_id = "00000000-0000-4000-8000-000000000001"
        host_root = self.mount / "automation-foundry" / "tool-tests" / job_id
        (host_root / "input").mkdir(parents=True)
        (host_root / "output").mkdir()
        (host_root / "request.json").write_text("{}", encoding="utf-8")
        (host_root / "input/run_tool_tests.py").write_text("pass", encoding="utf-8")
        sandbox_root = bridge.config.sandbox_root / "automation-foundry" / "tool-tests" / job_id
        host_result = host_root / "output/result.json"

        bridge.publish_tool_test(host_root, sandbox_root)
        bridge.download_tool_test_result(sandbox_root / "output/result.json", host_result)

        upload = next(command for command in commands if len(command) > 2 and command[2] == "upload")
        download = next(command for command in commands if len(command) > 2 and command[2] == "download")
        self.assertEqual(upload[3], str(host_root))
        self.assertEqual(download[3], str(sandbox_root / "output/result.json"))
        self.assertEqual(download[4], str(host_result))
        self.assertTrue(host_result.is_file())

    async def test_generator_returns_validated_draft_and_persists_output(self) -> None:
        automation = self.store.create_automation("Update lead")
        evidence = EvidencePackage(
            automation_id=automation.id,
            sop_documents=[],
            videos=[
                VideoEvidence(
                    source_id="98bb77c7-a0fc-4c8d-9854-669cb9857f7a",
                    duration_seconds=2,
                    frame_paths=self._create_frame(automation.id),
                )
            ],
        )
        response = json.dumps(
            {
                "schema_version": "1.0",
                "sop_markdown": "# Update lead\n\nStage and review before saving.",
                "skill_markdown": "---\ndescription: Update a CRM lead safely.\n---\n\nStop before save.",
                "inputs": [],
                "input_schema": {"type": "object"},
                "steps": [
                    {
                        "id": "commit_update",
                        "instruction": "After approval, save the staged update once.",
                        "critical": True,
                        "persistent_action": True,
                        "requires_confirmation_before": True,
                        "evidence": [
                            {
                                "source_type": "inference",
                                "inference_reason": "Synthetic generation fixture boundary.",
                            }
                        ],
                    }
                ],
                "preconditions": [],
                "completion_checks": [
                    {
                        "id": "visible_success",
                        "description": "A visible success state appears.",
                        "evidence": [
                            {
                                "source_type": "inference",
                                "inference_reason": "Synthetic generation fixture check.",
                            }
                        ],
                    }
                ],
                "tools_code": "",
                "tools": [],
                "eval_cases": [],
                "conflicts": [],
            }
        )
        response = f"Generated bundle follows.\n```json\n{response}\n```\n"
        client = FakeHermesClient(response)

        draft = await BundleGenerator(self.bridge, client).generate(automation.id, evidence)

        self.assertIn("mandatory stop-and-review", client.system_prompt)
        self.assertIn("persistent_action=true MUST also set requires_confirmation_before=true", client.system_prompt)
        self.assertIn("typing into an unsaved local draft are non-persistent", client.system_prompt)
        self.assertIn("skill_markdown MUST start with YAML frontmatter", client.system_prompt)
        self.assertIn("Stop for explicit review and approval before Save", client.system_prompt)
        self.assertEqual(draft.sop_markdown.splitlines()[0], "# Update lead")
        outputs = list((self.mount / "automation-foundry" / "jobs").glob("*/output/bundle.json"))
        self.assertEqual(len(outputs), 1)

    async def test_generator_rejects_invalid_response(self) -> None:
        automation = self.store.create_automation("Update lead")
        evidence = EvidencePackage(
            automation_id=automation.id,
            videos=[
                VideoEvidence(
                    source_id="98bb77c7-a0fc-4c8d-9854-669cb9857f7a",
                    duration_seconds=2,
                    frame_paths=self._create_frame(automation.id),
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "invalid automation bundle"):
            await BundleGenerator(self.bridge, FakeHermesClient("not-json")).generate(automation.id, evidence)

    def _create_frame(self, automation_id) -> list[str]:
        automation_root = self.store.automation_root(automation_id)
        frame = automation_root / "evidence" / "frames" / "frame-000001.jpg"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"jpeg")
        return [frame.relative_to(automation_root).as_posix()]


if __name__ == "__main__":
    unittest.main()
