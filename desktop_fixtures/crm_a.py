"""CRM A — "Northlight CRM", the taught application.

Structure: left-hand contact list, inline detail form on the right, one
explicit bottom-right "Save" button. Selecting another contact discards
unsaved edits; the Save button is the only path to persisted state.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop_fixtures.holo_overlay import HoloOverlay
from desktop_fixtures.qt_common import apply_light_fusion_style, fix_window_geometry
from desktop_fixtures.store import (
    FIELD_LABELS,
    STATUS_VALUES,
    AppKey,
    ContactRecord,
    CrmState,
    load_state,
    next_contact_id,
    state_path,
    write_state_atomic,
)

_APP_KEY: AppKey = "a"


class CrmAWindow(QMainWindow):
    """Main window holding the in-memory working state for CRM A."""

    def __init__(self, path: Path):
        """Load persisted state once and build the UI.

        Args:
            path: JSON state file backing this instance.
        """
        super().__init__()
        self._path = path
        self._state: CrmState = load_state(path)
        self._current_index: int | None = None
        self._creating_new = False
        self.setWindowTitle("Northlight CRM")
        fix_window_geometry(self)
        self._build_ui()
        if self._state.records:
            self._contact_list.setCurrentRow(0)

    def save_current_record(self) -> None:
        """Apply form edits to the selected record and persist the whole store."""
        if self._creating_new:
            first_name = self._first_name.text().strip()
            last_name = self._last_name.text().strip()
            if not first_name or not last_name:
                self.statusBar().showMessage("First Name and Last Name are required", 5_000)
                return
            record = ContactRecord(
                id=next_contact_id(self._state),
                first_name=first_name,
                last_name=last_name,
                company=self._company.text().strip() or "Not provided",
                phone=self._phone.text().strip() or "Not provided",
                email=self._email.text().strip() or "Not provided",
                status=self._status.currentText(),
                owner=self._owner.text().strip() or "Unassigned",
                notes=self._notes.toPlainText().strip(),
            )
            self._state.records.append(record)
            write_state_atomic(self._path, self._state)
            self._contact_list.addItem(record.full_name)
            self._creating_new = False
            self._contact_list.setCurrentRow(len(self._state.records) - 1)
            self.statusBar().showMessage(f"Added {record.full_name}", 5_000)
            return
        if self._current_index is None:
            return
        record = self._state.records[self._current_index]
        updated = record.model_copy(
            update={
                "first_name": self._first_name.text().strip(),
                "last_name": self._last_name.text().strip(),
                "company": self._company.text().strip(),
                "phone": self._phone.text().strip(),
                "email": self._email.text().strip(),
                "status": self._status.currentText(),
                "owner": self._owner.text().strip(),
                "notes": self._notes.toPlainText().strip(),
            }
        )
        self._state.records[self._current_index] = updated
        write_state_atomic(self._path, self._state)
        self._contact_list.item(self._current_index).setText(updated.full_name)
        self.statusBar().showMessage(f"Saved {updated.full_name}", 5_000)

    def begin_add_record(self) -> None:
        """Clear the form for a new record without touching persisted state."""
        self._contact_list.setCurrentRow(-1)
        self._current_index = None
        self._creating_new = True
        for field in (
            self._first_name,
            self._last_name,
            self._company,
            self._phone,
            self._email,
            self._owner,
        ):
            field.clear()
        self._status.setCurrentText("Lead")
        self._notes.clear()
        self.save_button.setText("Add Record")
        self._first_name.setFocus()
        self.statusBar().showMessage("Enter the new contact details, then choose Add Record")

    def _build_ui(self) -> None:
        labels = {field: per_app[_APP_KEY] for field, per_app in FIELD_LABELS.items()}
        root = QWidget()
        layout = QHBoxLayout(root)

        left = QVBoxLayout()
        left.addWidget(QLabel("Contacts"))
        self._contact_list = QListWidget()
        for record in self._state.records:
            self._contact_list.addItem(record.full_name)
        self._contact_list.currentRowChanged.connect(self._load_record)
        left.addWidget(self._contact_list)
        self.add_button = QPushButton("Add Record")
        self.add_button.clicked.connect(self.begin_add_record)
        left.addWidget(self.add_button)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Contact details"))
        form = QFormLayout()
        self._first_name = QLineEdit()
        self._last_name = QLineEdit()
        self._company = QLineEdit()
        self._phone = QLineEdit()
        self._email = QLineEdit()
        self._status = QComboBox()
        self._status.addItems(list(STATUS_VALUES))
        self._owner = QLineEdit()
        self._notes = QPlainTextEdit()
        self._notes.setFixedHeight(120)
        for label_text, widget in (
            (labels["first_name"], self._first_name),
            (labels["last_name"], self._last_name),
            (labels["company"], self._company),
            (labels["phone"], self._phone),
            (labels["email"], self._email),
            (labels["status"], self._status),
            (labels["owner"], self._owner),
            (labels["notes"], self._notes),
        ):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            form.addRow(label, widget)
        right.addLayout(form)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("primaryAction")
        self.save_button.clicked.connect(self.save_current_record)
        button_row.addWidget(self.save_button)
        right.addLayout(button_row)
        layout.addLayout(right, 2)

        self.setCentralWidget(root)
        self.statusBar().showMessage("Ready")
        self._holo_overlay = HoloOverlay(self, _APP_KEY)

    def _load_record(self, row: int) -> None:
        if row < 0 or row >= len(self._state.records):
            self._current_index = None
            return
        self._current_index = row
        self._creating_new = False
        self.save_button.setText("Save")
        record = self._state.records[row]
        self._first_name.setText(record.first_name)
        self._last_name.setText(record.last_name)
        self._company.setText(record.company)
        self._phone.setText(record.phone)
        self._email.setText(record.email)
        self._status.setCurrentText(record.status)
        self._owner.setText(record.owner)
        self._notes.setPlainText(record.notes)


def main() -> None:
    """Launch CRM A against its default state file."""
    app = QApplication(sys.argv)
    apply_light_fusion_style(app)
    window = CrmAWindow(state_path(_APP_KEY))
    window.show()
    window.raise_()
    window.activateWindow()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
