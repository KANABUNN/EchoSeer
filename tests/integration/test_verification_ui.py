"""The user sees confirmed, inferred, mismatched or checked results without replacing raw passes."""
import pytest
from audio.waveio import write_wav
from config.schema import AppConfig
from tests.fakes_audio import AudioFactory
from tests.integration.test_live_ui import qt_app,make_window,cleanup,pump_until
from tests.unit.test_sequence_analyzer import two_pass_clip
from tests.verification_helpers import RowsClassifier,rows_for
from encounter.vog_oracles import OracleId


@pytest.mark.gui
@pytest.mark.parametrize("status",["CONFIRMED","INFERRED","MISMATCH","CHECK"])
def test_explicit_status_sequence_and_unmodified_passes(qt_app,tmp_path,status):
    factory=AudioFactory()
    config=AppConfig()
    config.logging.event_logs=config.logging.uncertain_audio=False
    window=make_window(tmp_path,factory,config)
    window.operations.classifier=RowsClassifier(rows_for(status))
    view=window.replay_page.sequence
    try:
        window.replay_page.set_wave_path(write_wav(tmp_path/"round.wav",two_pass_clip()))
        view.analyze_button.click()
        pump_until(lambda:not window.operations.busy and view.result is not None)
        result=view.result
        assert result.verification.status==status
        assert status in view.verification_label.text()
        assert (result.state=="CONFIRMED")== (status=="CONFIRMED")
        if status=="CONFIRMED":
            assert view.final_label.text().startswith("確定順：")
            assert result.final_sequence==(OracleId.L1,OracleId.L2,OracleId.L3)
        else:
            assert not result.confirmed and result.final_sequence is None
            assert not view.final_label.text().startswith("確定順")
        if status=="INFERRED":
            assert "推定順" in view.final_label.text() and "L3" in view.final_label.text()
            assert "unknown" in view.table.item(0,2).text()
            assert "L2 0.830" in view.table.item(0,2).toolTip()
            assert "上位候補" in view.table.item(0,2).toolTip()
        if status=="MISMATCH":
            assert "不一致位置：3" in view.verification_label.text()
            assert view.table.item(0,2).text().startswith("L3")
            assert view.table.item(1,2).text().startswith("R3")
            assert view.table.item(0,2).background().color().name()=="#472936"
        view.next_button.click()
        assert view.result is None and not view.final_label.text() and not view.verification_label.text()
        assert view.round_index==2
        view.reset_button.click()
        assert view.round_index==1 and view.result is None
    finally:cleanup(window,factory)


@pytest.mark.gui
def test_failed_reanalysis_clears_confirmed_sequence_and_status(qt_app,tmp_path):
    factory=AudioFactory()
    window=make_window(tmp_path,factory)
    window.operations.classifier=RowsClassifier(rows_for("CONFIRMED"))
    path=write_wav(tmp_path/"round.wav",two_pass_clip())
    view=window.replay_page.sequence
    try:
        window.replay_page.set_wave_path(path)
        view.analyze_button.click()
        pump_until(lambda:not window.operations.busy and view.result is not None)
        assert view.result.confirmed
        path.write_bytes(b"broken")
        view.analyze_button.click()
        assert view.result is None and not view.final_label.text()
        pump_until(lambda:not window.operations.busy)
        assert view.result is None and not view.verification_label.text()
        assert not window.replay_page.save_button.isEnabled()
    finally:cleanup(window,factory)
