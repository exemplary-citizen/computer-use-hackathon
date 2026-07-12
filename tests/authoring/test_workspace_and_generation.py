"""Workspace, Hermes, and direct-video generation boundary tests."""

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from automation_foundry.authoring.evidence import EvidencePackage, VideoEvidence
from automation_foundry.authoring.generation import BundleGenerator, GeneratedBundleDraft
from automation_foundry.authoring.workspace import WorkspaceConfig
from automation_foundry.contracts import EvidenceSourceType, SourceMetadata
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


class FakeDirectMediaClient:
    """Capture original video paths supplied to a hosted multimodal provider."""

    def __init__(self, response: str):
        self.response = response
        self.video_paths: list[Path] = []

    async def complete_bundle(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        evidence_json: str,
        response_schema: dict[str, object],
        video_paths: list[Path],
    ) -> str:
        del system_prompt, user_prompt, evidence_json, response_schema
        self.video_paths = video_paths
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
        client = FakeHermesClient(response)

        draft = await BundleGenerator(self.bridge, client).generate(automation.id, evidence)

        self.assertIn("mandatory stop-and-review", client.system_prompt)
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

    async def test_direct_media_generator_receives_the_original_uploaded_video(self) -> None:
        automation = self.store.create_automation("Create contact")
        source_id = uuid4()
        source_path = self.store.automation_root(automation.id) / "sources" / "demo.mp4"
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(b"small-video")
        automation.sources.append(
            SourceMetadata(
                id=source_id,
                source_type=EvidenceSourceType.VIDEO,
                original_name="demo.mp4",
                relative_path="sources/demo.mp4",
                media_type="video/mp4",
                size_bytes=source_path.stat().st_size,
                sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            )
        )
        self.store.save_manifest(automation)
        evidence = EvidencePackage(
            automation_id=automation.id,
            videos=[
                VideoEvidence(
                    source_id=source_id,
                    duration_seconds=2,
                    frame_paths=self._create_frame(automation.id),
                )
            ],
        )
        response = json.dumps(
            {
                "schema_version": "1.0",
                "sop_markdown": "# Create contact\n\nStage a new contact before adding it.",
                "skill_markdown": "---\ndescription: Create a CRM contact safely.\n---\n\nStop before Add Record.",
                "inputs": [],
                "input_schema": {"type": "object"},
                "steps": [
                    {
                        "id": "add_contact",
                        "instruction": "After approval, click Add Record once.",
                        "critical": True,
                        "persistent_action": True,
                        "requires_confirmation_before": True,
                        "evidence": [{"source_type": "video", "timestamp_seconds": 1}],
                    }
                ],
                "preconditions": [],
                "completion_checks": [
                    {
                        "id": "visible_contact",
                        "description": "The contact appears in the list.",
                        "evidence": [{"source_type": "video", "timestamp_seconds": 1}],
                    }
                ],
                "tools_code": "",
                "tools": [],
                "eval_cases": [],
                "conflicts": [],
            }
        )
        client = FakeDirectMediaClient(response)

        draft = await BundleGenerator(self.bridge, client).generate(automation.id, evidence)

        self.assertEqual(client.video_paths, [source_path.resolve()])
        self.assertEqual(draft.steps[0].evidence[0].source_id, source_id)

    def _create_frame(self, automation_id) -> list[str]:
        automation_root = self.store.automation_root(automation_id)
        frame = automation_root / "evidence" / "frames" / "frame-000001.jpg"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"jpeg")
        return [frame.relative_to(automation_root).as_posix()]


if __name__ == "__main__":
    unittest.main()
