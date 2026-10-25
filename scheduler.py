from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable, Optional

from storage import Lecture, LectureStore


class LectureScheduler:
    def __init__(
        self,
        store: LectureStore,
        event_runner: Callable[[Lecture, threading.Event], None],
        status_callback: Callable[[str], None],
    ):
        self.store = store
        self.event_runner = event_runner
        self.status_callback = status_callback
        self._stop = threading.Event()
        self._stop.set()
        self._thread: Optional[threading.Thread] = None
        self._active_lock = threading.RLock()
        self._active = False
        self._active_lecture_id: Optional[int] = None
        self._cancel: Optional[threading.Event] = None
        self._idle = threading.Event()
        self._idle.set()

    @property
    def running(self) -> bool:
        with self._active_lock:
            return not self._stop.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def active(self) -> bool:
        with self._active_lock:
            return self._active

    @property
    def active_lecture_id(self) -> Optional[int]:
        with self._active_lock:
            return self._active_lecture_id

    def start(self) -> None:
        with self._active_lock:
            if self._thread is not None and self._thread.is_alive() and not self._stop.is_set():
                return
            # A stopped thread may still be leaving its loop. Give the new
            # generation its own signal so a quick restart cannot revive it.
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._loop, args=(self._stop,), name="lecture-scheduler", daemon=True
            )
            try:
                self._thread.start()
            except Exception:
                self._stop.set()
                self._thread = None
                raise

    def stop(self) -> None:
        """Stop future scheduling while allowing an active recording to finish."""
        with self._active_lock:
            self._stop.set()

    def cancel_active(self) -> bool:
        with self._active_lock:
            if self._cancel is None:
                return False
            self._cancel.set()
            return True

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        """Wait for browser and recorder cleanup before closing the application."""
        return self._idle.wait(timeout)

    def start_now(self, lecture_id: int) -> bool:
        if type(lecture_id) is not int or lecture_id <= 0:
            raise ValueError("Укажите корректный номер лекции.")
        return self._start_lecture(lecture_id)

    def start_quick(self, lecture: Lecture) -> Optional[int]:
        """Create and start one immediate lecture without racing the schedule."""
        with self._active_lock:
            if self._active:
                return None
            lecture_id = self.store.add(lecture)
            try:
                if self._start_lecture(lecture_id):
                    return lecture_id
            except Exception:
                stored = self.store.get(lecture_id)
                if stored is not None and stored.status == "scheduled":
                    self.store.delete(lecture_id)
                raise
            # A rejected launch must not become an automatic future launch.
            self.store.delete(lecture_id)
            return None

    def _notify(self, message: str) -> None:
        try:
            self.status_callback(message)
        except Exception:
            # A closed UI or failing listener must not prevent recording cleanup.
            pass

    def _start_lecture(
        self, lecture_id: Optional[int] = None, schedule_stop: Optional[threading.Event] = None
    ) -> bool:
        with self._active_lock:
            if self._active:
                return False
            # The stop flag applies only to automatic launches; manual launches
            # work with the schedule disabled.
            if lecture_id is None and (
                self._stop.is_set() or schedule_stop is not self._stop
            ):
                return False
            now = datetime.now()
            lecture = self.store.claim_due(now) if lecture_id is None else self.store.claim(lecture_id, now)
            if lecture is None:
                return False
            cancel = threading.Event()
            self._active = True
            self._active_lecture_id = lecture.id
            self._cancel = cancel
            self._idle.clear()
            worker = threading.Thread(
                target=self._run_lecture,
                args=(lecture, cancel),
                name=f"lecture-{lecture.id}",
                daemon=True,
            )
            try:
                worker.start()
            except Exception as exc:
                try:
                    self.store.finish(lecture.id, str(exc) or type(exc).__name__)
                finally:
                    self._active = False
                    self._active_lecture_id = None
                    self._cancel = None
                    self._idle.set()
                raise
            return True

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self._start_lecture(schedule_stop=stop)
            except Exception as exc:
                self._notify(f"Ошибка расписания: {str(exc) or type(exc).__name__}")
            stop.wait(1)

    def _run_lecture(self, lecture: Lecture, cancel: threading.Event) -> None:
        error = None
        cancelled = False
        recording = None
        try:
            if not cancel.is_set():
                self._notify(f"Подключение: {lecture.title}")
                self.event_runner(lecture, cancel)
            cancelled = cancel.is_set()
        except Exception as exc:
            cancelled = type(exc).__name__ == "LectureCancelled" or getattr(exc, "cancelled", False) is True
            if not cancelled:
                error = str(exc) or type(exc).__name__
        finally:
            try:
                self.store.finish(lecture.id, error, cancelled=cancelled)
                recording = self.store.recording_for(lecture.id)
            except Exception as exc:
                error = f"Не удалось сохранить результат: {str(exc) or type(exc).__name__}"
            finally:
                if error:
                    self._notify(f"Ошибка «{lecture.title}»: {error}")
                elif recording and recording["status"] == "discarded":
                    self._notify(f"{lecture.title}: {recording['note']}")
                elif cancelled:
                    self._notify(f"Остановлена: {lecture.title}")
                else:
                    self._notify(f"Запись завершена: {lecture.title}" if recording else f"Подключение завершено: {lecture.title}")
                with self._active_lock:
                    self._active = False
                    self._active_lecture_id = None
                    self._cancel = None
                    self._idle.set()
