"""Device identities, queue bounds, native formats, ring storage and levels."""

from dataclasses import replace

import numpy as np
import pytest

from audio.backend import AmbiguousDeviceError, AudioDevice, DeviceNotFoundError
from audio.capture import CaptureSession
from audio.device_manager import DeviceManager, resolve_device
from audio.level import measure_level
from audio.ring_buffer import RingBuffer
from tests.fakes_audio import AudioFactory, FakeModule


def test_catalog_separates_loopbacks_and_normal_inputs() -> None:
    factory = AudioFactory()
    manager = DeviceManager(FakeModule, factory)
    interface = manager.create_interface()
    try:
        catalog = manager.enumerate(interface)
        assert len(catalog.devices) == 2
        assert catalog.default_for("wasapi_loopback").loopback
        normal = catalog.default_for("input_device")
        assert normal.name == "VoiceMeeter Output" and normal.sample_rate == 44100
        assert all(device.loopback for device in catalog.for_backend("wasapi_loopback"))
        assert all(not device.loopback for device in catalog.for_backend("input_device"))
    finally:
        interface.terminate()


def test_saved_identity_survives_index_and_native_rate_changes() -> None:
    old = AudioDevice(2, "Windows WASAPI", "Speakers", True, 48000, 2)
    current = replace(old, index=15, sample_rate=44100)
    assert resolve_device([current], old.identity()) == current


def test_resolution_rejects_missing_and_ambiguous_devices() -> None:
    device = AudioDevice(2, "Windows WASAPI", "Speakers", True, 48000, 2)
    with pytest.raises(DeviceNotFoundError):
        resolve_device([replace(device, host_api="MME")], device.identity())
    with pytest.raises(AmbiguousDeviceError):
        resolve_device([device, replace(device, index=4)], device.identity())


def test_same_name_uses_optional_format_only_when_needed() -> None:
    device = AudioDevice(2, "Windows WASAPI", "Speakers", True, 48000, 2)
    other = replace(device, index=4, channels=8)
    assert resolve_device([device, other], other.identity()) == other


@pytest.mark.parametrize("channels", [1, 2, 8])
@pytest.mark.parametrize("rate", [44100, 48000, 192000])
def test_native_format_is_used_when_opening_stream(rate: int, channels: int) -> None:
    factory = AudioFactory()
    manager = DeviceManager(FakeModule, factory)
    interface = manager.create_interface()
    device = AudioDevice(2, "Windows WASAPI", "Speakers", True, rate, channels)
    session = CaptureSession(interface, FakeModule, manager.backend(device.backend), device)
    try:
        options = factory.streams[0].options
        assert options["rate"] == rate and options["channels"] == channels
        assert options["input"] is True and options["start"] is False
        assert "output" not in options
    finally:
        session.close()
        interface.terminate()


def test_callback_queue_discards_oldest_and_counts_missing_frames() -> None:
    factory = AudioFactory()
    manager = DeviceManager(FakeModule, factory)
    interface = manager.create_interface()
    device = manager.enumerate(interface).default_for("wasapi_loopback")
    session = CaptureSession(interface, FakeModule, manager.backend(device.backend), device, queue_blocks=2)
    try:
        for index in range(5):
            samples = np.full((2, 2), index, dtype=np.float32)
            assert session._callback(samples.tobytes(), 2, {}, 0) == (None, FakeModule.paContinue)
        assert session.stats().received_frames == 10
        assert session.stats().dropped_frames == 6
        assert np.all(session.pop().samples == 3)
        assert np.all(session.pop().samples == 4)
        assert session.pop(timeout=0) is None
    finally:
        session.close()
        interface.terminate()


def test_malformed_callback_aborts_instead_of_escaping_portaudio() -> None:
    factory = AudioFactory()
    manager = DeviceManager(FakeModule, factory)
    interface = manager.create_interface()
    device = manager.enumerate(interface).default_for("wasapi_loopback")
    session = CaptureSession(interface, FakeModule, manager.backend(device.backend), device)
    try:
        assert session._callback(b"bad", 10, {}, 0)[1] == FakeModule.paAbort
        assert session.stats().error
        assert session.stats().received_frames == 0
    finally:
        session.close()
        interface.terminate()


def test_callback_only_enqueues_without_metering(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = AudioFactory()
    manager = DeviceManager(FakeModule, factory)
    interface = manager.create_interface()
    device = manager.enumerate(interface).default_for("wasapi_loopback")
    session = CaptureSession(interface, FakeModule, manager.backend(device.backend), device)
    def forbid_meter(*args: object, **kwargs: object) -> None:
        raise AssertionError("Callback performed analysis")
    monkeypatch.setattr(np, "mean", forbid_meter)
    try:
        result = session._callback(np.ones((5, 2), dtype=np.float32).tobytes(), 5, {}, FakeModule.paInputOverflow)
        assert result[1] == FakeModule.paContinue
        assert session.stats().overflow_count == 1
    finally:
        session.close()
        interface.terminate()


def test_queue_must_have_a_positive_bound() -> None:
    factory = AudioFactory()
    manager = DeviceManager(FakeModule, factory)
    interface = manager.create_interface()
    device = manager.enumerate(interface).default_for("wasapi_loopback")
    try:
        with pytest.raises(ValueError):
            CaptureSession(interface, FakeModule, manager.backend(device.backend), device, queue_blocks=0)
    finally:
        interface.terminate()


def test_ring_wraparound_matches_chronological_reference() -> None:
    ring = RingBuffer(10, 2, 0.7)
    reference = np.empty((0, 2), dtype=np.float32)
    random = np.random.default_rng(123)
    for count in [2, 4, 1, 9, 3, 8, 0, 6]:
        samples = random.normal(size=(count, 2)).astype(np.float32)
        ring.write(samples)
        reference = np.concatenate((reference, samples))[-7:]
        np.testing.assert_array_equal(ring.snapshot(), reference)
        np.testing.assert_array_equal(ring.snapshot(3), reference[-3:])
    copied = ring.snapshot()
    copied[:] = -999
    np.testing.assert_array_equal(ring.snapshot(), reference)
    assert ring.capacity_frames == 7
    ring.clear()
    assert ring.snapshot().shape == (0, 2)


def test_ring_rejects_wrong_channels_and_negative_reads() -> None:
    ring = RingBuffer(48000, 2, 5)
    with pytest.raises(ValueError):
        ring.write(np.zeros((5, 1), dtype=np.float32))
    with pytest.raises(ValueError):
        ring.snapshot(-1)
    assert ring.size_frames == 0


def test_meter_preserves_opposite_phase_channels_and_rejects_nan() -> None:
    level = measure_level(np.tile(np.array([[0.3, -0.3]], dtype=np.float32), (100, 1)))
    assert level.rms == pytest.approx(0.3) and level.peak == pytest.approx(0.3)
    silent = measure_level(np.empty((0, 2), dtype=np.float32))
    assert silent.rms == 0 and silent.rms_dbfs == -80
    assert measure_level(np.ones((1, 2), dtype=np.float32)).clipping
    with pytest.raises(ValueError):
        measure_level(np.array([[float("nan")]], dtype=np.float32))
