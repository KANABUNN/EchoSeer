"""Portable validation, default-off compatibility, conflicts and ownership cleanup."""
from dataclasses import replace
import pytest

from config.hotkeys import (
    ACTIONS, BASE_ID, MOD_NOREPEAT, HotkeySpec, HotkeyRegistry,
    HotkeyRegistrationError, parse_hotkey,
)
from config.schema import AppConfig, HotkeySettings, ConfigValidationError
from tests.fakes_hotkeys import FakeHotkeyBackend


@pytest.mark.parametrize("text,expected", [
    ("", None), ("  ", None), ("Ctrl+Alt+F7", HotkeySpec(3, 0x76)),
    ("alt+ctrl+f7", HotkeySpec(3, 0x76)), ("Shift+Z", HotkeySpec(4, 90)),
    ("Ctrl+0", HotkeySpec(2, 48)), ("Alt+F24", HotkeySpec(1, 0x87)),
])
def test_supported_single_chords(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize("text", [
    "F7", "A", "Win+F7", "Ctrl+F12", "Ctrl+F25", "Ctrl+F0",
    "Ctrl+Ctrl+A", "Ctrl+", "Ctrl+A, Ctrl+B", "Ctrl+Delete", "Meta+A", "A" * 65,
])
def test_reserved_unmodified_and_multiple_chords_are_rejected(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_old_config_gets_safe_defaults_and_invalid_new_fields_are_rejected():
    payload = AppConfig().to_dict()
    payload.pop("hotkeys")
    payload["audio"].pop("auto_reconnect")
    payload["audio"].pop("reconnect_interval")
    payload["sequence"].pop("auto_advance")
    old = AppConfig.from_dict(payload)
    assert not old.hotkeys.enabled
    assert old.audio.auto_reconnect and old.sequence.auto_advance
    assert old.to_dict() == AppConfig().to_dict()
    for group, key, value in (
        ("hotkeys", "enabled", 1), ("audio", "auto_reconnect", "yes"),
        ("sequence", "auto_advance", 1), ("audio", "reconnect_interval", float("nan")),
        ("audio", "reconnect_interval", 0),
    ):
        invalid = old.to_dict()
        invalid[group][key] = value
        with pytest.raises(ConfigValidationError):
            AppConfig.from_dict(invalid)


@pytest.mark.parametrize("settings", [
    HotkeySettings(enabled=True, toggle_capture="", reset="", next_round="", toggle_overlay=""),
    HotkeySettings(reset="alt+ctrl+f7"),
    HotkeySettings(toggle_overlay="Ctrl+F12"),
])
def test_binding_conflicts_are_validated_even_before_registration(settings):
    cfg = AppConfig(hotkeys=settings)
    with pytest.raises(ConfigValidationError):
        cfg.validate()


def test_disabled_creates_no_registrations_then_change_releases_old_chords():
    backend = FakeHotkeyBackend()
    registry = HotkeyRegistry(backend)
    registry.configure(HotkeySettings())
    assert not backend.calls and not registry.active
    registry.configure(HotkeySettings(enabled=True))
    assert len(backend.entries) == 4
    assert all(call[2] & MOD_NOREPEAT for call in backend.calls if call[0] == "register")
    registry.configure(HotkeySettings(enabled=True, reset="Ctrl+Alt+R", next_round=""))
    assert len(backend.entries) == 3
    assert registry.action(BASE_ID + 1, 3, 0x77) is None  # Queued old Reset.
    assert registry.action(BASE_ID + 1, 3, ord("R")) == "reset"
    assert registry.action(BASE_ID + 2, 3, 0x78) is None  # Removed binding.
    registry.clear()
    registry.clear()
    assert not backend.entries


def test_partial_conflict_restores_previous_bindings_without_leaks():
    backend = FakeHotkeyBackend()
    registry = HotkeyRegistry(backend)
    initial = HotkeySettings(enabled=True)
    registry.configure(initial)
    previous = dict(backend.entries)
    backend.reserved.add((3, ord("R")))
    with pytest.raises(HotkeyRegistrationError, match="Reset"):
        registry.configure(replace(initial, toggle_capture="Ctrl+Alt+S", reset="Ctrl+Alt+R"))
    assert backend.entries == previous
    assert len(registry.active) == 4
    registry.clear()
    assert not backend.entries


def test_failed_restore_is_explicit_and_leaves_no_partial_new_bindings():
    backend = FakeHotkeyBackend()
    registry = HotkeyRegistry(backend)
    initial = HotkeySettings(enabled=True)
    registry.configure(initial)
    backend.reserved.update({(3, ord("R")), (3, 0x76)})
    with pytest.raises(HotkeyRegistrationError, match="復元"):
        registry.configure(replace(initial, toggle_capture="Ctrl+Alt+S", reset="Ctrl+Alt+R"))
    assert not backend.entries and not registry.active
