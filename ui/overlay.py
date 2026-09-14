"""Independent translucent, non-activating Qt overlay with explicit drag mode."""
from dataclasses import replace

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from config.schema import AppConfig
from ui.oracle_map import copy_positions, draw_map
from config.defaults import DEFAULT_ORACLE_LABELS
from ui.window_geometry import Bounds, fit_to_screens


class OverlayWindow(QWidget):
    position_changed = Signal(int, int)
    enabled_changed = Signal(bool)
    drag_mode_changed = Signal(bool)

    def __init__(self, settings, labels=None, positions=None, parent=None):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                 | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(parent, flags)
        self.setWindowTitle("Oracle Assistant Overlay")
        self.setObjectName("oracleOverlay")
        self.setStyleSheet("QWidget#oracleOverlay { background-color: transparent; }")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.labels = dict(labels or DEFAULT_ORACLE_LABELS)
        self.positions = copy_positions(positions)
        self.presentation = None
        self.settings = replace(settings)
        self.drag_mode = False
        self._drag_offset = None
        self._applying = False
        self.setAccessibleName("Oracle順序Overlay")
        app = QApplication.instance()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(self.restore_geometry)
        for screen in app.screens():
            screen.availableGeometryChanged.connect(self.restore_geometry)
        self.apply_settings(settings)

    def _screens(self):
        return tuple(Bounds(rect.x(), rect.y(), rect.width(), rect.height())
                     for screen in QApplication.screens() for rect in (screen.availableGeometry(),))

    def _screen_added(self, screen):
        screen.availableGeometryChanged.connect(self.restore_geometry)
        self.restore_geometry()

    def apply_settings(self, settings):
        config = AppConfig()
        config.overlay = replace(settings)
        config.validate()
        self.settings = replace(settings)
        if not settings.enabled and self.drag_mode:
            self.drag_mode = False
            self.drag_mode_changed.emit(False)
        self._applying = True
        transparent = settings.click_through and not self.drag_mode
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, transparent)
        width = max(320 if settings.mode == "map" else 240, round(settings.width * settings.scale))
        height = max(320 if settings.mode == "map" else 112,
                     round(max(settings.height, 330 if settings.mode == "map" else 0) * settings.scale))
        geometry = fit_to_screens(Bounds(settings.x, settings.y, width, height), self._screens())
        self.setFixedSize(geometry.width, geometry.height)
        self.move(geometry.x, geometry.y)
        self.setWindowOpacity(settings.opacity)
        self.settings.x, self.settings.y = geometry.x, geometry.y
        self._applying = False
        if settings.enabled:
            self.show()
            self.raise_()
        else:
            self.hide()
        self.update()

    def restore_geometry(self, *unused):
        before = self.pos()
        self.apply_settings(self.settings)
        if self.pos() != before:
            self.position_changed.emit(self.x(), self.y())

    def set_drag_mode(self, enabled):
        enabled = bool(enabled and self.settings.enabled)
        if enabled == self.drag_mode:
            return
        self.drag_mode = enabled
        self._drag_offset = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.apply_settings(self.settings)
        self.drag_mode_changed.emit(enabled)

    def set_result(self, presentation):
        self.presentation = presentation
        self.setAccessibleDescription(presentation.state_text + " / " + presentation.sequence_text)
        self.update()

    def set_labels(self, labels):
        self.labels = dict(labels)
        self.update()

    def set_positions(self, positions):
        self.positions = copy_positions(positions)
        self.update()

    def _text(self, painter, area, text, point_size, color, bold=False):
        # Fit long sequences at small scale instead of clipping the seventh Oracle.
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        low, high, selected = 8, max(8, min(96, round(point_size))), 8
        while low <= high:
            value = (low + high) // 2
            font = QFont("Yu Gothic UI", value)
            font.setBold(bold)
            painter.setFont(font)
            bounds = painter.boundingRect(area, flags, text)
            if bounds.width() <= area.width() and bounds.height() <= area.height():
                selected, low = value, value + 1
            else:
                high = value - 1
        font = QFont("Yu Gothic UI", selected)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color))
        bounds = painter.boundingRect(area, flags, text)
        if bounds.width() > area.width() or bounds.height() > area.height():
            text = QFontMetrics(font).elidedText(text.replace("\n", " "), Qt.TextElideMode.ElideRight, round(area.width()))
        painter.drawText(area, flags, text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        view = self.presentation
        color = view.color if view else "#a8c8ff"
        painter.setPen(QPen(QColor("#eac16f" if self.drag_mode else color), 2))
        painter.setBrush(QColor(12, 20, 33, 224))
        painter.drawRoundedRect(area, 12, 12)
        header = "位置調整中 · クリック透過OFF" if self.drag_mode else (
            f"Round {view.round_index} · {view.state_text}" if view else "STOPPED")
        self._text(painter, QRectF(10, 5, self.width() - 20, 25), header, 12 * self.settings.scale, color, True)
        if self.settings.mode == "map":
            map_area = QRectF(8, 35, self.width() - 16, self.height() - 77)
            scale = max(.7, min(self.settings.scale * self.settings.font_size / 28,
                                map_area.width() / 600, map_area.height() / 200))
            draw_map(painter, map_area, self.positions, self.labels, view.sequence if view else (),
                     view.status if view else "IDLE", scale=scale)
        else:
            text = view.sequence_text if view else "LiveでStartを押してください。"
            self._text(painter, QRectF(12, 33, self.width() - 24, self.height() - 65),
                       text, self.settings.font_size * self.settings.scale, color, True)
        footer = view.confidence_text if view else "Confidence —"
        if view and self.width() >= 540:
            footer += "  ·  " + view.count_text
        self._text(painter, QRectF(10, self.height() - 30, self.width() - 20, 24),
                   footer, 10 * self.settings.scale, "#d2dfef")

    def mousePressEvent(self, event):
        if self.drag_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_mode and self._drag_offset is not None:
            position = event.globalPosition().toPoint() - self._drag_offset
            self.move(position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            geometry = fit_to_screens(Bounds(self.x(), self.y(), self.width(), self.height()), self._screens())
            self.move(geometry.x, geometry.y)
            self.settings.x, self.settings.y = self.x(), self.y()
            self.position_changed.emit(self.x(), self.y())
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        self.settings.enabled = False
        self.set_drag_mode(False)
        self.hide()
        self.enabled_changed.emit(False)
        event.accept()
