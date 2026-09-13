"""Real WAV encodings, malformed containers and atomic destination protection."""

from io import BytesIO
from pathlib import Path
import struct
from threading import Event
import wave

import numpy as np
import pytest
from scipy.io import wavfile

from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled
from audio import waveio
from audio.waveio import WaveAudioError, read_wav, write_wav


def pcm_bytes(values, bits: int, rate: int, channels: int) -> bytes:
    memory = BytesIO()
    with wave.open(memory, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(bits // 8)
        stream.setframerate(rate)
        if bits == 24:
            raw = b"".join((int(value) & 0xFFFFFF).to_bytes(3, "little") for value in values.flat)
        else:
            dtype = {8: "u1", 16: "<i2", 32: "<i4"}[bits]
            raw = values.astype(dtype).tobytes()
        stream.writeframes(raw)
    return memory.getvalue()


@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("encoding", ["pcm8", "pcm16", "pcm24", "pcm32", "float32", "float64"])
def test_real_encodings_decode_to_scaled_native_frames(tmp_path: Path, rate, channels, encoding) -> None:
    values = np.tile(np.array([-0.5, 0, 0.5, 0.25])[:, None], (1, channels))
    path = tmp_path / "日本語 oracle.wav"
    if encoding.startswith("pcm"):
        bits = int(encoding[3:])
        integers = values * (1 << (bits - 1)) + (128 if bits == 8 else 0)
        path.write_bytes(pcm_bytes(integers, bits, rate, channels))
    else:
        wavfile.write(path, rate, values.astype(encoding))
    clip = read_wav(path)
    assert clip.sample_rate == rate and clip.channels == channels
    assert clip.samples.dtype == np.float32 and not clip.samples.flags.writeable
    np.testing.assert_array_equal(clip.samples, values.astype(np.float32))


def test_extensible_pcm_and_metadata_chunks_are_supported(tmp_path: Path) -> None:
    payload = np.array([[0], [16384], [-16384]], dtype="<i2").tobytes()
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, 48000, 96000, 2, 16, 22, 16, 4)
    fmt += bytes.fromhex("0100000000001000800000aa00389b71")
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"JUNK" + struct.pack("<I", 3) + b"abc\0"
    body += b"data" + struct.pack("<I", len(payload)) + payload
    path = tmp_path / "extended.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    np.testing.assert_array_equal(read_wav(path).samples[:, 0], [0, 0.5, -0.5])


def test_odd_final_pcm8_chunk_without_pad_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "odd.wav"
    path.write_bytes(pcm_bytes(np.array([[128], [192], [64]]), 8, 48000, 1))
    np.testing.assert_array_equal(read_wav(path).samples[:, 0], [0, 0.5, -0.5])


@pytest.mark.parametrize("corruption", ["header", "truncate", "data_length", "alignment", "compressed", "empty"])
def test_corrupt_or_unsupported_wav_is_rejected(tmp_path: Path, corruption) -> None:
    data = bytearray(pcm_bytes(np.array([[0], [16384]]), 16, 48000, 1))
    if corruption == "header":
        data[:4] = b"NOPE"
    elif corruption == "truncate":
        data = data[:-1]
    elif corruption == "data_length":
        struct.pack_into("<I", data, 40, 1000)
    elif corruption == "alignment":
        struct.pack_into("<H", data, 32, 1)
    elif corruption == "compressed":
        struct.pack_into("<H", data, 20, 6)
    elif corruption == "empty":
        data = bytearray(pcm_bytes(np.empty((0, 1)), 16, 48000, 1))
    path = tmp_path / "broken.wav"
    path.write_bytes(data)
    with pytest.raises(WaveAudioError):
        read_wav(path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1e300])
def test_invalid_float_samples_are_rejected(tmp_path: Path, value) -> None:
    path = tmp_path / "bad-float.wav"
    wavfile.write(path, 48000, np.array([0, value], dtype=np.float64))
    with pytest.raises(AudioDataError):
        read_wav(path)


def test_input_size_and_decoded_size_limits_are_enforced(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "small.wav"
    path.write_bytes(pcm_bytes(np.zeros((20, 2)), 16, 48000, 2))
    monkeypatch.setattr(waveio, "MAX_FILE_BYTES", 10)
    with pytest.raises(WaveAudioError, match="128 MiB"):
        read_wav(path)
    monkeypatch.setattr(waveio, "MAX_FILE_BYTES", 10000)
    monkeypatch.setattr(waveio, "MAX_DECODED_BYTES", 10)
    with pytest.raises(WaveAudioError, match="256 MiB"):
        read_wav(path)


def test_float32_export_preserves_native_samples_exactly(tmp_path: Path) -> None:
    samples = np.random.default_rng(20).normal(size=(1027, 8)).astype(np.float32)
    clip = AudioClip(samples, 192000)
    path = write_wav(tmp_path / "native.wav", clip)
    reopened = read_wav(path)
    assert reopened.sample_rate == clip.sample_rate and reopened.channels == 8
    np.testing.assert_array_equal(reopened.samples, clip.samples)


def test_pcm16_export_quantizes_and_clips_intentionally(tmp_path: Path) -> None:
    clip = AudioClip(np.array([-1.25, -0.5, 0, 0.5, 1.25]), 48000)
    path = write_wav(tmp_path / "pcm.wav", clip, "pcm16")
    rate, values = wavfile.read(path)
    assert rate == 48000 and values.dtype == np.int16
    np.testing.assert_array_equal(values[:, 0] if values.ndim == 2 else values, [-32768, -16384, 0, 16384, 32767])


@pytest.mark.parametrize("failure", ["replace", "partial_write", "cancel"])
def test_failed_save_preserves_existing_destination_and_removes_temp(tmp_path: Path, monkeypatch, failure) -> None:
    path = tmp_path / "keep.wav"
    original = b"existing data"
    path.write_bytes(original)
    cancel = Event()
    real_write = wavfile.write
    if failure == "replace":
        def fail_replace(*args):
            raise PermissionError("blocked replace")
        monkeypatch.setattr(waveio.os, "replace", fail_replace)
    else:
        def fail_write(stream, rate, values):
            if failure == "partial_write":
                stream.write(b"partial")
                raise OSError("disk full")
            real_write(stream, rate, values)
            cancel.set()
        monkeypatch.setattr(wavfile, "write", fail_write)
    with pytest.raises((OSError, OperationCancelled)):
        write_wav(path, AudioClip(np.zeros((4, 2), dtype=np.float32), 48000), cancel=cancel)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_precancelled_read_and_empty_export_do_not_touch_destination(tmp_path: Path) -> None:
    cancel = Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        read_wav(tmp_path / "not-present.wav", cancel)
    path = tmp_path / "keep.wav"
    path.write_bytes(b"keep")
    with pytest.raises(WaveAudioError):
        write_wav(path, AudioClip(np.empty((0, 2), dtype=np.float32), 48000))
    assert path.read_bytes() == b"keep"
