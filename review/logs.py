"""Treat JSONL as data; contain audio paths and bound files, lines, bytes and rows."""
from heapq import heappush,heappop
from dataclasses import dataclass
import json
from pathlib import Path

from audio.data import AudioDataError
from audio.operations import check_cancel
from replay.provenance import canonical

MAX_FILES=200
MAX_BYTES=64*1024*1024
MAX_LINE=256*1024
MAX_ROWS=2000


@dataclass(frozen=True,slots=True)
class LogEvent:
    payload_json: str
    audio_relative: str | None
    @property
    def payload(self):
        return json.loads(self.payload_json)


@dataclass(frozen=True,slots=True)
class LogCatalog:
    root: Path
    events: tuple[LogEvent,...]
    notices: tuple[str,...] = ()


def audio_path(root,relative):
    root=Path(root).resolve()
    if not isinstance(relative,str) or not relative or Path(relative).is_absolute():
        raise AudioDataError("ログの音声パスが不正です。")
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower()!=".wav" or not path.is_file():
        raise AudioDataError("ログ音声が見つからないか保存フォルダー外を参照しています。")
    return path


def load_logs(root,cancel=None):
    root=Path(root).resolve()
    rows=[]; files=[]; notices=[]; total=invalid=serial=0
    if not root.is_dir():
        return LogCatalog(root,(),("保存ログはまだありません。",))
    for path in root.rglob("*.jsonl"):
        check_cancel(cancel)
        files.append(path)
        if len(files)>=MAX_FILES:
            notices.append("最大200ファイルまで読み込みました。古いログはフォルダーを分けて確認してください。")
            break
    for path in sorted(files,reverse=True):
        check_cancel(cancel)
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            invalid+=1;continue
        with path.open("rb") as stream:
            consumed=0
            while line:=stream.readline(MAX_LINE+1):
                check_cancel(cancel)
                total+=len(line); consumed+=len(line)
                if total>MAX_BYTES or consumed>16*1024*1024:
                    notices.append("ログの読込サイズ上限に達しました。")
                    break
                if len(line)>MAX_LINE:
                    invalid+=1;continue
                try:
                    value=json.loads(line.decode("utf-8"),parse_constant=lambda value:(_ for _ in ()).throw(ValueError(value)))
                    if not isinstance(value,dict) or value.get("event_type") not in ("oracle","audio_evidence"):
                        continue
                    relative=value.get("audio_path")
                    if relative is not None:
                        try:audio_path(root,relative)
                        except AudioDataError:
                            invalid+=1; relative=None
                    stamp=value.get("timestamp",0)
                    stamp=stamp if type(stamp) in (int,float) else 0
                    serial+=1
                    heappush(rows,(stamp,serial,LogEvent(canonical(value),relative)))
                    if len(rows)>MAX_ROWS:heappop(rows)
                except (ValueError,UnicodeError):
                    invalid+=1
        if total>MAX_BYTES:break
    if invalid:notices.append(f"破損・参照できないログ情報 {invalid}件を検出しました。")
    ordered=[entry[2] for entry in sorted(rows,reverse=True)]
    return LogCatalog(root,tuple(ordered),tuple(dict.fromkeys(notices)))
