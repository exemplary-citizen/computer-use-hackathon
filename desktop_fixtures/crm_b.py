"""CRM B — "Meridian Contacts", the zero-shot transfer target.

Deliberately different structure from CRM A: top tab bar, search-driven
lookup into a results table, and a modal edit dialog whose "Commit Changes"
button is the only path to persisted state. Field labels use different
wording (see ``FIELD_LABELS``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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

_APP_KEY: AppKey = "b"
_TABLE_COLUMNS = ("Given name", "Family name", "Organisation", "Stage")


class RecordDialog(QDialog):
    """Modal editor for one contact; commits via an explicit button only."""

    def __init__(
        self,
        record: ContactRecord | None,
        parent: QWidget | None = None,
        *,
        record_id: str | None = None,
    ):
        """Build the form pre-filled with the record's current values.

        Args:
            record: Contact being edited, or ``None`` for creation.
            parent: Owning window.
            record_id: New deterministic identifier when creating.
        """
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._record = record
        self._record_id = record.id if record is not None else record_id
        if self._record_id is None:
            raise ValueError("record_id is required when creating a record")
        labels = {field: per_app[_APP_KEY] for field, per_app in FIELD_LABELS.items()}
        self.setWindowTitle(f"Record — {record.full_name}" if record is not None else "Add Record")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.first_name = QLineEdit(record.first_name if record is not None else "")
        self.last_name = QLineEdit(record.last_name if record is not None else "")
        self.company = QLineEdit(record.company if record is not None else "")
        self.phone = QLineEdit(record.phone if record is not None else "")
        self.email = QLineEdit(record.email if record is not None else "")
        self.status = QComboBox()
        self.status.addItems(list(STATUS_VALUES))
        self.status.setCurrentText(record.status if record is not None else "Lead")
        self.owner = QLineEdit(record.owner if record is not None else "")
        self.notes = QPlainTextEdit(record.notes if record is not None else "")
        self.notes.setFixedHeight(110)
        for label_text, widget in (
            (labels["first_name"], self.first_name),
            (labels["last_name"], self.last_name),
            (labels["company"], self.company),
            (labels["phone"], self.phone),
            (labels["email"], self.email),
            (labels["status"], self.status),
            (labels["owner"], self.owner),
            (labels["notes"], self.notes),
        ):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            form.addRow(label, widget)
        layout.addLayout(form)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("fieldError")
        layout.addWidget(self.validation_label)

        buttons = QHBoxLayout()
        discard = QPushButton("Discard")
        discard.clicked.connect(self.reject)
        buttons.addWidget(discard)
        buttons.addStretch(1)
        self.commit_button = QPushButton("Commit Changes" if record is not None else "Add Record")
        self.commit_button.setObjectName("primaryAction")
        self.commit_button.setDefault(True)
        self.commit_button.setAutoDefault(True)
        self.commit_button.clicked.connect(self._validate_and_accept)
        buttons.addWidget(self.commit_button)
        layout.addLayout(buttons)

        self._field_shortcuts = [
            self._shortcut("Meta+Shift+F", lambda: self._focus_and_select(self.first_name)),
            self._shortcut("Meta+Shift+L", lambda: self._focus_and_select(self.last_name)),
            self._shortcut("Meta+Shift+C", lambda: self._focus_and_select(self.company)),
            self._shortcut("Meta+Shift+P", lambda: self._focus_and_select(self.phone)),
            self._shortcut("Meta+Shift+E", lambda: self._focus_and_select(self.email)),
            self._shortcut("Meta+Shift+T", self.status.setFocus),
            self._shortcut("Meta+Shift+O", lambda: self._focus_and_select(self.owner)),
            self._shortcut("Meta+Shift+N", lambda: self._focus_and_select(self.notes)),
            self._shortcut("Meta+S", self._validate_and_accept),
        ]
        if record is not None:
            QTimer.singleShot(0, lambda: self._focus_and_select(self.last_name))

    def _shortcut(self, keys: str, action: Callable[[], object]) -> QShortcut:
        shortcut = QShortcut(QKeySequence(keys), self)
        shortcut.activated.connect(action)
        return shortcut

    @staticmethod
    def _focus_and_select(widget: QLineEdit | QPlainTextEdit) -> None:
        widget.setFocus()
        widget.selectAll()

    def edited_record(self) -> ContactRecord:
        """Return the record with the dialog's current field values applied."""
        values = {
            "id": self._record_id,
            "first_name": self.first_name.text().strip(),
            "last_name": self.last_name.text().strip(),
            "company": self.company.text().strip() or "Not provided",
            "phone": self.phone.text().strip() or "Not provided",
            "email": self.email.text().strip() or "Not provided",
            "status": self.status.currentText(),
            "owner": self.owner.text().strip() or "Unassigned",
            "notes": self.notes.toPlainText().strip(),
        }
        return ContactRecord.model_validate(values)

    def _validate_and_accept(self) -> None:
        if not self.first_name.text().strip() or not self.last_name.text().strip():
            self.validation_label.setText("Given name and Family name are required")
            return
        self.accept()


