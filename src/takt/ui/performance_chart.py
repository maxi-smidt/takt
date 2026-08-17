from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from takt.domain.run import Run


class PerformanceChart(QWidget):
    """Small dependency-free Qt chart optimized for the Pi dashboard."""

    def __init__(self) -> None:
        super().__init__()
        self._runs: list[Run] = []
        self._points: list[tuple[QPointF, Run]] = []
        self.setMinimumHeight(150)
        self.setMouseTracking(True)

    def set_runs(self, runs: list[Run]) -> None:
        self._runs = runs
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0d1822"))
        area = QRectF(52, 18, max(10, self.width() - 72), max(10, self.height() - 57))
        self._draw_grid(painter, area)
        self._points = []
        if not self._runs:
            painter.setPen(QColor("#718493"))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "Noch keine Läufe gespeichert")
            return

        maximum = max(run.total_time.milliseconds for run in self._runs)
        maximum = max(10_000, int(maximum * 1.15))
        count = len(self._runs)

        def point_for(index: int, milliseconds: int) -> QPointF:
            offset = area.width() / 2 if count == 1 else index * area.width() / (count - 1)
            x = area.left() + offset
            y = area.bottom() - (milliseconds / maximum) * area.height()
            return QPointF(x, y)

        actual_points = [
            point_for(index, run.actual_time.milliseconds) for index, run in enumerate(self._runs)
        ]
        total_points = [
            point_for(index, run.total_time.milliseconds) for index, run in enumerate(self._runs)
        ]
        for actual, total in zip(actual_points, total_points, strict=True):
            painter.setPen(QPen(QColor("#f1a817"), 4))
            painter.drawLine(actual, total)
        self._draw_line(painter, actual_points, QColor("#2aa9ff"))
        self._draw_line(painter, total_points, QColor("#e6edf2"))

        label_step = max(1, count // 5)
        painter.setFont(QFont("Arial", 8))
        painter.setPen(QColor("#8093a1"))
        for index, (point, run) in enumerate(zip(total_points, self._runs, strict=True)):
            self._points.append((point, run))
            if index % label_step == 0 or index == count - 1:
                label = run.started_at.astimezone().strftime("%d.%m. %H:%M")
                painter.drawText(
                    QRectF(point.x() - 43, area.bottom() + 8, 86, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )

        self._draw_legend(painter)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        for point, run in self._points:
            if (position - point).manhattanLength() < 13:
                self.setToolTip(
                    f"{run.started_at.astimezone():%d.%m.%Y, %H:%M} · Lauf {run.run_number}\n"
                    f"Ist-Zeit: {run.actual_time.format_stopwatch()}\n"
                    f"Zuschlag: {run.added_time.format_added()}\n"
                    f"Gesamtzeit: {run.total_time.format_stopwatch()}"
                )
                return
        self.setToolTip("")

    @staticmethod
    def _draw_grid(painter: QPainter, area: QRectF) -> None:
        painter.setFont(QFont("Arial", 8))
        for step in range(5):
            y = area.top() + step * area.height() / 4
            painter.setPen(QPen(QColor("#1c2b36"), 1))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        painter.setPen(QPen(QColor("#2a3d4b"), 1))
        painter.drawRect(area)

    @staticmethod
    def _draw_line(painter: QPainter, points: list[QPointF], color: QColor) -> None:
        painter.setPen(QPen(color, 2))
        for previous, current in zip(points, points[1:], strict=False):
            painter.drawLine(previous, current)
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#07111a"), 1))
        for point in points:
            painter.drawEllipse(point, 4, 4)

    def _draw_legend(self, painter: QPainter) -> None:
        entries = [
            ("Ist-Zeit", QColor("#2aa9ff")),
            ("Zuschlag", QColor("#f1a817")),
            ("Gesamtzeit", QColor("#e6edf2")),
        ]
        x = self.width() - 265
        for label, color in entries:
            painter.setPen(QPen(color, 3))
            painter.drawLine(x, 10, x + 14, 10)
            painter.setPen(QColor("#aebbc5"))
            painter.drawText(x + 19, 14, label)
            x += 85
