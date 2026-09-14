"""Real threaded lifecycle and reconnect behavior without physical devices."""

from queue import Empty, Queue
import time

import pytest

from audio.device_manager import DeviceCatalog, DeviceManager
from audio.level import AudioLevel
from audio.service import CaptureService, CaptureStatus
from tests.fakes_audio import AudioFactory, FakeModule


def wait_event(events: Queue, predicate, timeout: float = 3):
    deadline = time.monotonic() + timeout
    history = []
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=max(0.001, deadline - time.monotonic()))
        except Empty:
            break
        history.append(event)
        if predicate(event):
            return event
    pytest.fail(f"Expected audio event did not arrive: {history}")


def make_service(factory: AudioFactory):
    events = Queue()
    service = CaptureService(events.put, manager=DeviceManager(FakeModule, factory), retry_seconds=0.02)
    service.start_worker()
    catalog = wait_event(events, lambda event: isinstance(event, DeviceCatalog))
    wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "STOPPED")
    return service, events, catalog


def test_repeated_start_stop_releases_all_native_and_callback_resources() -> None:
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    device = catalog.default_for("wasapi_loopback")
    try:
        assert not factory.streams  # Startup enumerates but never captures.
        for _ in range(12):
            service.start(device)
            wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "LIVE")
            level = wait_event(events, lambda event: isinstance(event, AudioLevel) and event.rms > 0.1)
            assert level.rms == pytest.approx(0.2)
            assert service.buffer.snapshot().shape[1] == 2
            service.stop()
            wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "STOPPED")
            assert factory.streams[-1].closed
    finally:
        assert service.shutdown()
    assert all(interface.terminated for interface in factory.interfaces)
    assert all(stream.thread is None or not stream.thread.is_alive() for stream in factory.streams)
    assert {interface.created_thread for interface in factory.interfaces} == {"OracleAudioWorker"}


def test_open_failure_is_visible_and_can_be_retried() -> None:
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    try:
        factory.fail_open = True
        service.start(catalog.default_for("wasapi_loopback"))
        error = wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "ERROR")
        assert "再検索" in error.message
        assert "Device unavailable" not in error.message
        assert "Device unavailable" in error.detail
        factory.fail_open = False
        service.start(catalog.default_for("input_device"))
        live = wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "LIVE")
        assert live.device.sample_rate == 44100 and not live.device.loopback
    finally:
        assert service.shutdown()


def test_inactive_stream_reconnects_to_identity_after_index_and_rate_change() -> None:
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    try:
        service.start(catalog.default_for("wasapi_loopback"))
        wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "LIVE")
        factory.index, factory.rate = 7, 44100
        factory.streams[-1].lose()
        wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "DEVICE_LOST")
        live = wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "LIVE")
        assert live.device.index == 7 and live.device.sample_rate == 44100
        assert service.buffer.sample_rate == 44100
        assert factory.streams[0].closed
    finally:
        assert service.shutdown()


def test_healthy_silent_loopback_does_not_get_reported_as_device_loss() -> None:
    factory = AudioFactory()
    factory.no_data = True
    service, events, catalog = make_service(factory)
    try:
        service.start(catalog.default_for("wasapi_loopback"))
        wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "LIVE")
        wait_event(events, lambda event: isinstance(event, AudioLevel) and event.rms == 0)
        time.sleep(0.4)
        history = []
        while not events.empty():
            history.append(events.get_nowait())
        assert not any(isinstance(event, CaptureStatus) and event.state == "DEVICE_LOST" for event in history)
    finally:
        assert service.shutdown()


def test_start_after_shutdown_is_rejected() -> None:
    factory = AudioFactory()
    service, events, catalog = make_service(factory)
    assert service.shutdown()
    with pytest.raises(RuntimeError):
        service.start(catalog.default_for("wasapi_loopback"))


def test_unexpected_worker_failure_remains_visible_after_cleanup() -> None:
    class BrokenFactory(AudioFactory):
        def __call__(self):
            interface = super().__call__()
            def fail_enumeration():
                raise KeyError("Broken native catalog")
            interface.get_device_count = fail_enumeration
            return interface
    factory = BrokenFactory()
    events = Queue()
    service = CaptureService(events.put, manager=DeviceManager(FakeModule, factory))
    service.start_worker()
    closed = wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "CLOSED")
    assert "再起動" in closed.message
    assert not service.wants_capture
    with pytest.raises(RuntimeError):
        service.refresh()
    assert service.shutdown()
    assert all(interface.terminated for interface in factory.interfaces)


def test_native_invalid_device_has_actionable_error_and_can_retry() -> None:
    class UnavailableFactory(AudioFactory):
        def __call__(self):
            interface = super().__call__()
            original_open = interface.open
            def open_or_fail(**options):
                if self.fail_open:
                    raise OSError(-9996, "Invalid device")
                return original_open(**options)
            interface.open = open_or_fail
            return interface
    factory = UnavailableFactory()
    service, events, catalog = make_service(factory)
    try:
        factory.fail_open = True
        service.start(catalog.default_for("wasapi_loopback"))
        error = wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "ERROR")
        assert "再検索" in error.message and "Invalid device" not in error.message
        factory.fail_open = False
        service.start(catalog.default_for("input_device"))
        wait_event(events, lambda event: isinstance(event, CaptureStatus) and event.state == "LIVE")
    finally:
        assert service.shutdown()
