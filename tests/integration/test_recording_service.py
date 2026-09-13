"""Recording lifecycle uses the existing capture worker and discards partial audio."""
import time

from audio.capture import AudioBlock
from audio.recorder import RecordingEvent
from audio.service import CaptureStatus
from tests.fakes_audio import AudioFactory
from tests.integration.test_audio_service import make_service, wait_event


def recording(events, token, state):
    return wait_event(events, lambda e: isinstance(e, RecordingEvent) and e.token == token and e.state == state)


def start(service, events, catalog):
    service.start(catalog.default_for("wasapi_loopback"))
    wait_event(events, lambda e: isinstance(e, CaptureStatus) and e.state == "LIVE")


def test_record_requires_live_and_fresh_complete_frames():
    factory = AudioFactory()
    factory.tone = True
    service, events, catalog = make_service(factory)
    try:
        service.record(0.1, "stopped")
        recording(events, "stopped", "ERROR")
        start(service, events, catalog)
        for index in range(3):
            token = str(index)
            service.record(0.1, token)
            recording(events, token, "RECORDING")
            complete = recording(events, token, "COMPLETE")
            assert complete.clip.frame_count == 4800 and complete.clip.channels == 2
            assert factory.streams[-1].is_active()
    finally:
        assert service.shutdown()
    assert all(i.terminated for i in factory.interfaces)


def test_cancel_stop_and_loss_never_complete_partial_recording():
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    try:
        start(service, events, catalog)
        service.record(5, "cancel")
        recording(events, "cancel", "RECORDING")
        service.cancel_recording()
        recording(events, "cancel", "CANCELLED")
        service.record(5, "stop")
        recording(events, "stop", "RECORDING")
        service.stop()
        recording(events, "stop", "CANCELLED")
        wait_event(events, lambda e: isinstance(e, CaptureStatus) and e.state == "STOPPED")
        start(service, events, catalog)
        service.record(5, "lost")
        recording(events, "lost", "RECORDING")
        factory.streams[-1].lose()
        recording(events, "lost", "CANCELLED")
    finally:
        assert service.shutdown()
    assert not any(isinstance(e, RecordingEvent) and e.state == "COMPLETE" for e in list(events.queue))


def test_overflow_discards_recording():
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    try:
        start(service, events, catalog)
        service.record(5, "overflow")
        recording(events, "overflow", "RECORDING")
        session = service._session
        frames, channels = 960, 2
        session._callback(bytes(frames * channels * 4), frames, {}, 2)
        event = recording(events, "overflow", "ERROR")
        assert "欠落" in event.message and event.clip is None
    finally:
        assert service.shutdown()


def test_no_callback_times_out_recording_without_stopping_healthy_live():
    factory = AudioFactory()
    factory.no_data = True
    service, events, catalog = make_service(factory)
    try:
        start(service, events, catalog)
        service.record(0.1, "silent")
        recording(events, "silent", "RECORDING")
        event = recording(events, "silent", "ERROR")
        assert "取得できません" in event.message
        assert factory.streams[-1].is_active()
    finally:
        assert service.shutdown()
