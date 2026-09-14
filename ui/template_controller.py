"""One cancellable template/file/playback worker; widgets stay on the Qt thread."""
from dataclasses import dataclass
import logging
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QObject, Qt, Signal, Slot
from audio.data import AudioClip, AudioDataError
from audio.operations import OperationCancelled, check_cancel
from audio.playback import AudioPlayer
from encounter.vog_oracles import OracleId
from templates.manager import DeletedSample, TemplateCatalog, TemplateManager
from templates.quality import QualityReport

logger = logging.getLogger("oracle_assistant.templates")


def user_error(error):
    if isinstance(error, AudioDataError):
        return str(error)
    if isinstance(error, OSError):
        return "ファイルの読み込み・保存に失敗しました。保存先・アクセス権・空き容量を確認してください。"
    return "処理に失敗しました。入力を確認して再試行してください。詳細は診断ログで確認できます。"



@dataclass(frozen=True, slots=True)
class TemplateTask:
    kind: str
    oracle: OracleId = OracleId.L1
    paths: tuple[Path, ...] = ()
    sample_id: str = ""
    clip: AudioClip | None = None
    deleted: DeletedSample | None = None
    volume: float = 0.2


@dataclass(frozen=True, slots=True)
class TemplateResult:
    kind: str
    catalog: TemplateCatalog | None = None
    selected_id: str = ""
    deleted: DeletedSample | None = None
    message: str = ""
    quality: QualityReport | None = None


class TemplateController(QObject):
    finished = Signal(object)
    busy_changed = Signal(bool)

    def __init__(self, root: Path, sample_rate: int = 48000, parent: QObject | None = None,
                 manager: TemplateManager | None = None, player: AudioPlayer | None = None) -> None:
        super().__init__(parent)
        self.manager = manager or TemplateManager(root, sample_rate)
        self.player = player or AudioPlayer()
        self._queue: Queue[TemplateTask] = Queue()
        self._closing, self._play_stop = Event(), Event()
        self._thread: Thread | None = None
        self._busy = False
        self.finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def submit(self, task: TemplateTask) -> bool:
        if self._busy or self._closing.is_set():
            return False
        self._busy = True
        self._play_stop.clear()
        self.busy_changed.emit(True)
        self._queue.put(task)
        if self._thread is None:
            self._thread = Thread(target=self._run, name="OracleTemplateWorker", daemon=False)
            self._thread.start()
        return True

    def stop_playback(self) -> None:
        self._play_stop.set()

    @Slot(object)
    def _finish(self, result: TemplateResult) -> None:
        self._busy = False
        self.busy_changed.emit(False)

    def shutdown(self, timeout: float = 0) -> bool:
        self._closing.set()
        self._play_stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.is_alive

    def _run_task(self, task: TemplateTask) -> TemplateResult:
        check_cancel(self._closing)
        message, selected, deleted, quality = "", task.sample_id, None, None
        if task.kind == "import":
            successes, errors = [], []
            for path in task.paths:
                check_cancel(self._closing)
                try:
                    sample = self.manager.import_wav(task.oracle, path, self._closing)
                    successes.append(sample)
                except OperationCancelled:
                    raise
                except Exception as error:
                    logger.exception("Template import failed: %s", path)
                    errors.append(f"{path.name}：{user_error(error)}")
            selected = successes[-1].metadata.sample_id if successes else ""
            message = f"{len(successes)} 件を登録しました。"
            if errors:
                message += "\n" + "\n".join(errors)
        elif task.kind == "record":
            sample = self.manager.add(task.oracle, task.clip, cancel=self._closing)
            selected = sample.metadata.sample_id
            message = "録音サンプルを登録しました。"
        elif task.kind == "delete":
            deleted = self.manager.delete(task.oracle, task.sample_id, self._closing)
            selected, message = "", "削除しました。「削除を戻す」で復元できます。"
        elif task.kind == "restore":
            sample = self.manager.restore(task.deleted, self._closing)
            selected, message = sample.metadata.sample_id, "サンプルを復元しました。"
        elif task.kind == "quality":
            quality = self.manager.check_quality(task.oracle, task.sample_id, self._closing)
            message = "保存音声の品質確認が完了しました。"
        elif task.kind == "play":
            clip = self.manager.load_audio(task.oracle, task.sample_id, self._closing)
            self.player.play(clip, task.volume, self._play_stop)
            return TemplateResult(task.kind, message="再生が終了しました。")
        elif task.kind != "refresh":
            raise ValueError("Unknown template task")
        catalog = self.manager.catalog(self._closing)
        return TemplateResult(task.kind, catalog, selected, deleted, message, quality)

    def _run(self) -> None:
        while not self._closing.is_set():
            try:
                task = self._queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                result = self._run_task(task)
            except OperationCancelled:
                result = TemplateResult(task.kind, message="処理を中止しました。")
            except Exception as error:
                logger.exception("Template operation failed")
                result = TemplateResult(task.kind, message=user_error(error))
            self.finished.emit(result)