class CrmBWindow(QMainWindow):
    """Main window holding the in-memory working state for CRM B."""

    def __init__(self, path: Path):
        """Load persisted state once and build the tabbed UI.

        Args:
            path: JSON state file backing this instance.
        """
        super().__init__()
        self._path = path
        self._state: CrmState = load_state(path)
        self._visible_indices: list[int] = []
        self.setWindowTitle("Meridian Contacts")
        fix_window_geometry(self)
        self._build_ui()
        self.run_search()

    def run_search(self) -> None:
        """Filter the results table by the search box (name or organisation)."""
        needle = self._search_box.text().strip().lower()
        self._visible_indices = [
            index
            for index, record in enumerate(self._state.records)
            if not needle or needle in record.full_name.lower() or needle in record.company.lower()
        ]
        self._results.setRowCount(len(self._visible_indices))
        for row, index in enumerate(self._visible_indices):
            record = self._state.records[index]
            for column, value in enumerate((record.first_name, record.last_name, record.company, record.status)):
                self._results.setItem(row, column, QTableWidgetItem(value))
        if self._visible_indices:
            self._results.selectRow(0)
            self._results.setCurrentCell(0, 0)
        self.statusBar().showMessage(f"{len(self._visible_indices)} record(s)")

    def open_selected_record(self) -> None:
        """Open the modal editor for the highlighted row; persist on commit."""
        row = self._results.currentRow()
        if row < 0 or row >= len(self._visible_indices):
            self.statusBar().showMessage("Select a record first", 5_000)
            return
        index = self._visible_indices[row]
        dialog = RecordDialog(self._state.records[index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_commit(index, dialog.edited_record())

    def apply_commit(self, index: int, record: ContactRecord) -> None:
        """Write one edited record through to persisted state.

        Args:
            index: Position of the record in the store.
            record: Edited replacement record.
        """
        self._state.records[index] = record
        write_state_atomic(self._path, self._state)
        self.run_search()
        self.statusBar().showMessage(f"Committed {record.full_name}", 5_000)

    def add_record(self) -> None:
        """Open a blank modal and persist one new record only after explicit confirmation."""
        dialog = RecordDialog(None, self, record_id=next_contact_id(self._state))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_create(dialog.edited_record())

    def apply_create(self, record: ContactRecord) -> None:
        """Append one validated new record and persist it atomically."""
        self._state.records.append(record)
        write_state_atomic(self._path, self._state)
        self.run_search()
        self.statusBar().showMessage(f"Added {record.full_name}", 5_000)

    def _build_ui(self) -> None:
        tabs = QTabWidget()

        directory = QWidget()
        directory_layout = QVBoxLayout(directory)
        search_row = QHBoxLayout()
        search_label = QLabel("Find contact")
        search_label.setObjectName("fieldLabel")
        search_row.addWidget(search_label)
        self._search_box = QLineEdit()
        self._search_box.returnPressed.connect(self._search_and_open_exact)
        search_row.addWidget(self._search_box, 1)
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.run_search)
        search_row.addWidget(search_button)
        directory_layout.addLayout(search_row)

        self._results = QTableWidget(0, len(_TABLE_COLUMNS))
        self._results.setHorizontalHeaderLabels(list(_TABLE_COLUMNS))
        self._results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._results.itemDoubleClicked.connect(self.open_selected_record)
        self._results.horizontalHeader().setStretchLastSection(True)
        self._results.setColumnWidth(0, 220)
        self._results.setColumnWidth(1, 220)
        self._results.setColumnWidth(2, 380)
        directory_layout.addWidget(self._results)

        open_row = QHBoxLayout()
        self.add_button = QPushButton("Add Record")
        self.add_button.clicked.connect(self.add_record)
        open_row.addWidget(self.add_button)
        self.open_button = QPushButton("Open Record…")
        self.open_button.setObjectName("primaryAction")
        self.open_button.clicked.connect(self.open_selected_record)
        open_row.addWidget(self.open_button)
        open_row.addStretch(1)
        directory_layout.addLayout(open_row)
        tabs.addTab(directory, "Directory")

        about = QWidget()
        about_layout = QVBoxLayout(about)
        about_layout.addWidget(QLabel("Meridian Contacts — internal directory fixture."))
        about_layout.addStretch(1)
        tabs.addTab(about, "About")

        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Ready")
        self._holo_overlay = HoloOverlay(self, _APP_KEY)
        self._navigation_shortcuts = [
            self._shortcut("Meta+F", self._focus_search),
            self._shortcut("Meta+O", self.open_selected_record),
            self._shortcut("Meta+N", self.add_record),
        ]

    def _shortcut(self, keys: str, action: Callable[[], object]) -> QShortcut:
        shortcut = QShortcut(QKeySequence(keys), self)
        shortcut.activated.connect(action)
        return shortcut

    def _focus_search(self) -> None:
        self._search_box.setFocus()
        self._search_box.selectAll()

    def _search_and_open_exact(self) -> None:
        self.run_search()
        needle = self._search_box.text().strip().casefold()
        if len(self._visible_indices) != 1:
            return
        record = self._state.records[self._visible_indices[0]]
        if record.full_name.casefold() == needle:
            self.open_selected_record()


def main() -> None:
    """Launch CRM B against its default state file."""
    app = QApplication(sys.argv)
    apply_light_fusion_style(app)
    window = CrmBWindow(state_path(_APP_KEY))
    window.show()
    window.raise_()
    window.activateWindow()
    QTimer.singleShot(0, window._focus_search)
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
