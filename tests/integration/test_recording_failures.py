"""Record setup failure reports completion without leaving the page pending."""
import pytest
from audio.recorder import RecordingEvent
from audio.service import CaptureStatus
from tests.fakes_audio import AudioFactory
from tests.integration.test_audio_service import make_service, wait_event

@pytest.mark.parametrize("error", [MemoryError("memory"), ValueError("invalid recording")])
def test_record_setup_failure_emits_error_and_keeps_capture_active(monkeypatch, error):
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    try:
        service.start(catalog.default_for("wasapi_loopback"))
        wait_event(events, lambda e: isinstance(e, CaptureStatus) and e.state == "LIVE")
        def failed(*args):
            raise error
        monkeypatch.setattr("audio.service.SampleRecorder", failed)
        service.record(0.1, "failed")
        result = wait_event(events, lambda e: isinstance(e, RecordingEvent) and e.token == "failed")
        assert result.state == "ERROR" and result.clip is None
        assert factory.streams[-1].is_active()
    finally:
        assert service.shutdown()
