"""Full finite replay event analysis without a round FSM stopping at the first problem."""
from dataclasses import dataclass, replace
import json

from audio.data import AudioClip, AudioDataError
from audio.event import EventContext
from audio.operations import check_cancel
from audio.sources import ClipSource
from config.schema import RecognitionSettings
from detector.confidence import ConfidenceEngine, ConfidenceLevel
from detector.events import RmsEventDetector
from replay.analyzer import Analyzer
from replay.provenance import canonical, digest, recognition_profile
from replay.sequence_analyzer import CueTrace, MAX_SEQUENCE_SECONDS


@dataclass(frozen=True, slots=True)
class TimelineResult:
    traces: tuple[CueTrace, ...]
    profile_json: str
    notices: tuple[str, ...] = ()

    @property
    def profile(self):
        return json.loads(self.profile_json)

    @property
    def profile_hash(self):
        return digest(self.profile)


def cue_clip(original, trace):
    if not 0 <= trace.start_frame < trace.end_frame <= original.frame_count:
        raise AudioDataError("選択区間が音声の範囲外です。再解析してください。")
    return AudioClip(original.samples[trace.start_frame:trace.end_frame], original.sample_rate)


class ReplayTimelineAnalyzer:
    def __init__(self, classifier, recognition=None, analyzer=None, recorder=None):
        self.classifier = classifier
        self.recognition = replace(recognition or RecognitionSettings())
        self.analyzer = analyzer or Analyzer(classifier.sample_rate)
        self.recorder = recorder

    def analyze(self, analysis, cancel=None):
        original = analysis.original
        if original.duration_seconds > MAX_SEQUENCE_SECONDS:
            raise AudioDataError("Timeline解析は120秒以内のWAVを選んでください。")
        profile = recognition_profile(self.classifier, self.recognition, cancel)
        confidence, traces, notices = ConfidenceEngine(self.recognition), [], []
        for window in RmsEventDetector(self.recognition.detection_threshold).detect(original, cancel):
            check_cancel(cancel)
            result = self.analyzer.analyze(ClipSource(window.clip), cancel)
            raw = self.classifier.classify_preprocessed(result.processed, cancel)
            decision = confidence.evaluate(raw, EventContext("replay", window.onset_frame / original.sample_rate,
                                                            start_frame=window.start_frame, end_frame=window.end_frame))
            if window.reason:
                decision = replace(decision, oracle=None, status=ConfidenceLevel.REJECTED, reason=window.reason)
            traces.append(CueTrace(window.start_frame, window.end_frame, window.onset_frame,
                                   window.signal_end_frame, decision, raw))
            notices.extend(raw.notices)
            if self.recorder:
                notices.extend(self.recorder.record(decision, raw, window.clip, result.checksum, cancel).notices)
        if recognition_profile(self.classifier, self.recognition, cancel) != profile:
            raise AudioDataError("解析中にテンプレートが変わりました。同じ条件で再解析してください。")
        return TimelineResult(tuple(traces), canonical(profile), tuple(dict.fromkeys(notices)))
