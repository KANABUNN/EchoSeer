"""Config weights reach the worker, Debug components and reset remain coherent."""
import pytest
from audio.waveio import write_wav
from config.schema import AppConfig
from templates.manager import TemplateManager
from tests.fakes_audio import AudioFactory
from tests.integration.test_live_ui import qt_app, make_window, cleanup, pump_until
from tests.unit.test_classifier import oracle_wave


@pytest.mark.gui
def test_config_weights_and_debug_components_are_visible_and_reset(qt_app, tmp_path):
    root = tmp_path/"user-data"
    manager = TemplateManager(root/"templates")
    manager.add("MID", oracle_wave(2))
    manager.add("R1", oracle_wave(3))
    settings = AppConfig()
    settings.recognition.waveform_weight = .2
    settings.recognition.spectrum_weight = .8
    factory = AudioFactory()
    window = make_window(tmp_path, factory, settings)
    try:
        page = window.replay_page
        table = page.recognition.table
        assert table.isColumnHidden(4) and table.isColumnHidden(5)
        page.recognition.details_checkbox.setChecked(True)
        assert not table.isColumnHidden(4) and not table.isColumnHidden(5)
        page.set_wave_path(write_wav(tmp_path/"query.wav", oracle_wave(2)))
        page.analyze_button.click()
        pump_until(lambda:not window.operations.busy and page.recognition.result is not None)
        result = page.recognition.result
        assert result.oracle.value == "MID"
        assert result.waveform_weight == .2 and result.spectrum_weight == .8
        row = result.ranking[0]
        assert table.item(0,2).text() == f"{row.combined_score:.6f}"
        assert table.item(0,4).text() == f"{row.waveform_score:.6f}"
        assert table.item(0,5).text() == f"{row.spectrum_score:.6f}"
        assert "0.2000" in page.recognition.weights_label.text() and "0.8000" in page.recognition.weights_label.text()
        page.set_wave_path(tmp_path/"missing.wav")
        assert page.recognition.result is None and table.item(0,4) is None
        assert page.recognition.details_checkbox.isChecked()
        page.analyze_button.click()
        pump_until(lambda:not window.operations.busy and bool(page.message_label.text()))
        assert page.recognition.result is None
    finally:
        cleanup(window, factory)
