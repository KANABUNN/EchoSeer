"""Windows RegisterHotKey bridge; no hooks, input synthesis, or worker threads."""
import ctypes
from ctypes import wintypes
from dataclasses import replace
import logging
import sys

from PySide6.QtCore import QObject, QAbstractNativeEventFilter, Signal
from PySide6.QtWidgets import QApplication, QKeySequenceEdit

from config.hotkeys import HotkeyRegistry, HotkeyRegistrationError

logger = logging.getLogger("oracle_assistant.hotkeys")


class WindowsHotkeyBackend:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self.user32.UnregisterHotKey.restype = wintypes.BOOL

    def register(self, identifier, modifiers, key):
        ok = bool(self.user32.RegisterHotKey(None, identifier, modifiers, key))
        if not ok:
            logger.warning("RegisterHotKey id=%s failed: winerror=%s", identifier, ctypes.get_last_error())
        return ok

    def unregister(self, identifier):
        ok = bool(self.user32.UnregisterHotKey(None, identifier))
        if not ok:
            logger.error("UnregisterHotKey id=%s failed: winerror=%s", identifier, ctypes.get_last_error())
        return ok


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312:
                action = self.owner.registry.action(
                    int(msg.wParam), int(msg.lParam) & 0xFFFF, (int(msg.lParam) >> 16) & 0xFFFF,
                )
                if action and not self.owner.closed:
                    self.owner.activated.emit(action)
                    return True, 0
        return False, 0


class HotkeyController(QObject):
    activated = Signal(str)
    status_changed = Signal(str)

    def __init__(self, settings, parent=None, backend=None):
        super().__init__(parent)
        self.closed = False
        self.suspended = False
        self._settings = replace(settings)
        self.supported = backend is not None or (
            sys.platform == "win32" and QApplication.platformName() == "windows"
        )
        self.registry = HotkeyRegistry(backend or WindowsHotkeyBackend()) if self.supported else None
        self._filter = _Filter(self) if self.supported else None
        self.status = ""
        app = QApplication.instance()
        if self._filter:
            app.installNativeEventFilter(self._filter)
        app.focusChanged.connect(self._focus_changed)
        try:
            self.configure(settings)
        except HotkeyRegistrationError as error:
            self._status(str(error))

    def _status(self, text):
        self.status = text
        self.status_changed.emit(text)

    def configure(self, settings):
        if self.closed:
            raise HotkeyRegistrationError("Hotkeyは終了処理中です。")
        if settings.enabled and not self.supported:
            raise HotkeyRegistrationError("この環境ではGlobal Hotkeyを使えません。Windows上で起動してください。")
        if self.registry:
            self.registry.configure(settings)
            if self.suspended:
                self.registry.clear()
        self._settings = replace(settings)
        self._status("Hotkey編集中 · 一時解除" if settings.enabled and self.suspended else
                     "Global Hotkey有効" if settings.enabled else "Hotkey無効")

    def _focus_changed(self, old, new):
        current = new
        editing = False
        while current is not None:
            if isinstance(current, QKeySequenceEdit):
                editing = True
                break
            current = current.parentWidget()
        if self.closed or editing == self.suspended:
            return
        self.suspended = editing
        try:
            if editing:
                if self.registry:
                    self.registry.clear()
                self._status("Hotkey編集中 · 一時解除" if self._settings.enabled else "Hotkey無効")
            else:
                self.configure(self._settings)
        except HotkeyRegistrationError as error:
            logger.exception("Hotkey resume failed")
            self._status(str(error))

    def close(self):
        if self.closed:
            return
        self.closed = True
        app = QApplication.instance()
        app.focusChanged.disconnect(self._focus_changed)
        if self.registry:
            try:
                self.registry.clear()
            except HotkeyRegistrationError:
                logger.exception("Hotkey cleanup failed")
        if self._filter:
            app.removeNativeEventFilter(self._filter)
