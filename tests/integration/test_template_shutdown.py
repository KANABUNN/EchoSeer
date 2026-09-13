"""Closing the window during recording/playback discards partial work and frees threads."""
import pytest
from PySide6.QtWidgets import QFileDialog
from tests.fakes_audio import AudioFactory
from tests.integration.test_live_ui import qt_app, pump_until, cleanup
from tests.integration.test_calibration_ui import window_at
from tests.unit.test_templates import tone
from templates.manager import TemplateManager


@pytest.mark.gui
@pytest.mark.parametrize("activity", ["record", "play"])
def test_close_during_record_or_play_keeps_saved_sample_and_frees_workers(qt_app, tmp_path, activity):
    root = tmp_path / "data"
    manager = TemplateManager(root / "templates")
    saved = manager.add("L1", tone(5))
    factory = AudioFactory()
    factory.tone = True
    window = window_at(root, factory)
    page = window.calibration_page
    try:
        if activity == "record":
            window.live_page.start_button.click()
            pump_until(lambda: page.record_button.isEnabled())
            page.duration_spin.setValue(5)
            page.record_button.click()
            pump_until(lambda: window.controller.service._recorder is not None)
        else:
            page.play_button.click()
            pump_until(lambda: bool(factory.streams))
        window.close()
        pump_until(lambda: not window.isVisible() and not window.templates.is_alive and not window.controller.service.is_alive)
        assert saved.path.exists() and len(manager.catalog().samples) == 1
        assert all(s.closed and not s.thread.is_alive() for s in factory.streams)
        assert all(i.terminated for i in factory.interfaces)
    finally:
        cleanup(window, factory)
