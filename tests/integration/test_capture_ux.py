"""Manual retry, stop intent during open, and worker-owned configuration."""
from queue import Queue
from threading import Event
import time

from audio.device_manager import DeviceCatalog, DeviceManager
from audio.service import CaptureService, CaptureStatus
from tests.fakes_audio import AudioFactory, FakeModule
from tests.integration.test_audio_service import make_service, wait_event


def status(events, state):
    return wait_event(events, lambda e: isinstance(e, CaptureStatus) and e.state == state)


def test_stop_during_open_never_publishes_live_or_starts_callbacks():
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    factory.open_gate = Event()
    try:
        service.start(catalog.default_for("wasapi_loopback"))
        assert factory.open_entered.wait(1)
        service.stop()
        factory.open_gate.set()
        status(events, "STOPPED")
        time.sleep(.05)
        history = []
        while not events.empty():
            history.append(events.get_nowait())
        assert not any(isinstance(e, CaptureStatus) and e.state == "LIVE" for e in history)
        assert all(s.closed and s.thread is None for s in factory.streams)
        assert not service.wants_capture
    finally:
        factory.open_gate.set()
        assert service.shutdown()


def test_manual_reconnect_waits_for_retry_and_stop_cancels_future_retries():
    factory = AudioFactory()
    events = Queue()
    service = CaptureService(events.put, manager=DeviceManager(FakeModule, factory))
    service.configure(12, .25, False)
    service.start_worker()
    catalog = wait_event(events, lambda e: isinstance(e, DeviceCatalog))
    status(events, "STOPPED")
    try:
        service.start(catalog.default_for("wasapi_loopback"))
        status(events, "LIVE")
        assert service.buffer.capacity_frames == round(48000 * 12)
        factory.index, factory.rate = 7, 44100
        factory.streams[-1].lose()
        lost = status(events, "DEVICE_LOST")
        assert "今すぐ再接続" in lost.message
        opened = len(factory.streams)
        time.sleep(.4)
        assert len(factory.streams) == opened
        service.retry()
        live = status(events, "LIVE")
        assert live.device.index == 7 and live.device.sample_rate == 44100
        assert service.buffer.sample_rate == 44100
        factory.streams[-1].lose()
        status(events, "DEVICE_LOST")
        service.stop()
        status(events, "STOPPED")
        opened = len(factory.streams)
        service.retry()
        time.sleep(.3)
        assert len(factory.streams) == opened and not service.wants_capture
    finally:
        assert service.shutdown()


def test_reconnect_failure_is_actionable_without_native_exception_text():
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    try:
        service.start(catalog.default_for("wasapi_loopback"))
        status(events, "LIVE")
        factory.fail_open = True
        factory.streams[-1].lose()
        status(events, "DEVICE_LOST")
        failure = wait_event(events, lambda e: isinstance(e, CaptureStatus) and "再接続できません" in e.message)
        assert "Device unavailable" not in failure.message
        assert "Device unavailable" in failure.detail
        assert "Stop" in failure.message
        service.stop()
        status(events, "STOPPED")
    finally:
        assert service.shutdown()
