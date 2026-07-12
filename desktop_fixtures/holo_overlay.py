"""Click-through in-app visualization of Holo's current observation and target."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class HoloOverlay(QWidget):
    """Render safe Holo action metadata without accepting mouse or keyboard input."""

    def __init__(self, parent: QWidget, app_key: str):
        super().__init__(parent)
        self._host = parent
        self._app_key = app_key
        configured_path = os.getenv("FOUNDRY_HOLO_OVERLAY_PATH", "").strip()
        self._path = Path(configured_path) if configured_path else None
        self._state: dict[str, object] = {}
        self._last_mtime_ns = -1
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        if self._path is not None:
            self._timer.start()

    def _poll(self) -> None:
        self.setGeometry(self._host.rect())
        path = self._path
        if path is None:
            return
        try:
            stat = path.stat()
            if stat.st_mtime_ns != self._last_mtime_ns:
                state = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    self._state = state
                    self._last_mtime_ns = stat.st_mtime_ns
            visible = bool(self._state.get("visible")) and self._state.get("app") == self._app_key
            expires_value = self._state.get("expires_at", 0.0)
            expires_at = float(expires_value) if isinstance(expires_value, (int, float)) else 0.0
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            visible = False
        if not visible or expires_at < time.time():
            self.hide()
            return
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        red = QColor(235, 45, 55)
        painter.setPen(QPen(red, 5))
        painter.setBrush(QColor(235, 45, 55, 12))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 10, 10)
        self._draw_badge(painter, red)
        self._draw_pointer(painter)
        self._draw_target(painter, red)

    def _draw_badge(self, painter: QPainter, red: QColor) -> None:
        label = str(self._state.get("label", "Holo observing"))[:120]
        badge = QRectF(18, 18, min(max(260, self.width() - 36), 850), 44)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, 225))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setPen(red)
        painter.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        painter.drawText(badge.adjusted(14, 0, -14, 0), Qt.AlignmentFlag.AlignVCenter, f"● HOLO  {label}")

    def _draw_pointer(self, painter: QPainter) -> None:
        cursor = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(cursor):
            return
        point = QPointF(cursor)
        pointer = QPolygonF(
            [
                point,
                point + QPointF(0, 25),
                point + QPointF(7, 18),
                point + QPointF(13, 30),
                point + QPointF(19, 27),
                point + QPointF(13, 15),
                point + QPointF(24, 15),
            ]
        )
        painter.setPen(QPen(QColor(15, 15, 18), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawPolygon(pointer)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawPolygon(pointer)

    def _draw_target(self, painter: QPainter, red: QColor) -> None:
        target = self._state.get("target")
        if not isinstance(target, dict):
            return
        element = str(target.get("element", "target"))[:80]
        try:
            normalized_x = float(target["x"])
            normalized_y = float(target["y"])
        except (KeyError, TypeError, ValueError):
            self._draw_keyboard_target(painter, red, element)
            return
        screen = self.window().screen()
        if screen is None:
            return
        screen_geometry = screen.geometry()
        global_target = QPoint(
            round(screen_geometry.x() + normalized_x * screen_geometry.width()),
            round(screen_geometry.y() + normalized_y * screen_geometry.height()),
        )
        local_target = self.mapFromGlobal(global_target)
        x = min(max(local_target.x(), 20), self.width() - 20)
        y = min(max(local_target.y(), 80), self.height() - 20)
        painter.setPen(QPen(red, 4))
        painter.setBrush(QColor(235, 45, 55, 45))
        painter.drawEllipse(QPointF(x, y), 18, 18)
        painter.drawLine(x - 30, y, x + 30, y)
        painter.drawLine(x, y - 30, x, y + 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(red)
        label_rect = QRectF(min(x + 24, self.width() - 370), max(72, y - 22), 345, 36)
        painter.drawRoundedRect(label_rect, 6, 6)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        painter.drawText(
            label_rect.adjusted(10, 0, -10, 0),
            Qt.AlignmentFlag.AlignVCenter,
            f"TARGET  {element}",
        )

    def _draw_keyboard_target(self, painter: QPainter, red: QColor, element: str) -> None:
        label_rect = QRectF(max(18, self.width() - 388), 18, 370, 44)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(red)
        painter.drawRoundedRect(label_rect, 8, 8)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        painter.drawText(
            label_rect.adjusted(12, 0, -12, 0),
            Qt.AlignmentFlag.AlignVCenter,
            f"TARGET  {element}",
        )
