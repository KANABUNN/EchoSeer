"""Validated PCM/IEEE-float WAV input and atomic float32/PCM16 output."""

from dataclasses import dataclass
import io
import os
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile
from threading import Event
import warnings

import numpy as np
from scipy.io import wavfile

from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_DECODED_BYTES = 256 * 1024 * 1024


class WaveAudioError(AudioDataError):
    """Unsupported or malformed WAV audio."""


@dataclass(frozen=True, slots=True)
class WaveFormat:
    sample_rate: int
    channels: int
    bits: int
    encoding: int
    frames: int


def _validate_header(data: bytes) -> WaveFormat:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WaveAudioError("RIFF / WAVE 形式の WAV ファイルを選択してください。")
    end = struct.unpack_from("<I", data, 4)[0] + 8
    if end != len(data):
        raise WaveAudioError("WAV の長さ情報が不正か、ファイルが途中で切れています。")
    cursor = 12
    fmt = None
    data_size = None
    while cursor < end:
        if cursor + 8 > end:
            raise WaveAudioError("WAV のチャンク情報が途中で切れています。")
        tag = data[cursor:cursor + 4]
        size = struct.unpack_from("<I", data, cursor + 4)[0]
        start, stop = cursor + 8, cursor + 8 + size
        if stop > end:
            raise WaveAudioError("WAV の音声データが途中で切れています。")
        if tag == b"fmt ":
            if fmt is not None or size < 16:
                raise WaveAudioError("WAV の音声形式情報が不正です。")
            encoding, channels, rate, byte_rate, alignment, bits = struct.unpack_from("<HHIIHH", data, start)
            if encoding == 0xFFFE:
                if size < 40 or struct.unpack_from("<H", data, start + 16)[0] < 22:
                    raise WaveAudioError("WAV の拡張形式情報が不正です。")
                subtype = data[start + 24:start + 40]
                if subtype[4:] != bytes.fromhex("00001000800000aa00389b71"):
                    raise WaveAudioError("この WAV の圧縮形式には対応していません。")
                encoding = struct.unpack_from("<I", subtype)[0]
                valid_bits = struct.unpack_from("<H", data, start + 18)[0]
                if not 1 <= valid_bits <= bits:
                    raise WaveAudioError("WAV の有効ビット数が不正です。")
            supported = (encoding == 1 and bits in (8, 16, 24, 32)) or (
                encoding == 3 and bits in (32, 64)
            )
            if not supported:
                raise WaveAudioError("PCM 8 / 16 / 24 / 32 bit または float 32 / 64 bit の WAV を選択してください。")
            if not 8000 <= rate <= 384000 or not 1 <= channels <= 64:
                raise WaveAudioError("この WAV のサンプルレートまたはチャンネル数には対応していません。")
            if alignment != channels * (bits // 8) or byte_rate != rate * alignment:
                raise WaveAudioError("WAV のフレーム形式が不正です。")
            fmt = (rate, channels, bits, encoding, alignment)
        elif tag == b"data":
            if data_size is not None:
                raise WaveAudioError("音声チャンクが複数ある WAV には対応していません。")
            data_size = size
        # Some valid writers omit the final pad byte of an odd-sized data chunk.
        cursor = stop if stop == end else stop + (size & 1)
    if fmt is None or data_size is None:
        raise WaveAudioError("WAV の音声形式または音声チャンクがありません。")
    rate, channels, bits, encoding, alignment = fmt
    if data_size == 0 or data_size % alignment:
        raise WaveAudioError("WAV の音声が空か、フレームの途中で切れています。")
    frames = data_size // alignment
    if frames * channels * 4 > MAX_DECODED_BYTES:
        raise WaveAudioError("この WAV は展開後の音声が 256 MiB を超えます。短い区間に分割してください。")
    return WaveFormat(rate, channels, bits, encoding, frames)


def read_wav(path: Path | str, cancel: Event | None = None) -> AudioClip:
    check_cancel(cancel)
    chunks = []
    total = 0
    with Path(path).open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > MAX_FILE_BYTES:
            raise WaveAudioError("128 MiB 以下の WAV を選択してください。")
        while True:
            check_cancel(cancel)
            part = stream.read(1024 * 1024)
            if not part:
                break
            total += len(part)
            if total > MAX_FILE_BYTES:
                raise WaveAudioError("128 MiB 以下の WAV を選択してください。")
            chunks.append(part)
    data = b"".join(chunks)
    fmt = _validate_header(data)
    check_cancel(cancel)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", wavfile.WavFileWarning)
            rate, raw = wavfile.read(io.BytesIO(data), mmap=False)
    except (ValueError, EOFError, struct.error) as error:
        raise WaveAudioError("WAV の音声を読み込めません。形式とファイルの破損を確認してください。") from error
    if rate != fmt.sample_rate or raw.size != fmt.frames * fmt.channels:
        raise WaveAudioError("WAV の音声データと形式情報が一致しません。")
    check_cancel(cancel)
    if raw.dtype.kind == "u":
        values = (raw.astype(np.float32) - 128) / 128
    elif raw.dtype.kind == "i":
        values = raw.astype(np.float32) / float(1 << (raw.dtype.itemsize * 8 - 1))
    else:
        values = raw
    return AudioClip(values, int(rate))


def write_wav(
    path: Path | str, clip: AudioClip, encoding: str = "float32",
    cancel: Event | None = None,
) -> Path:
    check_cancel(cancel)
    if clip.frame_count == 0:
        raise WaveAudioError("保存する音声がありません。音を取り込んでから再試行してください。")
    if encoding not in ("float32", "pcm16"):
        raise WaveAudioError("保存形式は float32 または PCM 16 bit を指定してください。")
    destination = Path(path)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w+b", dir=destination.parent, prefix=f".{destination.name}.",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            if encoding == "pcm16":
                values = np.clip(
                    np.rint(clip.samples.astype(np.float64) * 32768), -32768, 32767,
                ).astype("<i2")
            else:
                values = clip.samples.astype("<f4", copy=False)
            check_cancel(cancel)
            wavfile.write(stream, clip.sample_rate, values)
            stream.flush()
            os.fsync(stream.fileno())
        check_cancel(cancel)
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
