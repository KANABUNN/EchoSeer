"""Fresh-frame recording, short playback output and native resource ownership."""
from threading import Event, Thread
import time

import numpy as np
import pytest

from audio.capture import AudioBlock
from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio.playback import AudioPlayer
from audio.recorder import SampleRecorder
from tests.fakes_audio import AudioFactory, FakeModule
from tests.unit.test_templates import tone


def test_recording_skips_stale_frames_and_crops_start_and_end():
    recorder = SampleRecorder(8000, 2, 0.1, 10.0)
    assert not recorder.append(AudioBlock(np.ones((800, 2), dtype=np.float32), 9.8, 0))
    assert recorder.count == 0
    values = np.repeat(np.arange(800, dtype=np.float32)[:, None], 2, axis=1)
    assert not recorder.append(AudioBlock(values, 9.95, 0))
    assert 399 <= recorder.count <= 400
    with pytest.raises(AudioDataError):
        recorder.clip()
    count = recorder.count
    assert recorder.append(AudioBlock(np.full((800, 2), 42, dtype=np.float32), 10.1, 0))
    clip = recorder.clip()
    assert clip.frame_count == 800 and clip.channels == 2
    np.testing.assert_array_equal(clip.samples[:count], values[-count:])
    assert np.all(clip.samples[count:] == 42) and not clip.samples.flags.writeable


@pytest.mark.parametrize("seconds", [0, -1, 10.1, float("nan"), True])
def test_recording_duration_validation(seconds):
    with pytest.raises(AudioDataError):
        SampleRecorder(48000, 2, seconds, 0)


def test_recording_memory_bound():
    with pytest.raises(AudioDataError, match="サイズ"):
        SampleRecorder(384000, 64, 10, 0)


def test_playback_converts_to_default_output_rate_channels_and_gain():
    factory = AudioFactory()
    player = AudioPlayer(FakeModule, factory)
    clip = tone(0.101, rate=44100, channels=1, dc=0)
    player.play(clip, 0.1)
    stream = factory.streams[0]
    assert stream.options["rate"] == 48000 and stream.options["channels"] == 2
    assert stream.options["output_device_index"] == 0
    frames = np.frombuffer(b"".join(stream.output_bytes), dtype=np.float32).reshape(-1, 2)
    assert np.max(np.abs(frames)) == pytest.approx(0.02, abs=0.0001)
    np.testing.assert_array_equal(frames[:, 0], frames[:, 1])
    assert np.all(frames[-20:] == 0)  # padded final callback
    assert stream.closed and factory.interfaces[0].terminated


def test_playback_stop_and_repeated_play_release_resources():
    factory = AudioFactory()
    player = AudioPlayer(FakeModule, factory)
    stop, errors = Event(), []
    def run():
        try:
            player.play(tone(5), stop=stop)
        except OperationCancelled:
            errors.append("cancelled")
    thread = Thread(target=run)
    thread.start()
    assert factory.open_entered.wait(2)
    stop.set()
    thread.join(2)
    assert not thread.is_alive() and errors == ["cancelled"]
    for _ in range(3):
        player.play(tone(0.1), 0.01)
    assert all(i.terminated for i in factory.interfaces)
    assert all(s.closed and not s.thread.is_alive() for s in factory.streams)


def test_playback_open_failure_and_prior_cancel_release_resources():
    factory = AudioFactory()
    factory.fail_open = True
    with pytest.raises(OSError):
        AudioPlayer(FakeModule, factory).play(tone())
    assert factory.interfaces[0].terminated and not factory.streams
    stop = Event()
    stop.set()
    with pytest.raises(OperationCancelled):
        AudioPlayer(FakeModule, factory).play(tone(), stop=stop)
    assert len(factory.interfaces) == 1


@pytest.mark.parametrize("volume", [-0.1, 1.1, float("nan")])
def test_invalid_playback_volume(volume):
    with pytest.raises(AudioDataError):
        AudioPlayer(FakeModule, AudioFactory()).play(tone(), volume)
