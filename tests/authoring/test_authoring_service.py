"""Authoring service lifecycle tests independent of provider credentials."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automation_foundry.authoring.bundles import BundleManagerConfig
from automation_foundry.authoring.service import AuthoringService
from automation_foundry.authoring.uploads import UploadPolicyConfig
from automation_foundry.contracts import AutomationStatus
from automation_foundry.storage import ArtifactStoreConfig


class SuccessfulPipeline:
    """Complete a retry without requiring provider credentials."""

    def __init__(self, store) -> None:
        self.store = store

    async def run(self, automation_id, progress) -> None:
        manifest = self.store.get_manifest(automation_id)
        manifest.status = AutomationStatus.REVIEW_REQUIRED
        self.store.save_manifest(manifest)
        progress("complete", 100, "Bundle is ready for review")


class AuthoringServiceTests(unittest.TestCase):
    """Verify persistent failure, deactivation, and deletion behavior."""

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = ArtifactStoreConfig(
            root=root / "automations",
            database_path=root / "foundry.sqlite3",
        ).make()
        bundles = BundleManagerConfig(published_skill_root=root / "published").make(self.store)
        self.service = AuthoringService(
            self.store,
            UploadPolicyConfig().make(self.store),
            bundles,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_unconfigured_processing_fails_persistently_and_safely(self) -> None:
        automation = self.store.create_automation("Update CRM record")

        asyncio.run(self.service.process(automation.id))

        manifest = self.store.get_manifest(automation.id)
        self.assertEqual(manifest.status, AutomationStatus.FAILED)
        self.assertIn("Generation is not configured", self.service.processing_error(automation.id) or "")
        progress = self.service.processing_progress(automation.id)
        self.assertIsNotNone(progress)
        self.assertEqual(progress.stage, "failed")

    def test_successful_retry_clears_stale_processing_error(self) -> None:
        automation = self.store.create_automation("Update CRM record")
        asyncio.run(self.service.process(automation.id))
        self.assertIsNotNone(self.service.processing_error(automation.id))
        self.service.pipeline = SuccessfulPipeline(self.store)

        asyncio.run(self.service.process(automation.id))

        self.assertEqual(self.store.get_manifest(automation.id).status, AutomationStatus.REVIEW_REQUIRED)
        self.assertIsNone(self.service.processing_error(automation.id))

    def test_deactivate_and_delete_update_persistent_state(self) -> None:
        automation = self.store.create_automation("Update CRM record")
        published = self.service.bundles.published_skill_root / automation.slug / "SKILL.md"
        published.parent.mkdir(parents=True)
        published.write_text("published", encoding="utf-8")

        inactive = self.service.deactivate(automation.id)
        self.assertEqual(inactive.status, AutomationStatus.INACTIVE)
        self.assertFalse(published.exists())
        self.service.delete(automation.id)

        with self.assertRaises(KeyError):
            self.store.get_manifest(automation.id)
        self.assertEqual(self.store.list_automations(), [])

    def test_startup_reconciliation_marks_processing_as_interrupted(self) -> None:
        automation = self.store.create_automation("Update CRM record")
        automation.status = AutomationStatus.PROCESSING
        self.store.save_manifest(automation)

        self.service.reconcile_startup()

        manifest = self.store.get_manifest(automation.id)
        self.assertEqual(manifest.status, AutomationStatus.FAILED)
        self.assertIn("interrupted", self.service.processing_error(automation.id) or "")
        self.assertEqual(self.service.processing_progress(automation.id).stage, "failed")


if __name__ == "__main__":
    unittest.main()
