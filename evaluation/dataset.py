"""Bounded labelled manifests with contained WAV paths and explicit timing truth."""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path

from audio.data import AudioDataError
from audio.operations import check_cancel
from config.defaults import ORACLE_KEYS

MAX_CASES = 500
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024


def read_json(path, limit=MAX_JSON_BYTES):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("JSON size limit")
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError) as error:
        raise AudioDataError("JSONの形式またはサイズが不正です。") from error


def file_checksum(path, cancel=None):
    h = sha256()
    with Path(path).open("rb") as stream:
        while data := stream.read(1024 * 1024):
            check_cancel(cancel)
            h.update(data)
    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetCase:
    case_id: str
    path: Path
    relative_path: str
    mode: str
    expected: tuple[str, ...]
    timestamps: tuple[tuple[float, float], ...] | None = None
    category: str = ""
    source_kind: str = "unspecified"
    notes: str = ""
    round_index: int = 1


@dataclass(frozen=True, slots=True)
class Dataset:
    path: Path
    cases: tuple[DatasetCase, ...]
    tolerance: float = .15
    description: str = ""


def load_dataset(path):
    path = Path(path).resolve()
    root = path.parent
    value = read_json(path)
    try:
        allowed = {"schema_version", "cases", "description", "tolerance_seconds", "provenance"}
        if not isinstance(value, dict) or set(value)-allowed or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("Dataset schema")
        tolerance = value.get("tolerance_seconds", .15)
        if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
            raise ValueError("Timing tolerance")
        description=value.get("description","")
        if not isinstance(description,str) or len(description)>2000:
            raise ValueError("Description")
        rows=value.get("cases")
        if not isinstance(rows,list) or not 1 <= len(rows) <= MAX_CASES:
            raise ValueError("Cases")
        cases, ids, total = [], set(), 0
        for index, row in enumerate(rows):
            allowed={"id","file","mode","round","expected","timestamps","category","source_kind","notes","provenance"}
            if not isinstance(row,dict) or set(row)-allowed:
                raise ValueError("Case fields")
            name=row.get("id",f"case-{index+1}")
            if not isinstance(name,str) or not name.strip() or len(name)>120 or name in ids:
                raise ValueError("Case id")
            ids.add(name)
            relative=row["file"]
            if not isinstance(relative,str) or not relative or len(relative)>500 or Path(relative).is_absolute():
                raise ValueError("WAV path")
            source=(root/relative).resolve()
            if not source.is_relative_to(root) or source.suffix.lower()!=".wav" or not source.is_file():
                raise ValueError("WAV outside dataset or absent")
            total += source.stat().st_size
            if total > MAX_INPUT_BYTES:
                raise ValueError("Dataset byte limit")
            mode=row.get("mode","events")
            expected=row["expected"]
            if mode not in ("clip","events","round") or not isinstance(expected,list) or len(expected)>128:
                raise ValueError("Mode/expected")
            if any(type(item) is not str or item not in ORACLE_KEYS for item in expected):
                raise ValueError("Oracle truth")
            if mode=="clip" and len(expected)>1:
                raise ValueError("Clip truth")
            round_index=row.get("round",len(expected)-2 if mode=="round" else 1)
            if type(round_index) is not int or not 1<=round_index<=5:
                raise ValueError("Round")
            if mode=="round" and (len(expected)!=round_index+2 or len(set(expected))!=len(expected)):
                raise ValueError("Round expected count/uniqueness")
            stamps=row.get("timestamps")
            if stamps is not None:
                if mode!="events" or not isinstance(stamps,list) or len(stamps)!=len(expected):
                    raise ValueError("Timestamps")
                timings=[]
                previous=0
                for stamp in stamps:
                    if not isinstance(stamp,dict) or set(stamp)!={"start","end"}:
                        raise ValueError("Timing fields")
                    start,end=stamp["start"],stamp["end"]
                    if any(type(t) not in (int,float) or not math.isfinite(t) for t in (start,end)) or not previous <= start < end <= 120:
                        raise ValueError("Timing order")
                    timings.append((float(start),float(end))); previous=end
                stamps=tuple(timings)
            category=row.get("category","")
            if category not in ("","success","low_confidence","incorrect","combat_noise"):
                raise ValueError("Category")
            source_kind=row.get("source_kind","unspecified")
            if source_kind not in ("unspecified","gameplay","isolated","synthetic"):
                raise ValueError("Source kind")
            notes=row.get("notes","")
            if not isinstance(notes,str) or len(notes)>2000:
                raise ValueError("Notes")
            cases.append(DatasetCase(name,source,relative,mode,tuple(expected),stamps,category,source_kind,notes,round_index))
        return Dataset(path,tuple(cases),float(tolerance),description)
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise AudioDataError("Datasetの正解情報・WAVパス・制限を確認してください。") from error
