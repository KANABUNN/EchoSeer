"""Cancellable continuous recognition worker; stale generations never update the UI."""
from dataclasses import dataclass, replace
import logging
from threading import Event, Lock, Thread
import time

from PySide6.QtCore import QObject, Signal

from audio.operations import OperationCancelled
from config.schema import LoggingSettings, RecognitionSettings, SequenceSettings
from detector.events import MAX_EVENT_BYTES
from detector.live_sequence import LiveResult, LiveSequenceSession
from encounter.sequence import SequenceEngine, SequenceState

logger = logging.getLogger("oracle_assistant.live")


@dataclass(frozen=True, slots=True)
class LiveUpdate:
    generation: int
    result: LiveResult
    message: str = ""


@dataclass(frozen=True, slots=True)
class LiveControl:
    generation: int
    ring: object | None
    round_index: int
    paused: str
    fault: str
    cancel: Event


class LiveController(QObject):
    updated = Signal(object)

    def __init__(self, classifier_factory, recognition=None, sequence=None, recorder=None, parent=None):
        super().__init__(parent)
        self.classifier_factory = classifier_factory
        self.recognition = replace(recognition or RecognitionSettings())
        self.sequence = replace(sequence or SequenceSettings())
        self.recorder = recorder
        self._logging = replace(recorder.settings if recorder else LoggingSettings())
        self._state = LiveControl(0, None, 1, "", "", Event())
        self._lock, self._wake, self._closing = Lock(), Event(), Event()
        self._thread = None
        self._last_cue = None

    @property
    def latest_cue(self):
        with self._lock:
            return self._last_cue

    @property
    def generation(self):
        with self._lock:
            return self._state.generation

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _change(self, **values):
        with self._lock:
            if self._closing.is_set():
                return
            self._last_cue = None
            old = self._state
            old.cancel.set()
            self._state = replace(old, generation=old.generation + 1, cancel=Event(), **values)
            active = self._state.ring is not None
        self._wake.set()
        if self._thread is None and active:
            self._thread = Thread(target=self._run, name="OracleLiveWorker", daemon=False)
            self._thread.start()

    def attach(self, ring, round_index=1):
        self._change(ring=ring, round_index=round_index, fault="")

    def stop(self):
        self._change(ring=None, fault="")

    def select_round(self, round_index):
        SequenceEngine(self.sequence).encounter.expected_count(round_index)
        self._change(round_index=round_index, fault="")

    def set_paused(self, message=""):
        with self._lock:
            same = self._state.paused == message
        if not same:
            self._change(paused=message)

    def invalidate(self, reason):
        self._change(fault=reason)

    def set_logging(self, settings):
        with self._lock:
            self._logging = replace(settings)

    def shutdown(self, timeout=0):
        self._closing.set()
        with self._lock:
            self._state.cancel.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        return not self.is_alive

    def _emit(self, state, result, message="", cue=None):
        if not self._closing.is_set() and not state.cancel.is_set():
            with self._lock:
                if self._state.generation == state.generation:
                    self._last_cue = cue
                    self._state = replace(self._state, round_index=result.snapshot.round_index)
            self.updated.emit(LiveUpdate(state.generation, result, message))

    def _blank(self, state):
        engine = SequenceEngine(self.sequence)
        snapshot = engine.arm(0, state.round_index)
        if state.ring is None:
            snapshot = replace(snapshot, state=SequenceState.IDLE, reason="STOPPED")
        return LiveResult(snapshot)

    def _run(self):
        generation, session, cursor, stream_id = -1, None, 0, None
        while not self._closing.is_set():
            with self._lock:
                state, logging_settings = self._state, replace(self._logging)
            try:
                if generation != state.generation:
                    generation = state.generation
                    if state.fault:
                        if session is not None:
                            result = session.invalidate(state.fault, state.cancel)
                        else:
                            engine = SequenceEngine(self.sequence)
                            engine.arm(0, state.round_index)
                            engine.invalidate(0, state.fault)
                            result = LiveResult(engine.verify(self.recognition, state.cancel))
                        self._emit(state, result)
                    elif state.ring is not None and not state.paused:
                        _, stream_id, stamp, _, cursor = state.ring.snapshot_event(0)
                        origin = (stamp or time.monotonic()) - cursor / state.ring.sample_rate
                        session = LiveSequenceSession(self.classifier_factory(), state.ring.sample_rate,
                            state.ring.channels, stream_id, cursor, origin, state.round_index,
                            self.recognition, self.sequence, self.recorder)
                        self._emit(state, LiveResult(session.engine.snapshot()))
                    else:
                        session = None
                        self._emit(state, self._blank(state), state.paused)
                if session is not None and state.ring is not None and not state.paused and not state.fault:
                    if self.recorder:
                        self.recorder.settings = logging_settings
                        self.recorder.events.enabled = logging_settings.event_logs
                    limit = min(round(state.ring.sample_rate * .5),
                                MAX_EVENT_BYTES // (4 * state.ring.channels))
                    read = state.ring.read_since(cursor, stream_id, max_frames=limit)
                    cursor = read.end_frame
                    if read.overrun:
                        reason = "SOURCE_CHANGED" if read.stream_id != stream_id else "AUDIO_GAP"
                        self._emit(state, session.invalidate(reason, state.cancel))
                        stream_id = read.stream_id
                    elif len(read.samples):
                        session.feed(read.samples, state.cancel, lambda result: self._emit(state,result,cue=session.evidence.latest))
                        continue
            except OperationCancelled:
                continue
            except Exception:
                logger.exception("Continuous live recognition failed")
                if session is not None:
                    try:
                        self._emit(state, session.invalidate("LIVE_ANALYSIS_ERROR", state.cancel))
                    except OperationCancelled:
                        continue
                    except Exception:
                        logger.exception("Could not preserve interrupted live result")
                        self._emit(state, self._blank(state), "認識に失敗しました。Resetで再開してください。")
                else:
                    self._emit(state, self._blank(state), "認識を開始できません。入力とテンプレートを確認してResetしてください。")
                session = None
            self._wake.wait(.02)
            self._wake.clear()
