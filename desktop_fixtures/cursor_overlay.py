"""Click-through red crosshair overlay for visible demo mouse movement."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

CROSSHAIR_SIZE = 48
CROSSHAIR_ARM_LENGTH = 21
CROSSHAIR_GAP = 6
CROSSHAIR_LINE_WIDTH = 4
CURSOR_REFRESH_MILLISECONDS = 16


class CrosshairOverlay(QWidget):
    """Always-on-top mouse marker that never receives input."""

    def __init__(self, size: int = CROSSHAIR_SIZE):
        """Create and start the pointer-following overlay.

        Args:
            size: Square overlay size in pixels.
        """
        super().__init__()
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.follow_pointer)
        self._timer.start(CURSOR_REFRESH_MILLISECONDS)
        self.follow_pointer()

    def follow_pointer(self) -> None:
        """Center the overlay on the current global pointer position."""
        position = QCursor.pos()
        self.move(position.x() - self.width() // 2, position.y() - self.height() // 2)
        self.raise_()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint a high-contrast red crosshair.

        Args:
            event: Qt paint event for the overlay surface.
        """
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center_x = self.width() // 2
        center_y = self.height() // 2
        outline = QPen(QColor("#ffffff"), CROSSHAIR_LINE_WIDTH + 3)
        outline.setCapStyle(Qt.PenCapStyle.RoundCap)
        red_color = QColor("#ff1010")
        red = QPen(red_color, CROSSHAIR_LINE_WIDTH)
        red.setCapStyle(Qt.PenCapStyle.RoundCap)
        segments = (
            (center_x - CROSSHAIR_ARM_LENGTH, center_y, center_x - CROSSHAIR_GAP, center_y),
            (center_x + CROSSHAIR_GAP, center_y, center_x + CROSSHAIR_ARM_LENGTH, center_y),
            (center_x, center_y - CROSSHAIR_ARM_LENGTH, center_x, center_y - CROSSHAIR_GAP),
            (center_x, center_y + CROSSHAIR_GAP, center_x, center_y + CROSSHAIR_ARM_LENGTH),
        )
        for pen in (outline, red):
            painter.setPen(pen)
            for start_x, start_y, end_x, end_y in segments:
                painter.drawLine(start_x, start_y, end_x, end_y)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#ffffff"), 6))
        painter.drawEllipse(center_x - 9, center_y - 9, 18, 18)
        painter.setPen(QPen(red_color, 3))
        painter.drawEllipse(center_x - 9, center_y - 9, 18, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(red_color)
        painter.drawEllipse(center_x - 3, center_y - 3, 6, 6)


def main() -> None:
    """Run the red crosshair overlay until interrupted."""
    app = QApplication(sys.argv)
    app.setApplicationName("Foundry Demo Cursor")
    app.setQuitOnLastWindowClosed(False)
    overlay = CrosshairOverlay()
    overlay.show()
    overlay.raise_()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
