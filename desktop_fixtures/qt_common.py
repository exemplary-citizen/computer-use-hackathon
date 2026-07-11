"""Shared Qt chrome enforcing the vision-legibility contract for both fixtures.

Rules (from the plan review): >=13pt text everywhere, >=4.5:1 contrast,
text-labeled buttons only, fixed 1280x800 window at a fixed origin, light
mode forced regardless of the host theme.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
WINDOW_ORIGIN_X = 80
WINDOW_ORIGIN_Y = 60

_BASE_STYLESHEET = """
* { font-size: 14px; color: #1a1a1a; }
QMainWindow, QDialog { background: #f7f6f2; }
QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QTableWidget {
    background: #ffffff; border: 1px solid #8a8a8a; border-radius: 3px; padding: 4px;
}
QPushButton {
    background: #ffffff; border: 1px solid #5a5a5a; border-radius: 3px; padding: 7px 18px;
}
QPushButton#primaryAction {
    background: #14532d; color: #ffffff; border: 1px solid #14532d; font-weight: 600;
}
QLabel#fieldLabel { font-weight: 600; }
QStatusBar { font-size: 13px; }
"""


def apply_light_fusion_style(app: QApplication) -> None:
    """Force a light, high-contrast Fusion look independent of macOS dark mode.

    Args:
        app: The running application instance.
    """
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f7f6f2"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#1d4ed8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    app.setStyleSheet(_BASE_STYLESHEET)


def fix_window_geometry(window: QWidget) -> None:
    """Pin a fixture window to the agreed fixed size and origin.

    Args:
        window: Top-level fixture window.
    """
    window.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
    window.move(WINDOW_ORIGIN_X, WINDOW_ORIGIN_Y)
