"""Shared vector Oracle map with stable IDs, numbering, labels and editable positions."""
from collections import defaultdict
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from config.defaults import DEFAULT_MAP_POSITIONS, DEFAULT_ORACLE_LABELS
from config.schema import MapPosition
from encounter.vog_oracles import OracleId


def copy_positions(positions=None):
    source = positions or DEFAULT_MAP_POSITIONS
    return {key: replace(point) if isinstance(point, MapPosition) else MapPosition(*point)
            for key, point in source.items()}


def node_rects(area, positions, scale=1):
    width = min(104.0 * scale, max(66.0 * scale, area.width() * .16))
    height = 34.0 * scale
    horizontal, vertical = width / 2 + 4, height / 2 + 3
    bounds = area.adjusted(horizontal, vertical, -horizontal, -vertical)
    return {OracleId(key): QRectF(bounds.left() + point.x * bounds.width() - width / 2,
                                 bounds.top() + point.y * bounds.height() - height / 2, width, height)
            for key, point in positions.items()}


def draw_map(painter, area, positions, labels, sequence=(), status="ARMED", editing=False, scale=1):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#111b2b"))
    painter.drawRoundedRect(area, 12, 12)
    rects = node_rects(area, positions, scale)
    indices = defaultdict(list)
    for index, oracle in enumerate(sequence, 1):
        if oracle is not None:
            indices[oracle].append(str(index))
    color = QColor("#81d6b5" if status == "CONFIRMED" else "#eac16f" if status in ("INFERRED", "CHECK", "UNCERTAIN") else "#f29aaa" if status == "MISMATCH" else "#a8c8ff")
    line = QColor(color)
    line.setAlpha(110)
    painter.setPen(QPen(line, 2, Qt.PenStyle.DashLine if status != "CONFIRMED" else Qt.PenStyle.SolidLine))
    for first, second in zip(sequence, sequence[1:]):
        if first is not None and second is not None:
            painter.drawLine(rects[first].center(), rects[second].center())
    for oracle, rect in rects.items():
        assigned = indices[oracle]
        painter.setPen(QPen(color if assigned else QColor("#50647f"), 2 if assigned else 1,
                            Qt.PenStyle.DashLine if assigned and status != "CONFIRMED" else Qt.PenStyle.SolidLine))
        painter.setBrush(QColor("#233b39" if assigned and status == "CONFIRMED" else "#323024" if assigned else "#182538"))
        painter.drawRoundedRect(rect, 8, 8)
        font = QFont("Yu Gothic UI", max(6, round(10 * scale)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(color if assigned else QColor("#e4ecf8"))
        title = (", ".join(assigned) + " · " if assigned else "") + oracle.value
        painter.drawText(rect.adjusted(3, 0, -3, -17 * scale), Qt.AlignmentFlag.AlignCenter, title)
        font.setBold(False)
        font.setPointSize(max(6, round(9 * scale)))
        painter.setFont(font)
        painter.setPen(QColor("#d2dfef"))
        label = QFontMetrics(font).elidedText(labels[oracle.value], Qt.TextElideMode.ElideRight, round(rect.width() - 8))
        painter.drawText(rect.adjusted(4, 16 * scale, -4, 0), Qt.AlignmentFlag.AlignCenter, label)
    if editing:
        painter.setPen(QColor("#eac16f"))
        painter.setFont(QFont("Yu Gothic UI", 9))
        painter.drawText(area.adjusted(8, 2, -8, -2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "配置調整中：Oracleをドラッグ")


class OracleMapWidget(QWidget):
    positions_changed = Signal(object)

    def __init__(self, labels=None, positions=None, parent=None):
        super().__init__(parent)
        self.labels, self.positions = dict(labels or DEFAULT_ORACLE_LABELS), copy_positions(positions)
        self.sequence, self.status = (), "ARMED"
        self.editing, self._dragged = False, None
        self.setMinimumHeight(180)
        self.setAccessibleName("Oracle Map")
        self.setMouseTracking(True)

    def set_result(self, presentation):
        self.sequence, self.status = presentation.sequence, presentation.status
        ordered = " / ".join(f"{i} {oracle.value if oracle else '?'}" for i, oracle in enumerate(self.sequence, 1))
        self.setAccessibleDescription(presentation.state_text + " / " + ordered)
        self.update()

    def set_labels(self, labels):
        self.labels = dict(labels)
        self.update()

    def set_positions(self, positions):
        self.positions = copy_positions(positions)
        self.update()

    def set_edit_mode(self, enabled):
        self.editing = enabled
        self._dragged = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        draw_map(painter, QRectF(self.rect()).adjusted(1, 1, -1, -1),
                 self.positions, self.labels, self.sequence, self.status, self.editing, scale=min(1, self.height() / 200))

    def mousePressEvent(self, event):
        if self.editing and event.button() == Qt.MouseButton.LeftButton:
            rects = node_rects(QRectF(self.rect()), self.positions, min(1, self.height() / 200))
            self._dragged = next((oracle for oracle, rect in rects.items() if rect.contains(event.position())), None)
            if self._dragged:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragged:
            rects = node_rects(QRectF(self.rect()), self.positions, min(1, self.height() / 200))
            rect = rects[self._dragged]
            hx, vy = rect.width() / 2 + 4, rect.height() / 2 + 3
            self.positions[self._dragged.value] = MapPosition(
                max(0, min(1, (event.position().x() - hx) / max(1, self.width() - hx * 2))),
                max(0, min(1, (event.position().y() - vy) / max(1, self.height() - vy * 2))))
            self.update()
            event.accept()
        else:
            rects = node_rects(QRectF(self.rect()), self.positions, min(1, self.height() / 200))
            oracle = next((key for key, rect in rects.items() if rect.contains(event.position())), None)
            self.setToolTip(f"{oracle.value} · {self.labels[oracle.value]}" if oracle else "")
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragged:
            self._dragged = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.positions_changed.emit(copy_positions(self.positions))
            event.accept()
        else:
            super().mouseReleaseEvent(event)
