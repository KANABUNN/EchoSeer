"""A single owning worker serializes PortAudio operations and meters audio."""

from dataclasses import dataclass
import logging
from queue import Empty, Queue
from threading import Event, Lock, Thread
import time
from typing import Any, Callable

import numpy as np

from audio.backend import AudioDevice, AudioError
from audio.capture import CaptureSession
from audio.device_manager import DeviceCatalog, DeviceManager, resolve_device
from audio.level import AudioLevel, measure_level
from audio.ring_buffer import RingBuffer
from config.schema import DeviceIdentity

logger = logging.getLogger("oracle_assistant.audio")
METER_INTERVAL = 0.05
HEALTH_INTERVAL = 0.25
SILENT_AFTER = 0.25
RECONNECT_INTERVAL = 2.0


@dataclass(frozen=True, slots=True)
class CaptureStatus:
    state: str
    message: str = ""
    device: AudioDevice | None = None


AudioEvent = DeviceCatalog | CaptureStatus | AudioLevel


class CaptureService:
    def __init__(
        self, sink: Callable[[AudioEvent], None], buffer_duration: float = 10,
        manager: DeviceManager | None = None, retry_seconds: float = RECONNECT_INTERVAL,
    ) -> None:
        if not 5 <= buffer_duration <= 30 or retry_seconds <= 0:
            raise ValueError("Invalid buffer or reconnect duration")
        self._sink = sink
        self._buffer_duration = buffer_duration
        self._manager = manager or DeviceManager()
        self._retry_seconds = retry_seconds
        self._commands: Queue[tuple[str, AudioDevice | None]] = Queue()
        self._closing = Event()
        self._thread_lock = Lock()
        self._thread: Thread | None = None
        self._buffer_lock = Lock()
        self._buffer: RingBuffer | None = None
        # The following fields belong exclusively to the worker.
        self._interface: Any = None
        self._session: CaptureSession | None = None
        self._target: DeviceIdentity | None = None
        self._next_retry = 0.0
        self._next_health = 0.0
        self._next_meter = 0.0
        self._last_audio = 0.0
        self._dropped = 0
        self._overflows = 0

    @property
    def buffer(self) -> RingBuffer | None:
        with self._buffer_lock:
            return self._buffer

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_worker(self) -> None:
        with self._thread_lock:
            if self._closing.is_set():
                raise AudioError("音声取得は終了処理中です。")
            if self._thread is None:
                self._commands.put(("refresh", None))
                self._thread = Thread(target=self._run, name="OracleAudioWorker", daemon=False)
                self._thread.start()

    def refresh(self) -> None:
        self.start_worker()
        self._commands.put(("refresh", None))

    def start(self, device: AudioDevice) -> None:
        self.start_worker()
        self._commands.put(("start", device))

    def stop(self) -> None:
        if not self._closing.is_set():
            self._commands.put(("stop", None))

    def shutdown(self, timeout: float = 5.0) -> bool:
        self._closing.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.is_alive

    def _publish(self, event: AudioEvent) -> None:
        self._sink(event)

    def _close_interface(self) -> None:
        interface, self._interface = self._interface, None
        if interface is not None:
            try:
                interface.terminate()
            except (OSError, RuntimeError) as error:
                logger.warning("Could not terminate PortAudio: %s", error)

    def _stop_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            session.close()

    def _catalog(self) -> DeviceCatalog:
        self._close_interface()
        self._interface = self._manager.create_interface()
        catalog = self._manager.enumerate(self._interface)
        self._publish(catalog)
        return catalog

    def _begin(self, device: AudioDevice) -> None:
        ring = RingBuffer(device.sample_rate, device.channels, self._buffer_duration)
        session = CaptureSession(
            self._interface, self._manager.module, self._manager.backend(device.backend), device,
        )
        if self._closing.is_set():
            session.close()
            return
        self._session = session
        session.start()
        with self._buffer_lock:
            self._buffer = ring
        self._dropped = self._overflows = 0
        now = time.monotonic()
        self._last_audio = now
        self._next_health = now + HEALTH_INTERVAL
        self._next_meter = now
        self._target = device.identity()
        logger.info("Audio LIVE: %s / %s, %s Hz, %s channels", device.host_api, device.name, device.sample_rate, device.channels)
        self._publish(CaptureStatus("LIVE", "音声を取得しています。", device))

    def _silent_level(self) -> AudioLevel:
        ring = self.buffer
        stats = self._session.stats() if self._session is not None else None
        return measure_level(
            np.empty((0, 1), dtype=np.float32),
            ring.duration_seconds if ring is not None else 0,
            stats.received_frames if stats is not None else 0,
            stats.dropped_frames if stats is not None else 0,
            stats.overflow_count if stats is not None else 0,
        )

    def _handle(self, command: str, device: AudioDevice | None) -> None:
        if command == "stop":
            self._target = None
            self._publish(CaptureStatus("STOPPING"))
            silent = self._silent_level()
            self._stop_session()
            self._close_interface()
            self._publish(silent)
            self._publish(CaptureStatus("STOPPED", "音声取得を停止しました。"))
        elif command == "refresh":
            if self._session is not None or self._target is not None:
                return
            self._publish(CaptureStatus("DISCOVERING", "音声デバイスを確認しています。"))
            self._catalog()
            self._publish(CaptureStatus("STOPPED"))
        elif command == "start" and device is not None:
            if self._session is not None and self._session.device == device:
                return
            self._target = None
            self._publish(CaptureStatus("STARTING", "音声入力を開始しています。", device))
            self._stop_session()
            catalog = self._catalog()
            if not self._closing.is_set():
                self._begin(resolve_device(catalog.devices, device.identity()))

    def _lost(self, reason: str) -> None:
        silent = self._silent_level()
        self._stop_session()
        self._close_interface()
        self._next_retry = time.monotonic() + self._retry_seconds
        logger.warning("Audio device lost: %s", reason)
        self._publish(silent)
        self._publish(CaptureStatus(
            "DEVICE_LOST", "Audio device lost：音声入力が停止しました。再接続を試みます。",
        ))

    def _consume(self) -> None:
        session = self._session
        ring = self.buffer
        if session is None or ring is None:
            return
        block = session.pop()
        now = time.monotonic()
        stats = session.stats()
        if stats.error:
            raise AudioError(stats.error)
        if stats.dropped_frames != self._dropped or stats.overflow_count != self._overflows:
            ring.clear()
            self._dropped, self._overflows = stats.dropped_frames, stats.overflow_count
            logger.warning("Audio discontinuity: dropped=%s overflow=%s", self._dropped, self._overflows)
        if block is not None:
            ring.write(block.samples)
            self._last_audio = now
        if now >= self._next_meter:
            if block is not None:
                values = block.samples
            elif now - self._last_audio >= SILENT_AFTER:
                values = np.empty((0, session.device.channels), dtype=np.float32)
            else:
                values = None
            if values is not None:
                level = measure_level(
                    values, ring.duration_seconds, stats.received_frames,
                    stats.dropped_frames, stats.overflow_count,
                )
                self._publish(level)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Audio RMS=%.5f peak=%.5f buffered=%.2fs", level.rms, level.peak, level.buffered_seconds)
                self._next_meter = now + METER_INTERVAL
        if now >= self._next_health:
            self._next_health = now + HEALTH_INTERVAL
            if not session.is_active():
                raise AudioError("Input stream became inactive")

    def _reconnect(self) -> None:
        target = self._target
        if target is None:
            return
        self._publish(CaptureStatus("RECONNECTING", "同じ音声デバイスへ再接続しています。"))
        try:
            catalog = self._catalog()
            if not self._closing.is_set():
                self._begin(resolve_device(catalog.devices, target))
        except (AudioError, OSError, ValueError, RuntimeError, MemoryError) as error:
            self._stop_session()
            self._close_interface()
            self._next_retry = time.monotonic() + self._retry_seconds
            self._publish(CaptureStatus("DEVICE_LOST", f"再接続待機中：{error}"))

    @staticmethod
    def _command_error_message(error: Exception) -> str:
        if isinstance(error, OSError) and error.errno == -9996:
            return (
                "この音声デバイスを開始できません。Windows / VoiceMeeter の出力先と"
                "音声設定を確認し、再検索または別のデバイスで再試行してください。"
            )
        return f"音声入力エラー：{error}"

    def _run(self) -> None:
        terminal_message = ""
        try:
            while not self._closing.is_set():
                try:
                    command, device = self._commands.get(timeout=0 if self._session else 0.05)
                except Empty:
                    command, device = "", None
                if command:
                    try:
                        self._handle(command, device)
                    except (AudioError, OSError, ValueError, RuntimeError, MemoryError) as error:
                        self._target = None
                        self._stop_session()
                        self._close_interface()
                        logger.exception("Audio command failed")
                        self._publish(CaptureStatus("ERROR", self._command_error_message(error)))
                if self._session is not None:
                    try:
                        self._consume()
                    except (AudioError, OSError, ValueError, RuntimeError) as error:
                        self._lost(str(error))
                elif self._target is not None and time.monotonic() >= self._next_retry:
                    self._reconnect()
        except Exception:
            # A thread boundary must release its resources even for programming errors.
            logger.exception("Unexpected audio worker failure")
            terminal_message = "音声ワーカーでエラーが発生しました。アプリを再起動してください。"
        finally:
            self._stop_session()
            self._close_interface()
            self._publish(CaptureStatus("CLOSED", terminal_message))
            logger.info("Audio worker closed")
