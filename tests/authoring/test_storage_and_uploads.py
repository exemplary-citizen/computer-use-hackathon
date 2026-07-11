"""Persistence and upload-policy tests."""

import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automation_foundry.authoring.uploads import UploadPolicyConfig
from automation_foundry.contracts import AutomationStatus, EvidenceSourceType
from automation_foundry.storage import ArtifactStoreConfig


class ArtifactStoreTests(unittest.TestCase):
    """Verify durable manifests and safe source storage."""

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        data_root = Path(self.temporary_directory.name)
        self.config = ArtifactStoreConfig(
            root=data_root / "automations",
            database_path=data_root / "foundry.sqlite3",
        )
        self.store = self.config.make()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_manifest_survives_store_restart(self) -> None:
        created = self.store.create_automation("  Update   CRM lead  ")

        restarted = self.config.make()
        loaded = restarted.get_manifest(created.id)

        self.assertEqual(loaded.name, "Update CRM lead")
        self.assertEqual(loaded.status, AutomationStatus.DRAFT)
        self.assertEqual(restarted.list_automations(), [loaded])

    def test_supported_source_is_hashed_and_indexed(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig(max_sop_bytes=100).make(self.store)

        source = uploads.store_upload(
            automation.id,
            filename="procedure.md",
            media_type="text/markdown; charset=utf-8",
            stream=io.BytesIO(b"# Procedure\n"),
        )

        manifest = self.store.get_manifest(automation.id)
        self.assertEqual(source.source_type, EvidenceSourceType.SOP)
        self.assertEqual(source.size_bytes, 12)
        self.assertEqual(len(source.sha256), 64)
        self.assertEqual(manifest.status, AutomationStatus.PROCESSING)
        self.assertEqual(manifest.sources, [source])
        source_path = self.store.automation_root(automation.id) / source.relative_path
        self.assertEqual(source_path.read_bytes(), b"# Procedure\n")

    def test_oversized_source_leaves_no_partial_file(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig(max_sop_bytes=4).make(self.store)

        with self.assertRaisesRegex(ValueError, "exceeds"):
            uploads.store_upload(
                automation.id,
                filename="procedure.txt",
                media_type="text/plain",
                stream=io.BytesIO(b"too large"),
            )

        source_directory = self.store.automation_root(automation.id) / "source"
        self.assertEqual(list(source_directory.iterdir()), [])
        self.assertEqual(self.store.get_manifest(automation.id).sources, [])

    def test_filename_path_traversal_is_rejected(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig().make(self.store)

        for unsafe_name in ("../procedure.md", "folder/procedure.md", "..", ""):
            with self.subTest(filename=unsafe_name), self.assertRaises(ValueError):
                uploads.store_upload(
                    automation.id,
                    filename=unsafe_name,
                    media_type="text/markdown",
                    stream=io.BytesIO(b"content"),
                )

    def test_extension_and_media_type_must_agree(self) -> None:
        automation = self.store.create_automation("Update CRM lead")
        uploads = UploadPolicyConfig().make(self.store)

        with self.assertRaisesRegex(ValueError, "Unexpected media type"):
            uploads.store_upload(
                automation.id,
                filename="demonstration.mp4",
                media_type="text/plain",
                stream=io.BytesIO(b"not a video"),
            )


if __name__ == "__main__":
    unittest.main()
