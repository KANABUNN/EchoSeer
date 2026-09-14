"""Qt-free transactional template storage, with reversible per-sample deletion."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shutil
import time
from threading import Event, RLock
from uuid import uuid4

from audio.data import AudioClip, AudioDataError
from audio.operations import check_cancel
from audio.sources import ClipSource
from audio.waveio import read_wav, write_wav
from encounter.vog_oracles import OracleId
from replay.analyzer import Analyzer
from templates.metadata import ID_PATTERN, SampleMetadata
from templates.quality import quality_notices, validate_length

logger = logging.getLogger("oracle_assistant.templates")


def audio_checksum(clip: AudioClip) -> str:
    return sha256(clip.samples.astype("<f4", copy=False).tobytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class TemplateSample:
    metadata: SampleMetadata
    directory: Path

    @property
    def path(self) -> Path:
        return self.directory / "sample.wav"


@dataclass(frozen=True, slots=True)
class TemplateCatalog:
    samples: tuple[TemplateSample, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeletedSample:
    oracle: OracleId
    sample_id: str
    trash_name: str


class TemplateManager:
    def __init__(self, root: Path, sample_rate: int = 48000) -> None:
        self.root = Path(os.path.abspath(root))
        self.analyzer = Analyzer(sample_rate)
        self._lock = RLock()

    def _safe(self, path: Path) -> Path:
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.root):
            raise AudioDataError("サンプルの保存先が不正です。")
        # Refuse linked/junction components, including the storage root.
        for part in (path, *path.parents):
            if part.is_symlink() or part.is_junction():
                raise AudioDataError("保存先のリンクには対応していません。")
            if part == self.root:
                break
        return path

    def _move_directory(self, source: Path, destination: Path, cancel: Event | None = None) -> None:
        # Windows scanners can briefly hold a just-written directory.
        delays = (0.02, 0.04, 0.08, 0.16)
        for attempt in range(len(delays) + 1):
            check_cancel(cancel)
            self._safe(source)
            self._safe(destination)
            if destination.exists():
                raise FileExistsError("同じ保存先がすでに存在します。")
            try:
                source.rename(destination)
                return
            except OSError as error:
                if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == len(delays):
                    raise
                if cancel is None:
                    time.sleep(delays[attempt])
                else:
                    cancel.wait(delays[attempt])

    def _directory(self, oracle: OracleId | str, sample_id: str) -> Path:
        key = OracleId(oracle)
        if not isinstance(sample_id, str) or not ID_PATTERN.fullmatch(sample_id):
            raise AudioDataError("サンプル ID が不正です。")
        return self._safe(self.root / "samples" / key.value / sample_id)

    def _load(self, directory: Path, oracle: OracleId, sample_id: str,
              cancel: Event | None = None) -> TemplateSample:
        check_cancel(cancel)
        meta_path = self._safe(directory / "metadata.json")
        if meta_path.stat().st_size > 32768:
            raise AudioDataError("サンプル情報が大きすぎます。")
        metadata = SampleMetadata.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
        if metadata.oracle != oracle.value or metadata.sample_id != sample_id:
            raise AudioDataError("サンプルの Oracle または ID が一致しません。")
        for filename, rate, channels, frames, checksum in (
            ("sample.wav", metadata.sample_rate, 1, metadata.frames, metadata.checksum),
            ("original.wav", metadata.original_sample_rate, metadata.original_channels,
             metadata.original_frames, metadata.original_checksum),
        ):
            clip = read_wav(self._safe(directory / filename), cancel)
            if (clip.sample_rate, clip.channels, clip.frame_count) != (rate, channels, frames):
                raise AudioDataError("保存音声とサンプル情報の形式が一致しません。")
            if audio_checksum(clip) != checksum:
                raise AudioDataError("保存音声が変更または破損しています。")
        return TemplateSample(metadata, directory)

    def catalog(self, cancel: Event | None = None) -> TemplateCatalog:
        with self._lock:
            samples, issues = [], []
            for oracle in OracleId:
                check_cancel(cancel)
                try:
                    parent = self._safe(self.root / "samples" / oracle.value)
                    if not parent.exists():
                        continue
                    directories = sorted(parent.iterdir())
                except (AudioDataError, OSError) as error:
                    logger.warning("Could not read template folder %s: %s", oracle.value, error)
                    issues.append(f"{oracle.value}：{error}")
                    continue
                for directory in directories:
                    check_cancel(cancel)
                    if not ID_PATTERN.fullmatch(directory.name):
                        continue
                    try:
                        self._safe(directory)
                        if not directory.is_dir():
                            raise AudioDataError("サンプルの保存形式が不正です。")
                        sample = self._load(directory, oracle, directory.name, cancel)
                        samples.append(sample)
                        if sample.metadata.sample_rate != self.analyzer.sample_rate:
                            issues.append(f"{oracle.value} / {directory.name[:8]}：内部レート設定と異なる保存サンプルです。")
                    except (AudioDataError, OSError, ValueError) as error:
                        logger.warning("Skipping damaged template %s: %s", directory, error)
                        issues.append(f"{oracle.value} / {directory.name[:8]}：{error}")
            samples.sort(key=lambda s: (s.metadata.oracle, s.metadata.created_at, s.metadata.sample_id))
            return TemplateCatalog(tuple(samples), tuple(issues))

    def import_wav(self, oracle: OracleId | str, path: Path,
                   cancel: Event | None = None) -> TemplateSample:
        return self.add(oracle, read_wav(path, cancel), "imported", Path(path).name, cancel)

    def add(self, oracle: OracleId | str, clip: AudioClip, source: str = "recorded",
            source_name: str = "録音", cancel: Event | None = None) -> TemplateSample:
        key = OracleId(oracle)
        validate_length(clip)
        result = self.analyzer.analyze(ClipSource(clip), cancel)
        notices = quality_notices(result)
        sample_id = uuid4().hex
        metadata = SampleMetadata.from_dict({
            "schema_version": 1, "sample_id": sample_id, "oracle": key.value,
            "sample_rate": result.processed.sample_rate, "frames": result.processed.frame_count,
            "duration_seconds": result.processed.duration_seconds,
            "created_at": datetime.now(timezone.utc).isoformat(), "source": source,
            "source_name": source_name, "original_sample_rate": clip.sample_rate,
            "original_channels": clip.channels, "original_frames": clip.frame_count,
            "peak": result.original_level.peak, "rms": result.original_level.rms,
            "clipping": result.original_level.peak >= 0.999,
            "checksum": result.checksum, "original_checksum": audio_checksum(clip),
            "notices": notices,
        })
        with self._lock:
            final = self._directory(key, sample_id)
            pending = self._safe(final.parent / (".pending-" + sample_id))
            pending.mkdir(parents=True, exist_ok=False)
            try:
                write_wav(pending / "sample.wav", result.processed, cancel=cancel)
                write_wav(pending / "original.wav", clip, cancel=cancel)
                with (pending / "metadata.json").open("x", encoding="utf-8", newline="\n") as stream:
                    json.dump(metadata.to_dict(), stream, ensure_ascii=False, indent=2, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                check_cancel(cancel)
                self._move_directory(pending, final, cancel)
            finally:
                if pending.exists():
                    shutil.rmtree(self._safe(pending))
            logger.info("Template saved: %s / %s (%s)", key.value, sample_id, source)
            return TemplateSample(metadata, final)

    def load_audio(self, oracle: OracleId | str, sample_id: str,
                   cancel: Event | None = None, *, original: bool = False) -> AudioClip:
        with self._lock:
            sample = self._load(self._directory(oracle, sample_id), OracleId(oracle), sample_id, cancel)
            clip = read_wav(sample.directory / "original.wav" if original else sample.path, cancel)
            metadata = sample.metadata
            expected = ((metadata.original_sample_rate, metadata.original_channels,
                         metadata.original_frames, metadata.original_checksum) if original
                        else (metadata.sample_rate, 1, metadata.frames, metadata.checksum))
            if (clip.sample_rate, clip.channels, clip.frame_count) != expected[:3] or audio_checksum(clip) != expected[3]:
                raise AudioDataError("読み込み中に保存音声が変更されました。再読込してください。")
            return clip

    def delete(self, oracle: OracleId | str, sample_id: str,
               cancel: Event | None = None) -> DeletedSample:
        with self._lock:
            key = OracleId(oracle)
            directory = self._directory(key, sample_id)
            self._load(directory, key, sample_id, cancel)
            trash_name = key.value + "-" + sample_id + "-" + uuid4().hex
            trash = self._safe(self.root / ".trash" / trash_name)
            trash.parent.mkdir(parents=True, exist_ok=True)
            check_cancel(cancel)
            self._move_directory(directory, trash, cancel)
            logger.info("Template moved to trash: %s", trash_name)
            return DeletedSample(key, sample_id, trash_name)

    def restore(self, deleted: DeletedSample, cancel: Event | None = None) -> TemplateSample:
        with self._lock:
            prefix = deleted.oracle.value + "-" + deleted.sample_id + "-"
            if not deleted.trash_name.startswith(prefix) or not ID_PATTERN.fullmatch(deleted.trash_name[len(prefix):]):
                raise AudioDataError("削除履歴が不正です。")
            trash = self._safe(self.root / ".trash" / deleted.trash_name)
            sample = self._load(trash, deleted.oracle, deleted.sample_id, cancel)
            final = self._directory(deleted.oracle, deleted.sample_id)
            if final.exists():
                raise AudioDataError("同じ ID のサンプルが存在するため復元できません。")
            final.parent.mkdir(parents=True, exist_ok=True)
            check_cancel(cancel)
            self._move_directory(trash, final, cancel)
            return TemplateSample(sample.metadata, final)
