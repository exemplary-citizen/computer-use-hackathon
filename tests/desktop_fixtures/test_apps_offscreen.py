"""Headless widget tests: no persistence before the explicit Save/Commit action."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from desktop_fixtures.crm_a import CrmAWindow
from desktop_fixtures.crm_b import CrmBWindow, RecordDialog
from desktop_fixtures.qt_common import WINDOW_HEIGHT, WINDOW_WIDTH
from desktop_fixtures.store import default_seed, load_state, state_path, write_state_atomic


def setUpModule() -> None:
    if QApplication.instance() is None:
        QApplication([])


class CrmAWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = state_path("a", Path(self._tmp.name))
        write_state_atomic(self.path, default_seed())
        self.baseline = self.path.read_bytes()
        self.window = CrmAWindow(self.path)
        self.addCleanup(self.window.close)

    def test_window_geometry_is_pinned(self) -> None:
        self.assertEqual((self.window.width(), self.window.height()), (WINDOW_WIDTH, WINDOW_HEIGHT))

    def test_save_button_is_text_labeled(self) -> None:
        self.assertEqual(self.window.save_button.text(), "Save")
        self.assertEqual(self.window.add_button.text(), "Add Record")

    def test_editing_fields_does_not_touch_persisted_state(self) -> None:
        self.window._phone.setText("+1 000 555 0000")
        self.window._notes.setPlainText("edited but never saved")
        self.assertEqual(self.path.read_bytes(), self.baseline)

    def test_save_persists_exactly_the_edited_fields(self) -> None:
        self.window._phone.setText("+1 999 555 0101")
        self.window.save_current_record()
        state = load_state(self.path)
        self.assertEqual(state.records[0].phone, "+1 999 555 0101")
        unchanged = default_seed().records[0].model_dump(exclude={"phone"})
        self.assertEqual(state.records[0].model_dump(exclude={"phone"}), unchanged)

    def test_switching_contacts_discards_unsaved_edits(self) -> None:
        self.window._phone.setText("+1 111 111 1111")
        self.window._contact_list.setCurrentRow(1)
        self.window._contact_list.setCurrentRow(0)
        self.assertEqual(self.window._phone.text(), default_seed().records[0].phone)
        self.assertEqual(self.path.read_bytes(), self.baseline)

    def test_add_record_requires_explicit_save(self) -> None:
        self.window.begin_add_record()
        self.window._first_name.setText("Amina")
        self.window._last_name.setText("Diallo")
        self.assertEqual(self.path.read_bytes(), self.baseline)
        self.window.save_current_record()
        state = load_state(self.path)
        self.assertEqual(state.records[-1].full_name, "Amina Diallo")
        self.assertEqual(state.records[-1].company, "Not provided")


class CrmBWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = state_path("b", Path(self._tmp.name))
        write_state_atomic(self.path, default_seed())
        self.baseline = self.path.read_bytes()
        self.window = CrmBWindow(self.path)
        self.addCleanup(self.window.close)

    def test_search_filters_results(self) -> None:
        self.window._search_box.setText("cobalt")
        self.window.run_search()
        self.assertEqual(self.window._results.rowCount(), 1)
        item = self.window._results.item(0, 2)
        assert item is not None
        self.assertEqual(item.text(), "Cobalt Analytics")

    def test_dialog_editing_does_not_touch_persisted_state(self) -> None:
        dialog = RecordDialog(default_seed().records[2])
        dialog.phone.setText("+1 222 555 0202")
        self.assertEqual(dialog.edited_record().phone, "+1 222 555 0202")
        self.assertEqual(self.path.read_bytes(), self.baseline)

    def test_commit_persists_exactly_the_edited_fields(self) -> None:
        edited = default_seed().records[2].model_copy(update={"status": "Churned"})
        self.window.apply_commit(2, edited)
        state = load_state(self.path)
        self.assertEqual(state.records[2].status, "Churned")
        unchanged = default_seed().records[2].model_dump(exclude={"status"})
        self.assertEqual(state.records[2].model_dump(exclude={"status"}), unchanged)

    def test_commit_button_is_text_labeled(self) -> None:
        dialog = RecordDialog(default_seed().records[0])
        self.assertEqual(dialog.commit_button.text(), "Commit Changes")
        create_dialog = RecordDialog(None, record_id="c007")
        self.assertEqual(create_dialog.commit_button.text(), "Add Record")

    def test_add_record_persists_only_on_apply_create(self) -> None:
        dialog = RecordDialog(None, record_id="c007")
        dialog.first_name.setText("Amina")
        dialog.last_name.setText("Diallo")
        self.assertEqual(self.path.read_bytes(), self.baseline)
        self.window.apply_create(dialog.edited_record())
        state = load_state(self.path)
        self.assertEqual(state.records[-1].full_name, "Amina Diallo")
        self.assertEqual(self.window.add_button.text(), "Add Record")

    def test_window_geometry_is_pinned(self) -> None:
        self.assertEqual((self.window.width(), self.window.height()), (WINDOW_WIDTH, WINDOW_HEIGHT))

    def test_open_record_control_stays_away_from_screen_corner(self) -> None:
        self.window.show()
        QApplication.processEvents()
        self.assertLess(self.window.open_button.geometry().center().x(), self.window.width() // 2)


if __name__ == "__main__":
    unittest.main()
