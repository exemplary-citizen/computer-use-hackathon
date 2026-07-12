"""Focused Atlas Returns Desk tests for the hackathon demo workflow."""

from __future__ import annotations

import plistlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from desktop_fixtures.atlas_returns import AtlasReturnsWindow
from desktop_fixtures.returns_cli import BUNDLE_IDENTIFIER, install_app
from desktop_fixtures.returns_store import default_returns_seed, load_returns_state, returns_state_path


def setUpModule() -> None:
    if QApplication.instance() is None:
        QApplication([])


class AtlasReturnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.path = returns_state_path(Path(self._temporary.name))
        self.window = AtlasReturnsWindow(self.path)
        self.addCleanup(self.window.close)

    def test_search_finds_seeded_analogous_hazmat_case(self) -> None:
        self.window._search.setText("RTN-1057")
        self.window.filter_cases()

        self.assertEqual(self.window._queue.rowCount(), 1)
        self.assertEqual(self.window._queue.item(0, 0).text(), "RTN-1057")
        seeded = default_returns_seed()
        analogous = [case for case in seeded.cases if "LITHIUM BATTERY" in case.risk_flags]
        self.assertGreaterEqual(len(analogous), 2)

    def test_draft_then_apply_resolution_persists_business_decision(self) -> None:
        self.window._search.setText("RTN-1057")
        self.window.filter_cases()
        self.window._queue.selectRow(0)
        self.window._resolution.setCurrentText("Replacement")
        self.window._disposition.setCurrentText("HAZMAT_INSPECTION")
        self.window._warehouse.setCurrentText("WH-HAZ-02")
        self.window._refund_amount.setValue(0)
        self.window._restocking_fee.setValue(0)
        self.window._internal_note.setPlainText("Gold customer; isolate swollen battery and issue replacement.")

        self.window.save_draft()
        draft = next(case for case in load_returns_state(self.path).cases if case.case_id == "RTN-1057")
        self.assertEqual(draft.status, "New")
        self.assertEqual(draft.disposition_code, "HAZMAT_INSPECTION")

        self.assertTrue(self.window.apply_resolution())
        resolved = next(case for case in load_returns_state(self.path).cases if case.case_id == "RTN-1057")
        self.assertEqual(resolved.status, "Resolved")
        self.assertEqual(resolved.resolution, "Replacement")
        self.assertEqual(resolved.warehouse_route, "WH-HAZ-02")
        self.assertEqual(resolved.restocking_fee, 0)
        self.assertEqual(resolved.timeline[-1].action, "Resolution applied")

    def test_apply_resolution_accepts_note_without_optional_fields(self) -> None:
        self.window._search.setText("RTN-1064")
        self.window.filter_cases()
        self.window._queue.selectRow(0)
        self.window._internal_note.setPlainText("Customer sounds very frustrated. Initiate return ASAP.")

        self.assertTrue(self.window.apply_resolution())

        resolved = next(case for case in load_returns_state(self.path).cases if case.case_id == "RTN-1064")
        self.assertEqual(resolved.internal_note, "Customer sounds very frustrated. Initiate return ASAP.")
        self.assertEqual(resolved.status, "Resolved")
        self.assertEqual(resolved.timeline[-1].action, "Resolution applied")
        self.assertEqual(self.window.statusBar().currentMessage(), "Updated!")

    def test_quit_restores_canonical_state_for_next_launch(self) -> None:
        self.window._search.setText("RTN-1064")
        self.window.filter_cases()
        self.window._queue.selectRow(0)
        self.window._internal_note.setPlainText("Temporary demo decision")
        self.assertTrue(self.window.apply_resolution())

        self.window.show()
        QApplication.processEvents()
        self.window.close()

        restored = next(case for case in load_returns_state(self.path).cases if case.case_id == "RTN-1064")
        self.assertEqual(restored.status, "Escalated")
        self.assertEqual(restored.internal_note, "")
        reopened = AtlasReturnsWindow(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened._search.text(), "")
        self.assertNotEqual(reopened._current_case_id, "RTN-1064")

    def test_installer_creates_launchable_macos_bundle(self) -> None:
        destination = Path(self._temporary.name) / "Applications"

        app_path = install_app(destination)

        launcher = app_path / "Contents" / "MacOS" / "atlas-returns-desk"
        plist_path = app_path / "Contents" / "Info.plist"
        self.assertTrue(launcher.is_file())
        self.assertTrue(launcher.stat().st_mode & 0o100)
        with plist_path.open("rb") as source:
            plist = plistlib.load(source)
        self.assertEqual(plist["CFBundleIdentifier"], BUNDLE_IDENTIFIER)
        self.assertEqual(plist["CFBundleDisplayName"], "Atlas Returns Desk")


if __name__ == "__main__":
    unittest.main()
