"""Deterministic evidence preprocessing tests."""

import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automation_foundry.authoring.evidence import TranscriptSegment
from automation_foundry.authoring.preprocessing import PreprocessingConfig
from automation_foundry.authoring.uploads import UploadPolicyConfig
from automation_foundry.storage import ArtifactStoreConfig


class FakeTranscriber:
    """Return stable transcript segments without a provider call."""

    async def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        if not audio_path.is_file():
            raise AssertionError("Expected extracted audio file")
        return [TranscriptSegment(text="Update Sarah Chen", start_seconds=0.2, stop_seconds=1.4)]


class FakeMediaRunner:
    """Simulate ffprobe and FFmpeg outputs for preprocessing tests."""

    def __init__(self, duration: float = 12.5, *, include_audio: bool = True):
        self.duration = duration
        self.include_audio = include_audio
        self.commands: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(arguments)
        if arguments[0] == "ffprobe":
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=json.dumps({"format": {"duration": str(self.duration)}}),
                stderr="",
            )
        destination = Path(arguments[-1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if "%06d" in destination.name:
            Path(str(destination).replace("%06d", "000001")).write_bytes(b"jpeg")
        elif self.include_audio:
            destination.write_bytes(b"R" * 100)
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")


class EvidencePreprocessorTests(unittest.IsolatedAsyncioTestCase):
    """Verify bounded text and video evidence packages."""

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        data_root = Path(self.temporary_directory.name)
        self.store = ArtifactStoreConfig(
            root=data_root / "automations",
            database_path=data_root / "foundry.sqlite3",
        ).make()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_markdown_sop_is_normalized_with_page_references(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig().make(self.store)
        source = uploads.store_upload(
            automation.id,
            filename="procedure.md",
            media_type="text/markdown",
            stream=io.BytesIO(b"# First\r\nDo this\f# Second\nThen this"),
        )

        package = await PreprocessingConfig(max_sop_pages=2).make(self.store).preprocess(automation.id)

        self.assertEqual(package.sop_documents[0].source_id, source.id)
        self.assertEqual([page.page for page in package.sop_documents[0].pages], [1, 2])
        self.assertEqual(package.sop_documents[0].pages[0].text, "# First\nDo this")
        self.assertEqual(self.store.get_manifest(automation.id).sources[0].page_count, 2)

    async def test_sop_page_limit_fails_before_evidence_is_persisted(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig().make(self.store)
        uploads.store_upload(
            automation.id,
            filename="procedure.txt",
            media_type="text/plain",
            stream=io.BytesIO(b"one\ftwo"),
        )

        with self.assertRaisesRegex(ValueError, "page limit"):
            await PreprocessingConfig(max_sop_pages=1).make(self.store).preprocess(automation.id)

        self.assertFalse((self.store.automation_root(automation.id) / "evidence" / "evidence.json").exists())

    async def test_video_produces_frames_audio_and_timestamped_transcript(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig(max_video_bytes=100).make(self.store)
        source = uploads.store_upload(
            automation.id,
            filename="demo.mp4",
            media_type="video/mp4",
            stream=io.BytesIO(b"synthetic-video"),
        )
        runner = FakeMediaRunner()

        package = await PreprocessingConfig().make(
            self.store,
            transcriber=FakeTranscriber(),
            command_runner=runner,
        ).preprocess(automation.id)

        video = package.videos[0]
        self.assertEqual(video.source_id, source.id)
        self.assertEqual(video.duration_seconds, 12.5)
        self.assertEqual(len(video.frame_paths), 1)
        self.assertIsNotNone(video.audio_path)
        self.assertEqual(video.transcript[0].text, "Update Sarah Chen")
        self.assertEqual(self.store.get_manifest(automation.id).sources[0].duration_seconds, 12.5)

    async def test_silent_video_keeps_visual_evidence_without_transcript(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig(max_video_bytes=100).make(self.store)
        uploads.store_upload(
            automation.id,
            filename="silent.webm",
            media_type="video/webm",
            stream=io.BytesIO(b"synthetic-video"),
        )

        package = await PreprocessingConfig().make(
            self.store,
            transcriber=FakeTranscriber(),
            command_runner=FakeMediaRunner(include_audio=False),
        ).preprocess(automation.id)

        self.assertEqual(package.videos[0].transcript, [])
        self.assertIsNone(package.videos[0].audio_path)

    async def test_narrated_video_requires_gradium_configuration(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig(max_video_bytes=100).make(self.store)
        uploads.store_upload(
            automation.id,
            filename="narrated.mp4",
            media_type="video/mp4",
            stream=io.BytesIO(b"synthetic-video"),
        )

        with self.assertRaisesRegex(RuntimeError, "Gradium transcription is not configured"):
            await PreprocessingConfig().make(
                self.store,
                command_runner=FakeMediaRunner(include_audio=True),
            ).preprocess(automation.id)

        evidence_path = self.store.automation_root(automation.id) / "evidence" / "evidence.json"
        self.assertFalse(evidence_path.exists())

    async def test_video_duration_limit_fails_before_extraction(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig(max_video_bytes=100).make(self.store)
        uploads.store_upload(
            automation.id,
            filename="long.mov",
            media_type="video/quicktime",
            stream=io.BytesIO(b"synthetic-video"),
        )
        runner = FakeMediaRunner(duration=601)

        with self.assertRaisesRegex(ValueError, "outside the supported range"):
            await PreprocessingConfig(max_video_seconds=600).make(
                self.store,
                command_runner=runner,
            ).preprocess(automation.id)

        self.assertEqual(len(runner.commands), 1)


if __name__ == "__main__":
    unittest.main()
