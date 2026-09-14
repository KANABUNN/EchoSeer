"""Register the supplied seven WAVs using an explicit, reviewed position mapping."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.paths import AppPaths
from audio.data import AudioDataError
from audio.operations import check_cancel
from audio.waveio import read_wav
from config.manager import ConfigManager
from encounter.vog_oracles import OracleId
from templates.manager import TemplateManager, audio_checksum


@dataclass(frozen=True, slots=True)
class ImportSummary:
    added: tuple[str, ...]
    skipped: tuple[str, ...]


def import_samples(source: Path, manager: TemplateManager, mapping: dict,
                   cancel: Event | None = None) -> ImportSummary:
    if not isinstance(mapping, dict) or len(mapping) != 7:
        raise AudioDataError("対応表には 7 種の Oracle を指定してください。")
    parsed = {}
    for filename, oracle in mapping.items():
        if not isinstance(filename, str) or Path(filename).name != filename or "/" in filename or "\\" in filename or Path(filename).suffix.lower() != ".wav":
            raise AudioDataError("対応表のファイル名が不正です。")
        parsed[filename] = OracleId(oracle)
    if set(parsed.values()) != set(OracleId):
        raise AudioDataError("対応表は各 Oracle に 1 ファイルずつ指定してください。")
    # Read all files before publishing any sample; a missing input leaves the store unchanged.
    clips = {}
    for filename in parsed:
        check_cancel(cancel)
        clips[filename] = read_wav(Path(source) / filename, cancel)
    catalog = manager.catalog(cancel)
    existing = {(s.metadata.oracle, s.metadata.original_checksum, s.metadata.original_sample_rate,
                 s.metadata.original_channels, s.metadata.original_frames) for s in catalog.samples}
    added, skipped = [], []
    for filename, oracle in parsed.items():
        check_cancel(cancel)
        clip = clips[filename]
        identity = (oracle.value, audio_checksum(clip), clip.sample_rate, clip.channels, clip.frame_count)
        if identity in existing:
            skipped.append(filename)
            continue
        manager.add(oracle, clip, "imported", filename, cancel)
        existing.add(identity)
        added.append(filename)
    return ImportSummary(tuple(added), tuple(skipped))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="対応表に従って Oracle サンプルを登録")
    parser.add_argument("--samples-dir", type=Path, default=PROJECT_ROOT / "samples")
    parser.add_argument("--mapping", type=Path, default=PROJECT_ROOT / "docs" / "sample-mapping.json")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        paths = AppPaths.discover(args.data_dir)
        settings = ConfigManager(paths.config).load()
        manager = TemplateManager(paths.templates, settings.audio.internal_sample_rate)
        mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
        result = import_samples(args.samples_dir, manager, mapping)
        print(f"登録 {len(result.added)} 件 / 既存と同一 {len(result.skipped)} 件")
        print(f"保存先：{paths.templates}")
        return 0
    except (AudioDataError, OSError, ValueError) as error:
        print(f"登録に失敗しました：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
