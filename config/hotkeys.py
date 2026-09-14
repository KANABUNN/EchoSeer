"""Qt-free single-chord hotkey validation and transactional registration."""
from dataclasses import dataclass
import re

ACTIONS = ("toggle_capture", "reset", "next_round", "toggle_overlay")
ACTION_LABELS = dict(zip(ACTIONS, ("Start / Stop", "Reset（Round 1）", "Next Round", "Overlay表示")))
MOD_NOREPEAT = 0x4000
BASE_ID = 0x5F00


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    modifiers: int
    virtual_key: int


def parse_hotkey(text: str) -> HotkeySpec | None:
    if not isinstance(text, str) or len(text) > 64:
        raise ValueError("Hotkeyは64文字以内のキー組み合わせで指定してください。")
    if not text.strip():
        return None
    parts = [part.strip().upper() for part in text.split("+")]
    modifiers, seen = 0, set()
    for part in parts[:-1]:
        if part not in ("CTRL", "ALT", "SHIFT") or part in seen:
            raise ValueError("Hotkeyの修飾キーにはCtrl・Alt・Shiftを各1回指定してください。")
        modifiers |= {"CTRL": 2, "ALT": 1, "SHIFT": 4}[part]
        seen.add(part)
    if not modifiers:
        raise ValueError("ゲーム操作との重複を避けるためCtrl・Alt・Shiftのいずれかを付けてください。")
    key = parts[-1]
    if re.fullmatch("[A-Z0-9]", key):
        virtual_key = ord(key)
    elif re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", key) and key != "F12":
        virtual_key = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError("Hotkeyには英字・数字・F1〜F24を指定してください。F12はWindows予約のため使えません。")
    return HotkeySpec(modifiers, virtual_key)


def bindings(settings):
    result, seen = {}, set()
    for action in ACTIONS:
        spec = parse_hotkey(getattr(settings, action))
        if spec is not None:
            if spec in seen:
                raise ValueError("同じHotkeyを複数の操作に割り当てることはできません。")
            seen.add(spec)
            result[action] = spec
    if settings.enabled and not result:
        raise ValueError("Hotkeyを有効にする場合は少なくとも1つ割り当ててください。")
    return result if settings.enabled else {}


class HotkeyRegistrationError(RuntimeError):
    pass


class HotkeyRegistry:
    """Backend must be called on the same owning thread for its entire lifetime."""
    def __init__(self, backend):
        self.backend = backend
        self.active = {}

    def clear(self):
        failed = []
        for identifier in tuple(self.active):
            if not self.backend.unregister(identifier):
                failed.append(identifier)
            else:
                self.active.pop(identifier)
        if failed:
            raise HotkeyRegistrationError("Hotkeyを解除できません。アプリを終了して再起動してください。")

    def _install(self, desired):
        for index, action in enumerate(ACTIONS):
            if action not in desired:
                continue
            identifier, spec = BASE_ID + index, desired[action]
            if not self.backend.register(identifier, spec.modifiers | MOD_NOREPEAT, spec.virtual_key):
                raise HotkeyRegistrationError(
                    f"{ACTION_LABELS[action]}のHotkeyを登録できません。他のアプリとの重複を確認してください。"
                )
            self.active[identifier] = (action, spec)

    def configure(self, settings):
        desired = bindings(settings)
        previous = {action: spec for action, spec in self.active.values()}
        self.clear()
        try:
            self._install(desired)
        except HotkeyRegistrationError as error:
            self.clear()
            try:
                self._install(previous)
            except HotkeyRegistrationError:
                self.clear()
                raise HotkeyRegistrationError(
                    "Hotkeyの変更と元の割り当ての復元に失敗しました。別のキーを設定してください。"
                ) from error
            raise

    def action(self, identifier, modifiers, virtual_key):
        registered = self.active.get(identifier)
        if registered and registered[1] == HotkeySpec(modifiers & 7, virtual_key):
            return registered[0]
        return None
