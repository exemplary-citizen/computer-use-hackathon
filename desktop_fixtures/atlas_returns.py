"""Atlas Returns Desk — a visually dense, siloed retail returns workstation."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCloseEvent, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop_fixtures.returns_store import (
    ASSIGNEES,
    CASE_STATUSES,
    CUSTOMER_TIERS,
    DISPOSITION_VALUES,
    RESOLUTION_VALUES,
    WAREHOUSE_ROUTES,
    ReturnCase,
    ReturnsState,
    TimelineEvent,
    load_returns_state,
    now_timestamp,
    reset_returns_state,
    returns_state_path,
    write_returns_state,
)

WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900
WINDOW_ORIGIN_X = 28
WINDOW_ORIGIN_Y = 32
UPDATE_RESET_MILLISECONDS = 5_000

_ATLAS_STYLESHEET = """
* { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 12px; color: #1f2933; }
QMainWindow, QDialog { background: #d9dde1; }
QMenuBar { background: #e8eaec; border-bottom: 1px solid #8b949e; padding: 2px; }
QMenuBar::item:selected, QMenu::item:selected { background: #c9d7e3; }
QMenu { background: #f1f2f3; border: 1px solid #79828b; }
QStatusBar { background: #e7e9eb; border-top: 1px solid #9aa2aa; color: #3f4d59; }
QFrame#header { background: #18364a; border-bottom: 3px solid #d19a36; }
QLabel#brand { color: #ffffff; font-size: 20px; font-weight: 700; }
QLabel#subtitle { color: #bfd0dc; font-size: 11px; }
QLabel#environmentBadge { background: #a92f2f; color: white; padding: 3px 8px; font-weight: 700; }
QLabel#userBadge { background: #284f67; color: #eaf2f7; padding: 4px 9px; border: 1px solid #5e7c8f; }
QFrame#sidebar { background: #263f50; border-right: 1px solid #172b38; }
QListWidget#navigation { background: #263f50; color: #e6edf2; border: 0; outline: 0; padding-top: 7px; }
QListWidget#navigation::item { color: #e6edf2; padding: 11px 12px; border-bottom: 1px solid #345468; }
QListWidget#navigation::item:selected { background: #d19a36; color: #17232c; font-weight: 700; }
QListWidget#navigation::item:hover { background: #35576c; }
QFrame#toolbar { background: #eef0f2; border-bottom: 1px solid #9ba4ad; }
QPushButton { background: #f6f7f8; border: 1px solid #7e8790; border-radius: 2px; padding: 6px 10px; }
QPushButton:hover { background: #e2e8ed; }
QPushButton:pressed { background: #cfd9e0; }
QPushButton#primaryAction { background: #24633f; color: white; border: 1px solid #164d2e; font-weight: 700; padding: 8px 14px; }
QPushButton#dangerAction { background: #9e3a32; color: white; border: 1px solid #72251f; font-weight: 700; }
QPushButton#warningAction { background: #d7a23d; color: #17232c; border: 1px solid #9b7225; font-weight: 700; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
    background: white; border: 1px solid #7f8992; border-radius: 1px; padding: 4px;
}
QTableWidget { background: white; alternate-background-color: #eef3f6; gridline-color: #b6bec5; border: 1px solid #818a93; }
QHeaderView::section { background: #445d6e; color: white; padding: 6px; border: 0; border-right: 1px solid #657a88; font-weight: 700; }
QGroupBox { background: #edf0f2; border: 1px solid #929ba3; margin-top: 9px; padding-top: 10px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QTabWidget::pane { background: #f4f5f6; border: 1px solid #89929a; }
QTabBar::tab { background: #d4d9dd; border: 1px solid #9099a1; padding: 7px 12px; }
QTabBar::tab:selected { background: #f5f6f7; border-bottom-color: #f5f6f7; font-weight: 700; }
QLabel#sectionTitle { font-size: 16px; font-weight: 700; color: #263f50; }
QLabel#metricValue { font-size: 22px; font-weight: 700; color: #18364a; }
QLabel#metricLabel { color: #566573; font-size: 11px; }
QFrame#metricCard { background: #f6f7f8; border: 1px solid #9ca5ad; border-left: 4px solid #d19a36; }
QLabel#fieldName { color: #5e6a74; font-weight: 700; }
QLabel#riskFlag { background: #f4d6d3; color: #7b211b; border: 1px solid #cc8e89; padding: 3px 6px; font-weight: 700; }
QLabel#policyHint { background: #fff4cf; border: 1px solid #d3b660; color: #514316; padding: 7px; }
QProgressBar { border: 1px solid #79838b; background: white; text-align: center; }
QProgressBar::chunk { background: #4c7a98; }
"""


class AtlasReturnsWindow(QMainWindow):
    """Main Atlas Returns Desk window backed by deterministic JSON state."""

    def __init__(self, path: Path):
        """Load fixture state and construct the legacy business UI.

        Args:
            path: JSON state file backing the application.
        """
        super().__init__()
        self._path = path
        self._state: ReturnsState = load_returns_state(path)
        self._current_case_id: str | None = None
        self._visible_case_ids: list[str] = []
        self.setWindowTitle("Atlas Returns Desk")
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.move(WINDOW_ORIGIN_X, WINDOW_ORIGIN_Y)
        self.setWindowIcon(_app_icon())
        self._build_ui()
        self.filter_cases()
        if self._queue.rowCount():
            self._queue.selectRow(0)

    def filter_cases(self) -> None:
        """Apply search, status, and tier filters to the visible work queue."""
        query = self._search.text().strip().casefold()
        status_filter = self._status_filter.currentText()
        tier_filter = self._tier_filter.currentText()
        matches = []
        for case in self._state.cases:
            haystack = " ".join(
                (case.case_id, case.order_number, case.customer_name, case.product_name, case.sku, case.return_reason)
            ).casefold()
            if query and query not in haystack:
                continue
            if status_filter != "All statuses" and case.status != status_filter:
                continue
            if tier_filter != "All tiers" and case.customer_tier != tier_filter:
                continue
            matches.append(case)
        self._visible_case_ids = [case.case_id for case in matches]
        self._queue.setRowCount(len(matches))
        for row, case in enumerate(matches):
            values = (
                case.case_id,
                case.customer_name,
                case.customer_tier,
                case.product_name,
                case.return_reason.split(".")[0],
                case.status,
                _sla_text(case.sla_hours_remaining),
                case.assignee,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 6:
                    item.setForeground(QColor("#a12620" if case.sla_hours_remaining <= 3 else "#1f2933"))
                if column == 5 and case.status == "Resolved":
                    item.setForeground(QColor("#247244"))
                self._queue.setItem(row, column, item)
        self._result_count.setText(f"{len(matches)} cases")
        if matches:
            selected_row = (
                self._visible_case_ids.index(self._current_case_id)
                if self._current_case_id in self._visible_case_ids
                else 0
            )
            self._queue.selectRow(selected_row)
            self._load_selected_row()
        else:
            self._current_case_id = None
        self.statusBar().showMessage(f"Queue refreshed — {len(matches)} matching cases", 4_000)

    def save_draft(self) -> None:
        """Persist decision fields without resolving the selected case."""
        case = self._selected_case()
        if case is None:
            return
        prior_status = case.status
        self._copy_decision_fields(case)
        case.status = prior_status
        case.timeline.append(
            TimelineEvent(
                occurred_at=now_timestamp(),
                actor="Maya Chen",
                action="Decision draft saved",
                detail=f"Draft resolution {case.resolution}; disposition {case.disposition_code}.",
            )
        )
        write_returns_state(self._path, self._state)
        self._render_timeline(case)
        self.statusBar().showMessage(f"Draft saved for {case.case_id}; case remains {case.status}", 5_000)

    def validate_decision(self, show_dialog: bool = True) -> list[str]:
        """Validate the visible workbench against core return-policy constraints.

        Args:
            show_dialog: Whether to display the validation result.

        Returns:
            Human-readable validation failures; empty means ready to apply.
        """
        case = self._selected_case()
        if case is None:
            return ["Select a case first."]
        errors = []
        if self._resolution.currentText() == "Pending Review":
            errors.append("Choose a final resolution.")
        if self._disposition.currentText() == "UNASSESSED":
            errors.append("Choose a disposition code.")
        if self._warehouse.currentText() == "Unassigned":
            errors.append("Choose a warehouse route.")
        if not self._internal_note.toPlainText().strip():
            errors.append("Enter an internal decision note.")
        if self._disposition.currentText() == "HAZMAT_INSPECTION" and self._warehouse.currentText() != "WH-HAZ-02":
            errors.append("HAZMAT_INSPECTION must route to WH-HAZ-02.")
        if "PREMIUM CUSTOMER" in case.risk_flags and self._restocking_fee.value() > 0:
            errors.append("Premium customers require a waived restocking fee.")
        if show_dialog:
            if errors:
                QMessageBox.warning(self, "Validation exceptions", "\n".join(f"• {error}" for error in errors))
            else:
                QMessageBox.information(self, "Validation passed", "No blocking policy exceptions were found.")
        self.statusBar().showMessage(
            "Validation passed" if not errors else f"Validation found {len(errors)} exception(s)"
        )
        return errors

    def apply_resolution(self) -> bool:
        """Persist the selected case resolution as the explicit business commit.

        Returns:
            True when the resolution was persisted.
        """
        case = self._selected_case()
        if case is None:
            return False
        self._copy_decision_fields(case)
        case.status = "Resolved"
        case.assignee = case.assignee if case.assignee != "Unassigned" else "Maya Chen"
        case.timeline.append(
            TimelineEvent(
                occurred_at=now_timestamp(),
                actor="Maya Chen",
                action="Resolution applied",
                detail=(
                    f"{case.resolution}; disposition {case.disposition_code}; route {case.warehouse_route}; "
                    f"refund ${case.refund_amount:,.2f}; fee ${case.restocking_fee:,.2f}."
                ),
            )
        )
        write_returns_state(self._path, self._state)
        self.filter_cases()
        self._search.setText(case.case_id)
        self._status_filter.setCurrentText("All statuses")
        self.filter_cases()
        if self._queue.rowCount():
            self._queue.selectRow(0)
        self._update_confirmation.setText("UPDATED!")
        QTimer.singleShot(UPDATE_RESET_MILLISECONDS, self._reset_after_update)
        return True

    def assign_selected_case(self) -> None:
        """Assign the selected case to a queue operator."""
        case = self._selected_case()
        if case is None:
            return
        assignee, accepted = QInputDialog.getItem(self, "Assign case", "Operator", list(ASSIGNEES[1:]), 0, False)
        if not accepted:
            return
        case.assignee = assignee
        case.timeline.append(
            TimelineEvent(
                occurred_at=now_timestamp(),
                actor="Maya Chen",
                action="Case assigned",
                detail=f"Assigned to {assignee}.",
            )
        )
        write_returns_state(self._path, self._state)
        self.filter_cases()
        self.statusBar().showMessage(f"{case.case_id} assigned to {assignee}", 5_000)

    def escalate_selected_case(self) -> None:
        """Move the selected case to the escalated queue with an audit event."""
        case = self._selected_case()
        if case is None:
            return
        case.status = "Escalated"
        case.timeline.append(
            TimelineEvent(
                occurred_at=now_timestamp(),
                actor="Maya Chen",
                action="Case escalated",
                detail="Escalated to Returns Operations supervisor queue.",
            )
        )
        write_returns_state(self._path, self._state)
        self.filter_cases()
        self.statusBar().showMessage(f"{case.case_id} escalated", 5_000)

    def add_case_note(self) -> None:
        """Append an operator note to the selected case timeline."""
        case = self._selected_case()
        if case is None:
            return
        note, accepted = QInputDialog.getMultiLineText(self, "Add case note", "Internal note")
        if not accepted or not note.strip():
            return
        case.timeline.append(
            TimelineEvent(occurred_at=now_timestamp(), actor="Maya Chen", action="Operator note", detail=note.strip())
        )
        write_returns_state(self._path, self._state)
        self._render_timeline(case)
        self.statusBar().showMessage("Case note added", 4_000)

    def preview_letter(self) -> None:
        """Display a customer-facing resolution-letter preview."""
        case = self._selected_case()
        if case is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Resolution Letter Preview — {case.case_id}")
        dialog.resize(660, 520)
        layout = QVBoxLayout(dialog)
        letter = QPlainTextEdit()
        letter.setReadOnly(True)
        letter.setPlainText(
            f"Atlas Retail Returns Operations\n\nDear {case.customer_name},\n\n"
            f"We reviewed your return request for {case.product_name} ({case.order_number}).\n\n"
            f"Proposed resolution: {self._resolution.currentText()}\n"
            f"Expected refund: ${self._refund_amount.value():,.2f}\n\n"
            "This preview has not been sent. A final notice is generated only after Apply Resolution.\n\n"
            "Regards,\nAtlas Returns Operations"
        )
        layout.addWidget(letter)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def export_queue(self, path: Path | None = None) -> Path | None:
        """Export the visible work queue as a CSV report.

        Args:
            path: Optional direct output path used by tests or automation.

        Returns:
            Written path, or None when the file dialog was cancelled.
        """
        if path is None:
            selected, _ = QFileDialog.getSaveFileName(
                self, "Export work queue", "atlas-return-queue.csv", "CSV (*.csv)"
            )
            if not selected:
                return None
            path = Path(selected)
        path.parent.mkdir(parents=True, exist_ok=True)
        by_id = {case.case_id: case for case in self._state.cases}
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(("Case ID", "Customer", "Tier", "Product", "Status", "SLA Hours", "Assignee"))
            for case_id in self._visible_case_ids:
                case = by_id[case_id]
                writer.writerow(
                    (
                        case.case_id,
                        case.customer_name,
                        case.customer_tier,
                        case.product_name,
                        case.status,
                        case.sla_hours_remaining,
                        case.assignee,
                    )
                )
        self.statusBar().showMessage(f"Exported {len(self._visible_case_ids)} cases to {path}", 6_000)
        return path

    def reset_demo_data(self, require_confirmation: bool = True) -> None:
        """Restore the canonical Atlas dataset.

        Args:
            require_confirmation: Whether to display a destructive reset confirmation.
        """
        if require_confirmation:
            choice = QMessageBox.question(
                self,
                "Reset Atlas demo data",
                "Restore every case to the canonical hackathon seed?",
                QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Reset:
                return
        self._state = reset_returns_state(self._path)
        self._search.clear()
        self._status_filter.setCurrentIndex(0)
        self._tier_filter.setCurrentIndex(0)
        self.filter_cases()
        if self._queue.rowCount():
            self._queue.selectRow(0)
        self._refresh_summary_pages()
        self.statusBar().showMessage("Canonical Atlas demo data restored", 6_000)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Restore canonical demo state whenever Atlas exits.

        Args:
            event: Qt close event for the main window.
        """
        self._state = reset_returns_state(self._path)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        self.setMenuBar(self._build_menu())
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_header())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_work_queue_page())
        self._pages.addWidget(self._build_customer_page())
        self._pages.addWidget(self._build_product_page())
        self._pages.addWidget(self._build_policy_page())
        self._pages.addWidget(self._build_reports_page())
        self._pages.addWidget(self._build_admin_page())
        body.addWidget(self._pages, 1)
        root_layout.addLayout(body, 1)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Connected to ATLAS-RMA-PROD | Ready")

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        export_action = QAction("Export Queue…", self)
        export_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        export_action.triggered.connect(lambda: self.export_queue())
        file_menu.addAction(export_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit Atlas Returns Desk", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        actions_menu = menu.addMenu("Actions")
        for label, callback, shortcut in (
            ("Assign Case…", self.assign_selected_case, "Ctrl+A"),
            ("Escalate Case", self.escalate_selected_case, "Ctrl+E"),
            ("Validate Decision", lambda: self.validate_decision(), "Ctrl+Shift+V"),
            ("Apply Resolution", lambda: self.apply_resolution(), "Ctrl+Return"),
        ):
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            actions_menu.addAction(action)
        view_menu = menu.addMenu("View")
        refresh_action = QAction("Refresh Queue", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self.filter_cases)
        view_menu.addAction(refresh_action)
        help_menu = menu.addMenu("Help")
        about_action = QAction("About Atlas Returns Desk", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        return menu

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("header")
        frame.setFixedHeight(68)
        layout = QHBoxLayout(frame)
        mark = QLabel("AR")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(42, 42)
        mark.setStyleSheet("background:#d19a36;color:#18364a;font-size:18px;font-weight:800;border:2px solid #f2c76f;")
        layout.addWidget(mark)
        titles = QVBoxLayout()
        brand = QLabel("ATLAS RETURNS DESK")
        brand.setObjectName("brand")
        subtitle = QLabel("Retail Merchandise Authorization & Disposition Workstation  •  Core 7.4.18")
        subtitle.setObjectName("subtitle")
        titles.addWidget(brand)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)
        environment = QLabel("PRODUCTION")
        environment.setObjectName("environmentBadge")
        layout.addWidget(environment)
        user = QLabel("Operator: Maya Chen  |  Returns Ops West")
        user.setObjectName("userBadge")
        layout.addWidget(user)
        return frame

    def _build_sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setFixedWidth(190)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 8, 0, 8)
        queue_label = QLabel("  OPERATIONS")
        queue_label.setStyleSheet("color:#9db2bf;font-size:10px;font-weight:700;padding:6px;")
        layout.addWidget(queue_label)
        self._navigation = QListWidget()
        self._navigation.setObjectName("navigation")
        self._navigation.addItems(
            ("▣  Work Queue", "◉  Customers", "▦  Products", "§  Policy Matrix", "▥  Reports", "⚙  Administration")
        )
        self._navigation.currentRowChanged.connect(self._pages_set_index)
        layout.addWidget(self._navigation, 1)
        sync = QLabel(
            f"  Last sync\n  {self._state.last_sync_at.replace('T', ' ')[:16]} UTC\n\n  Database: ATLAS-RMA-PROD"
        )
        sync.setStyleSheet("color:#a9bbc6;font-size:10px;padding:8px;border-top:1px solid #466276;")
        layout.addWidget(sync)
        self._navigation.setCurrentRow(0)
        return frame

    def _build_work_queue_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(7)
        layout.addWidget(self._build_action_toolbar())
        metrics = QHBoxLayout()
        self._metric_labels = {}
        for key, label in (
            ("open", "Open cases"),
            ("risk", "SLA at risk"),
            ("premium", "Premium accounts"),
            ("resolved", "Resolved"),
        ):
            card, value = _metric_card(label)
            self._metric_labels[key] = value
            metrics.addWidget(card)
        layout.addLayout(metrics)
        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search case, order, customer, product, SKU or reason…")
        self._search.returnPressed.connect(self.filter_cases)
        filters.addWidget(QLabel("Search"))
        filters.addWidget(self._search, 1)
        self._status_filter = QComboBox()
        self._status_filter.addItems(("All statuses", *CASE_STATUSES))
        self._status_filter.currentTextChanged.connect(self.filter_cases)
        filters.addWidget(self._status_filter)
        self._tier_filter = QComboBox()
        self._tier_filter.addItems(("All tiers", *CUSTOMER_TIERS))
        self._tier_filter.currentTextChanged.connect(self.filter_cases)
        filters.addWidget(self._tier_filter)
        search_button = QPushButton("Run Search")
        search_button.clicked.connect(self.filter_cases)
        filters.addWidget(search_button)
        self._result_count = QLabel()
        self._result_count.setMinimumWidth(60)
        filters.addWidget(self._result_count)
        layout.addLayout(filters)
        self._queue = QTableWidget(0, 8)
        self._queue.setHorizontalHeaderLabels(
            ("Case", "Customer", "Tier", "Product", "Reason", "Status", "SLA", "Assignee")
        )
        self._queue.setAlternatingRowColors(True)
        self._queue.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._queue.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._queue.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._queue.verticalHeader().setVisible(False)
        self._queue.setColumnWidth(0, 92)
        self._queue.setColumnWidth(1, 150)
        self._queue.setColumnWidth(2, 82)
        self._queue.setColumnWidth(3, 220)
        self._queue.setColumnWidth(4, 230)
        self._queue.setColumnWidth(5, 100)
        self._queue.setColumnWidth(6, 70)
        self._queue.horizontalHeader().setStretchLastSection(True)
        self._queue.itemSelectionChanged.connect(self._load_selected_row)
        layout.addWidget(self._queue, 2)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_case_tabs())
        splitter.addWidget(self._build_decision_workbench())
        splitter.setSizes((760, 420))
        layout.addWidget(splitter, 3)
        self._refresh_metrics()
        return page

    def _build_action_toolbar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("toolbar")
        layout = QHBoxLayout(frame)
        title = QLabel("Returns Operations Work Queue")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        layout.addStretch(1)
        for label, callback in (
            ("Refresh", self.filter_cases),
            ("Assign…", self.assign_selected_case),
            ("Escalate", self.escalate_selected_case),
            ("Add Note…", self.add_case_note),
            ("Preview Letter", self.preview_letter),
            ("Export CSV…", lambda: self.export_queue()),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
        return frame

    def _build_case_tabs(self) -> QWidget:
        self._case_tabs = QTabWidget()
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        self._case_heading = QLabel("Select a case")
        self._case_heading.setObjectName("sectionTitle")
        overview_layout.addWidget(self._case_heading)
        self._risk_row = QHBoxLayout()
        overview_layout.addLayout(self._risk_row)
        summary = QGridLayout()
        self._detail_labels = {}
        fields = (
            ("order_number", "Order"),
            ("customer", "Customer"),
            ("tier", "Customer tier"),
            ("product", "Product"),
            ("sku", "SKU"),
            ("serial", "Serial number"),
            ("purchase", "Purchased"),
            ("request", "Requested"),
            ("channel", "Channel"),
            ("condition", "Condition"),
            ("remedy", "Requested remedy"),
            ("value", "Order value"),
        )
        for index, (key, title) in enumerate(fields):
            box = QVBoxLayout()
            name = QLabel(title.upper())
            name.setObjectName("fieldName")
            value = QLabel("—")
            value.setWordWrap(True)
            self._detail_labels[key] = value
            box.addWidget(name)
            box.addWidget(value)
            summary.addLayout(box, index // 3, index % 3)
        overview_layout.addLayout(summary)
        reason_group = QGroupBox("RETURN INTAKE NARRATIVE")
        reason_layout = QVBoxLayout(reason_group)
        self._reason = QLabel("—")
        self._reason.setWordWrap(True)
        reason_layout.addWidget(self._reason)
        overview_layout.addWidget(reason_group)
        self._policy_hint = QLabel("—")
        self._policy_hint.setObjectName("policyHint")
        self._policy_hint.setWordWrap(True)
        overview_layout.addWidget(self._policy_hint)
        overview_layout.addStretch(1)
        self._case_tabs.addTab(overview, "Case Overview")

        customer_order = QWidget()
        customer_layout = QGridLayout(customer_order)
        self._customer_card = QLabel()
        self._customer_card.setWordWrap(True)
        self._order_card = QLabel()
        self._order_card.setWordWrap(True)
        customer_layout.addWidget(_wrapped_group("CUSTOMER MASTER", self._customer_card), 0, 0)
        customer_layout.addWidget(_wrapped_group("ORDER & FULFILLMENT", self._order_card), 0, 1)
        self._case_tabs.addTab(customer_order, "Customer + Order")

        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        self._timeline = QTableWidget(0, 4)
        self._timeline.setHorizontalHeaderLabels(("Timestamp", "Actor", "Event", "Details"))
        self._timeline.horizontalHeader().setStretchLastSection(True)
        self._timeline.setColumnWidth(0, 150)
        self._timeline.setColumnWidth(1, 120)
        self._timeline.setColumnWidth(2, 150)
        self._timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        timeline_layout.addWidget(self._timeline)
        self._case_tabs.addTab(timeline_page, "Timeline / Audit")

        attachments_page = QWidget()
        attachments_layout = QVBoxLayout(attachments_page)
        self._attachments = QListWidget()
        attachments_layout.addWidget(QLabel("CASE DOCUMENTS AND EVIDENCE"))
        attachments_layout.addWidget(self._attachments)
        open_attachment = QPushButton("Open Selected Attachment")
        open_attachment.clicked.connect(
            lambda: self.statusBar().showMessage("Attachment opened in secure viewer", 4_000)
        )
        attachments_layout.addWidget(open_attachment)
        self._case_tabs.addTab(attachments_page, "Attachments")
        return self._case_tabs

    def _build_decision_workbench(self) -> QWidget:
        group = QGroupBox("DECISION WORKBENCH")
        layout = QVBoxLayout(group)
        form = QFormLayout()
        self._resolution = QComboBox()
        self._resolution.addItems(RESOLUTION_VALUES)
        self._disposition = QComboBox()
        self._disposition.addItems(DISPOSITION_VALUES)
        self._warehouse = QComboBox()
        self._warehouse.addItems(WAREHOUSE_ROUTES)
        self._refund_amount = QSpinBox()
        self._refund_amount.setRange(0, 25_000)
        self._refund_amount.setPrefix("$ ")
        self._restocking_fee = QSpinBox()
        self._restocking_fee.setRange(0, 2_500)
        self._restocking_fee.setPrefix("$ ")
        self._notify_customer = QCheckBox("Generate customer notification")
        self._notify_customer.setChecked(True)
        self._internal_note = QPlainTextEdit()
        self._internal_note.setPlaceholderText("Required: concise decision rationale and handling instructions…")
        self._internal_note.setFixedHeight(76)
        for label, widget in (
            ("Resolution", self._resolution),
            ("Disposition", self._disposition),
            ("Warehouse route", self._warehouse),
            ("Refund amount", self._refund_amount),
            ("Restocking fee", self._restocking_fee),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        layout.addWidget(self._notify_customer)
        layout.addWidget(QLabel("Internal decision note"))
        layout.addWidget(self._internal_note)
        policy_check = QHBoxLayout()
        validate_button = QPushButton("Validate")
        validate_button.clicked.connect(lambda: self.validate_decision())
        policy_check.addWidget(validate_button)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear_decision)
        policy_check.addWidget(clear_button)
        save_button = QPushButton("Save Draft")
        save_button.clicked.connect(self.save_draft)
        policy_check.addWidget(save_button)
        layout.addLayout(policy_check)
        preview_button = QPushButton("Preview Customer Letter")
        preview_button.clicked.connect(self.preview_letter)
        layout.addWidget(preview_button)
        self.apply_button = QPushButton("Apply Resolution")
        self.apply_button.setObjectName("primaryAction")
        self.apply_button.clicked.connect(lambda: self.apply_resolution())
        layout.addWidget(self.apply_button)
        self._update_confirmation = QLabel("")
        self._update_confirmation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_confirmation.setStyleSheet("color:#b42318;font-size:14px;font-weight:800;")
        layout.addWidget(self._update_confirmation)
        warning = QLabel("SYSTEM OF RECORD ACTION — applies inventory routing, financial adjustment and audit event.")
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#7c2d24;font-size:10px;font-weight:700;")
        layout.addWidget(warning)
        return group

    def _build_customer_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Customer Account Directory")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        table = QTableWidget(len(self._state.cases), 5)
        table.setHorizontalHeaderLabels(("Customer", "Tier", "Email", "Open Returns", "Lifetime Value Band"))
        for row, case in enumerate(self._state.cases):
            for column, value in enumerate(
                (case.customer_name, case.customer_tier, case.customer_email, "1", _value_band(case.customer_tier))
            ):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)
        return page

    def _build_product_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Product & Disposition Catalog")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        table = QTableWidget(len(self._state.cases), 6)
        table.setHorizontalHeaderLabels(("SKU", "Product", "Category", "Unit Value", "Return Count", "Handling Class"))
        for row, case in enumerate(self._state.cases):
            handling = "Hazardous" if "LITHIUM BATTERY" in case.risk_flags else "Standard"
            for column, value in enumerate(
                (case.sku, case.product_name, case.category, f"${case.order_value:,.2f}", "1", handling)
            ):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)
        return page

    def _build_policy_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Returns Policy Matrix — Published Rules")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Effective 2026-Q3  •  Owner: Retail Operations Governance  •  Read-only production reference"
        )
        layout.addWidget(subtitle)
        rows = (
            (
                "H-04",
                "Damaged rechargeable products",
                "Any",
                "Isolate item; HAZMAT_INSPECTION; route WH-HAZ-02",
                "Critical",
            ),
            (
                "P-17",
                "Premium return window",
                "Gold / Enterprise",
                "45 days from purchase; waive restocking fee",
                "Active",
            ),
            (
                "E-09",
                "Enterprise DOA display",
                "Enterprise",
                "Expedited replacement after photo verification",
                "Active",
            ),
            ("A-12", "Serviceable audio defect", "All", "Attempt depot repair before replacement", "Active"),
            (
                "M-03",
                "Marketplace window exception",
                "Standard",
                "Supervisor approval required after 30 days",
                "Review",
            ),
            ("C-22", "Connected device replacement", "All", "Capture firmware version before authorization", "Active"),
        )
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(("Rule", "Scenario", "Customer Segment", "Required Handling", "State"))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.setColumnWidth(0, 70)
        table.setColumnWidth(1, 230)
        table.setColumnWidth(2, 150)
        table.setColumnWidth(3, 500)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(table)
        buttons = QHBoxLayout()
        for label in ("View Rule Detail", "Print Matrix", "Export Policy Snapshot"):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, text=label: self.statusBar().showMessage(f"{text} completed", 4_000)
            )
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page

    def _build_reports_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Operational Reports & Queue Health")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        cards = QHBoxLayout()
        for label, value in (
            ("First-touch SLA", "92.4%"),
            ("Avg. cycle time", "18.6 hrs"),
            ("Avoided refund leakage", "$48.2K"),
            ("Safety cases isolated", "14"),
        ):
            card, metric = _metric_card(label)
            metric.setText(value)
            cards.addWidget(card)
        layout.addLayout(cards)
        group = QGroupBox("RETURN VOLUME BY DISPOSITION")
        bars = QVBoxLayout(group)
        for name, value in (
            ("Return to stock", 72),
            ("Refurbishment", 54),
            ("Vendor return", 36),
            ("Hazmat inspection", 18),
            ("Scrap authorized", 9),
        ):
            row = QHBoxLayout()
            label = QLabel(name)
            label.setFixedWidth(140)
            bar = QProgressBar()
            bar.setValue(value)
            bar.setFormat(f"{value} cases")
            row.addWidget(label)
            row.addWidget(bar)
            bars.addLayout(row)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_admin_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Administration & Integration Health")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        systems = QTableWidget(5, 4)
        systems.setHorizontalHeaderLabels(("Subsystem", "Endpoint", "State", "Last heartbeat"))
        for row, values in enumerate(
            (
                ("Order Management", "OMS-PROD-3", "ONLINE", "12 sec ago"),
                ("Customer Master", "CDP-WEST", "ONLINE", "8 sec ago"),
                ("Warehouse Router", "WMS-ROUTE-2", "ONLINE", "21 sec ago"),
                ("Notification Service", "COMMS-GW", "DEGRADED", "2 min ago"),
                ("Financial Adjustments", "FIN-POST-1", "ONLINE", "17 sec ago"),
            )
        ):
            for column, value in enumerate(values):
                systems.setItem(row, column, QTableWidgetItem(value))
        systems.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(systems)
        settings = QGroupBox("WORKSTATION SETTINGS")
        settings_layout = QGridLayout(settings)
        for row, label in enumerate(
            (
                "Require validation before commit",
                "Generate customer notification by default",
                "Show policy hints",
                "Enable keyboard shortcuts",
            )
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            settings_layout.addWidget(checkbox, row, 0)
        reset_button = QPushButton("Reset Demo Data")
        reset_button.setObjectName("dangerAction")
        reset_button.clicked.connect(lambda: self.reset_demo_data())
        settings_layout.addWidget(reset_button, 0, 1, 2, 1)
        test_button = QPushButton("Test Integration Health")
        test_button.clicked.connect(
            lambda: QMessageBox.information(
                self, "Integration test", "4 systems online; Notification Service degraded but reachable."
            )
        )
        settings_layout.addWidget(test_button, 2, 1, 2, 1)
        layout.addWidget(settings)
        return page

    def _load_selected_row(self) -> None:
        row = self._queue.currentRow()
        if row < 0 or row >= len(self._visible_case_ids):
            return
        self._current_case_id = self._visible_case_ids[row]
        case = self._selected_case()
        if case is None:
            return
        self._case_heading.setText(f"{case.case_id}  •  {case.status}  •  Assigned: {case.assignee}")
        _clear_layout(self._risk_row)
        for flag in case.risk_flags:
            label = QLabel(flag)
            label.setObjectName("riskFlag")
            self._risk_row.addWidget(label)
        self._risk_row.addStretch(1)
        values = {
            "order_number": case.order_number,
            "customer": case.customer_name,
            "tier": case.customer_tier,
            "product": case.product_name,
            "sku": case.sku,
            "serial": case.serial_number,
            "purchase": case.purchase_date,
            "request": case.request_date,
            "channel": case.sales_channel,
            "condition": case.item_condition,
            "remedy": case.requested_remedy,
            "value": f"${case.order_value:,.2f}",
        }
        for key, value in values.items():
            self._detail_labels[key].setText(value)
        self._reason.setText(case.return_reason)
        self._policy_hint.setText(f"POLICY REFERENCE: {case.policy_hint}")
        self._customer_card.setText(
            f"<b>{case.customer_name}</b><br>{case.customer_email}<br><br>Tier: <b>{case.customer_tier}</b><br>"
            f"Account standing: Good<br>Returns in last 12 months: 1<br>Preferred contact: Email"
        )
        self._order_card.setText(
            f"Order: <b>{case.order_number}</b><br>Channel: {case.sales_channel}<br>Purchased: {case.purchase_date}<br><br>"
            f"SKU: {case.sku}<br>Serial: {case.serial_number}<br>Value: ${case.order_value:,.2f}"
        )
        self._attachments.clear()
        self._attachments.addItems(case.attachments)
        self._render_timeline(case)
        self._resolution.setCurrentText(case.resolution)
        self._disposition.setCurrentText(case.disposition_code)
        self._warehouse.setCurrentText(case.warehouse_route)
        self._refund_amount.setValue(round(case.refund_amount))
        self._restocking_fee.setValue(round(case.restocking_fee))
        self._internal_note.setPlainText(case.internal_note)
        self._update_confirmation.clear()
        self.apply_button.setEnabled(case.status != "Resolved")
        self.statusBar().showMessage(f"Loaded {case.case_id} — {case.customer_name}", 4_000)

    def _render_timeline(self, case: ReturnCase) -> None:
        events = list(reversed(case.timeline))
        self._timeline.setRowCount(len(events))
        for row, event in enumerate(events):
            for column, value in enumerate(
                (event.occurred_at.replace("T", " "), event.actor, event.action, event.detail)
            ):
                self._timeline.setItem(row, column, QTableWidgetItem(value))

    def _selected_case(self) -> ReturnCase | None:
        if self._current_case_id is None:
            self.statusBar().showMessage("Select a return case first", 4_000)
            return None
        return next((case for case in self._state.cases if case.case_id == self._current_case_id), None)

    def _copy_decision_fields(self, case: ReturnCase) -> None:
        case.resolution = self._resolution.currentText()  # type: ignore[assignment]
        case.disposition_code = self._disposition.currentText()  # type: ignore[assignment]
        case.warehouse_route = self._warehouse.currentText()
        case.refund_amount = float(self._refund_amount.value())
        case.restocking_fee = float(self._restocking_fee.value())
        case.internal_note = self._internal_note.toPlainText().strip()

    def _clear_decision(self) -> None:
        self._resolution.setCurrentIndex(0)
        self._disposition.setCurrentIndex(0)
        self._warehouse.setCurrentIndex(0)
        self._refund_amount.setValue(0)
        self._restocking_fee.setValue(0)
        self._internal_note.clear()
        self._update_confirmation.clear()

    def _refresh_metrics(self) -> None:
        self._metric_labels["open"].setText(str(sum(case.status != "Resolved" for case in self._state.cases)))
        self._metric_labels["risk"].setText(
            str(sum(case.sla_hours_remaining <= 3 and case.status != "Resolved" for case in self._state.cases))
        )
        self._metric_labels["premium"].setText(
            str(sum(case.customer_tier in ("Gold", "Enterprise") for case in self._state.cases))
        )
        self._metric_labels["resolved"].setText(str(sum(case.status == "Resolved" for case in self._state.cases)))

    def _refresh_summary_pages(self) -> None:
        self._refresh_metrics()

    def _reset_after_update(self) -> None:
        self.reset_demo_data(require_confirmation=False)

    def _pages_set_index(self, index: int) -> None:
        if hasattr(self, "_pages"):
            self._pages.setCurrentIndex(max(0, index))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Atlas Returns Desk",
            "<b>Atlas Returns Desk 7.4.18</b><br><br>Retail Merchandise Authorization and Disposition Workstation.<br>"
            "Environment: ATLAS-RMA-PROD<br>Build: 2026.07.12-hackathon",
        )


def main() -> None:
    """Launch Atlas Returns Desk against its default state file."""
    app = QApplication(sys.argv)
    app.setApplicationName("Atlas Returns Desk")
    app.setOrganizationName("Atlas Retail Systems")
    app.setStyle("Fusion")
    app.setStyleSheet(_ATLAS_STYLESHEET)
    window = AtlasReturnsWindow(returns_state_path())
    window.show()
    raise SystemExit(app.exec())


def _metric_card(label: str) -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setObjectName("metricCard")
    layout = QVBoxLayout(frame)
    value = QLabel("0")
    value.setObjectName("metricValue")
    name = QLabel(label.upper())
    name.setObjectName("metricLabel")
    layout.addWidget(value)
    layout.addWidget(name)
    return frame, value


def _wrapped_group(title: str, content: QWidget) -> QGroupBox:
    group = QGroupBox(title)
    layout = QVBoxLayout(group)
    layout.addWidget(content)
    return group


def _sla_text(hours: int) -> str:
    if hours < 0:
        return f"{abs(hours)}h late"
    return f"{hours}h"


def _value_band(tier: str) -> str:
    return {"Standard": "$", "Silver": "$$", "Gold": "$$$", "Enterprise": "$$$$"}[tier]


def _clear_layout(layout: QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _app_icon() -> QIcon:
    pixmap = QPixmap(128, 128)
    pixmap.fill(QColor("#18364a"))
    return QIcon(pixmap)


if __name__ == "__main__":
    main()
