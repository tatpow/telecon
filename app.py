from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from storage import ALERT_DEFAULTS, Lecture, LectureStore, TELEMOST_HOME_URL, user_data_dir, validate_telemost_url, validate_profile_url
from power import SleepInhibitor
from notifications import HiddenWindowAlerts
from capabilities import desktop_capabilities
from tray import TRAY_DEFAULTS


DATE_FORMAT = "%Y-%m-%d %H:%M"
_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _default_lecture_start(now: datetime | None = None) -> datetime:
    return ((now or datetime.now()) + timedelta(minutes=5)).replace(second=0, microsecond=0)


def _group_lectures_by_date(lectures: list[Lecture]) -> list[tuple[date, list[Lecture]]]:
    groups: dict[date, list[Lecture]] = {}
    for lecture in sorted(lectures, key=lambda item: (item.starts_at, item.id or 0)):
        groups.setdefault(lecture.starts_at.date(), []).append(lecture)
    return list(groups.items())


def _date_separator_label(day: date, today: date | None = None) -> str:
    today = today or date.today()
    prefix = "СЕГОДНЯ  ·  " if day == today else "ЗАВТРА  ·  " if day == today + timedelta(days=1) else ""
    return f"{prefix}{_WEEKDAYS[day.weekday()]}, {day.day} {_MONTHS[day.month - 1]} {day.year}"


def _positive_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Некорректный номер лекции.")
    return value


def _local_datetime(value, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Укажите {name}.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Некорректное время: {name}.") from None
    if result.tzinfo is not None:
        raise ValueError("Используйте местное время без часового пояса.")
    return result.replace(microsecond=0)


class DesktopApi:
    """Small, JSON-only bridge. GUI and integrations are imported only when used."""

    def __init__(self, store: LectureStore | None = None, *, scheduler_factory=None,
                 event_runner: Callable | None = None, recording_checker: Callable | None = None,
                 sleep_inhibitor=None, alert_backend=None):
        self._store = store or LectureStore()
        self._store.recover_interrupted()
        self._scheduler_factory = scheduler_factory
        self._event_runner = event_runner
        self._recording_checker = recording_checker
        self._scheduler = None
        self._lock = threading.RLock()
        self._message = "Расписание на паузе"
        settings = self._store.get_settings()
        self._config = {key: settings[key] for key in (
            "recording_enabled", "ffmpeg_path", "recordings_dir", "prevent_sleep", "default_duration_minutes",
            "minimum_recording_minutes", "recording_name_template", "recording_format",
            "recording_quality", "recording_fps", "recording_width", "recording_height", *ALERT_DEFAULTS, *TRAY_DEFAULTS)}
        self._tray = None
        self._capabilities = desktop_capabilities()
        if not self._capabilities["recording"]:
            self._config["recording_enabled"] = False
        self._alerts = HiddenWindowAlerts(self._config, alert_backend)
        self._meeting_target = None
        self._sleep = sleep_inhibitor if sleep_inhibitor is not None else SleepInhibitor()
        self._schedule_enabled = False
        self._recording_active = False
        self._session_recording = False
        self._last_recording = None
        self._closing = False
        self._close_allowed = False
        self._account_thread: threading.Thread | None = None
        self._account_cancel = threading.Event()

    def _account_active(self) -> bool:
        return self._account_thread is not None and self._account_thread.is_alive()

    def _reply(self, action):
        try:
            return {"ok": True, "data": action()}
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            return {"ok": False, "error": message}

    def _on_status(self, message: str) -> None:
        with self._lock:
            self._message = str(message)

    def _sync_power(self):
        self._sleep.set_active(self._capabilities["prevent_sleep"] and self._config["prevent_sleep"] and
                               (self._schedule_enabled or self._recording_active))

    def _on_visibility(self, hidden, target=None):
        with self._lock:
            self._meeting_target = target
            self._alerts.update(bool(hidden) and not self._closing)

    def restore_meeting(self):
        def restore():
            with self._lock:
                if self._meeting_target is None or self._closing:
                    raise ValueError("Нет открытого окна встречи.")
                from recording import restore_target
                restore_target(self._meeting_target)
        return self._reply(restore)

    def preview_alert_sound(self, payload):
        def preview():
            if not isinstance(payload, dict):
                raise ValueError("Укажите громкость предупреждения.")
            with self._lock:
                if self._closing:
                    raise ValueError("Приложение закрывается.")
                self._alerts.preview(payload.get("volume"))
        return self._reply(preview)

    def preview_recording_name(self, payload):
        def preview():
            if not isinstance(payload, dict) or not isinstance(payload.get("template"), str) or len(payload["template"]) > 300:
                raise ValueError("Укажите шаблон имени файла до 300 символов.")
            from recording_names import recording_stem
            from recording_names import RECORDING_FORMATS
            extension = payload.get("format", self._config["recording_format"])
            if extension not in RECORDING_FORMATS:
                raise ValueError("Выберите формат видео MKV или MP4.")
            start = datetime.now().replace(second=0, microsecond=0)
            result = recording_stem(payload["template"], name="Название лекции", url="telemost.yandex.ru/j/1234567890",
                                    started_at=start, ended_at=start + timedelta(minutes=85), duration_seconds=85 * 60)
            return {"filename": result["stem"] + "." + extension, "fallback": result["fallback"]}
        return self._reply(preview)

    def _run(self, lecture: Lecture, cancel_event):
        with self._lock:
            self._recording_active = True
            config = dict(self._config)
            self._session_recording = config["recording_enabled"]
        try:
            with self._lock:
                self._sync_power()
            runner = self._event_runner
            options = {}
            if runner is None:
                from integrations import run_lecture
                runner = run_lecture
                options["visibility_callback"] = self._on_visibility
                options["recording_callback"] = lambda result: self._store.save_recording(lecture.id, result)
            output = runner(lecture, config, cancel_event=cancel_event, status_callback=self._on_status, **options)
            with self._lock:
                self._last_recording = output
            return output
        finally:
            with self._lock:
                self._on_visibility(False)
                self._recording_active = False
                self._session_recording = False
                self._sync_power()

    def _ensure_scheduler(self):
        if self._closing:
            raise ValueError("Приложение завершает лекцию и закрывается.")
        if self._account_active():
            raise ValueError("Сначала завершите вход в Яндекс и закройте открытый браузер.")
        if self._scheduler is None:
            factory = self._scheduler_factory
            if factory is None:
                from scheduler import LectureScheduler
                factory = LectureScheduler
            self._scheduler = factory(self._store, self._run, self._on_status)
        return self._scheduler

    def get_settings(self):
        def read():
            with self._lock:
                return {**self._config, "capabilities": dict(self._capabilities),
                        "tray": self._tray.state() if self._tray else
                        {"available": False, "hidden": False, "error": ""}}
        return self._reply(read)

    def get_recording_options(self):
        def read():
            if not self._capabilities["recording"]:
                raise ValueError("Запись пока доступна только в Windows.")
            from video_settings import monitor_size, resolve_video_settings
            hwnd = 0
            window = getattr(self._alerts.backend, "window", None)
            if window is not None and window.native is not None:
                hwnd = int(window.native.Handle.ToInt64())
            screen = monitor_size(hwnd)
            with self._lock:
                effective = resolve_video_settings(self._config, screen)
            return {"screen_width": screen[0], "screen_height": screen[1], "max_fps": 60,
                    "effective": effective}
        return self._reply(read)

    def save_settings(self, payload):
        def save():
            if not isinstance(payload, dict) or set(payload) - self._config.keys():
                raise ValueError("Некорректные настройки приложения.")
            if payload.get("recording_enabled") and not self._capabilities["recording"]:
                raise ValueError("Запись пока доступна только в Windows. Подключение к лекциям работает без записи.")
            if "recordings_dir" in payload and isinstance(payload["recordings_dir"], str) and payload["recordings_dir"]:
                directory = Path(payload["recordings_dir"]).expanduser().resolve()
                if directory.is_relative_to(Path(__file__).resolve().parent):
                    raise ValueError("Выберите папку записей вне исходного каталога приложения.")
            with self._lock:
                if self._closing:
                    raise ValueError("Приложение закрывается.")
                from video_settings import validate_video_settings
                merged = {**self._config, **payload}
                validate_video_settings(merged)
                if {"recording_width", "recording_height"} & payload.keys() and merged["recording_width"]:
                    options = self.get_recording_options()
                    if not options["ok"]:
                        raise ValueError(options["error"])
                    screen = options["data"]
                    if merged["recording_width"] > screen["screen_width"] or merged["recording_height"] > screen["screen_height"]:
                        raise ValueError(f"Разрешение записи не может превышать экран {screen['screen_width']}×{screen['screen_height']}.")
                old = dict(self._config)
                settings = self._store.save_settings(payload)
                self._config = {key: settings[key] for key in old}
                if not self._capabilities["recording"]:
                    self._config["recording_enabled"] = False
                try:
                    self._sync_power()
                except Exception:
                    self._config = old
                    self._store.save_settings(old)
                    self._sync_power()
                    raise
                self._alerts.configure(self._config)
                return dict(self._config)
        return self._reply(save)

    def check_recording(self):
        def check():
            with self._lock:
                config = dict(self._config)
            checker = self._recording_checker
            if checker is None:
                from recording import check_recorder
                checker = check_recorder
            return checker(config)
        return self._reply(check)

    def get_state(self):
        def state():
            lectures = []
            for lecture in self._store.list_all():
                recording = self._store.recording_for(lecture.id)
                lectures.append({
                    "id": lecture.id, "title": lecture.title, "url": lecture.url,
                    "starts_at": lecture.starts_at.isoformat(timespec="seconds"),
                    "ends_at": lecture.ends_at.isoformat(timespec="seconds"),
                    "lead_minutes": lecture.lead_minutes, "status": lecture.status,
                    "error": self._store.error_for(lecture.id),
                    "recording": ({"status": recording["status"], "duration_seconds": recording["duration_seconds"],
                                   "note": recording["note"], "filename": Path(recording["path"]).name if recording["path"] else None}
                                  if recording else None),
                })
            lectures.sort(key=lambda item: (item["starts_at"], item["id"]))
            with self._lock:
                scheduler = self._scheduler
                return {"lectures": lectures, "now": datetime.now().isoformat(timespec="seconds"),
                        "scheduler": {"running": bool(scheduler and scheduler.running),
                                      "active": bool(scheduler and scheduler.active),
                                      "active_lecture_id": scheduler.active_lecture_id if scheduler else None,
                                      "message": self._message, "closing": self._closing},
                        "recording_enabled": self._config["recording_enabled"],
                        "recording_active": bool(scheduler and scheduler.active and self._session_recording),
                        "account_login": self._account_active(),
                        "alerts": self._alerts.state(),
                        "sleep_prevented": self._sleep.active, "last_recording": self._last_recording}
        return self._reply(state)

    def save_lecture(self, payload):
        def save():
            if not isinstance(payload, dict):
                raise ValueError("Некорректные данные лекции.")
            title, url = payload.get("title"), payload.get("url")
            if not isinstance(title, str) or not isinstance(url, str):
                raise ValueError("Укажите название и ссылку на Телемост.")
            if len(title) > 300 or len(url) > 2048:
                raise ValueError("Название или ссылка слишком длинные.")
            lead = payload.get("lead_minutes", 2)
            if isinstance(lead, bool) or not isinstance(lead, int) or not 0 <= lead <= 120:
                raise ValueError("Подключение заранее: от 0 до 120 минут.")
            lecture_id = payload.get("id")
            if lecture_id is not None:
                _positive_id(lecture_id)
            lecture = Lecture(lecture_id, title.strip(), url.strip(),
                              _local_datetime(payload.get("starts_at"), "начало лекции"),
                              _local_datetime(payload.get("ends_at"), "окончание лекции"),
                              lead_minutes=lead)
            if lecture_id is None:
                lecture_id = self._store.add(lecture)
            else:
                if not self._store.update(lecture):
                    raise ValueError("Лекция уже запущена или удалена. Обновите расписание.")
            return {"id": lecture_id}
        return self._reply(save)

    def delete_lecture(self, payload):
        def delete():
            lecture_id = payload.get("id") if isinstance(payload, dict) else payload
            delete_file = payload.get("delete_recording", False) if isinstance(payload, dict) else False
            if type(delete_file) is not bool:
                raise ValueError("Выберите способ удаления лекции.")
            if delete_file and payload.get("confirmed") is not True:
                raise ValueError("Подтвердите удаление файла записи.")
            lecture = self._store.get(_positive_id(lecture_id))
            if lecture is None:
                raise ValueError("Лекция не найдена.")
            if lecture.status == "running":
                raise ValueError("Сначала завершите текущую лекцию.")
            if not self._store.delete(lecture_id, delete_recording=delete_file):
                raise ValueError("Лекцию не удалось удалить: её состояние изменилось. Обновите расписание.")
            return None
        return self._reply(delete)

    def join_now(self, lecture_id):
        def join():
            with self._lock:
                scheduler = self._ensure_scheduler()
                if scheduler.active:
                    raise ValueError("Другая лекция уже запущена.")
                self._recording_active = True
                try:
                    self._sync_power()
                    if not scheduler.start_now(_positive_id(lecture_id)):
                        raise ValueError("Лекция уже запущена или время этой лекции прошло.")
                except Exception:
                    self._recording_active = False
                    self._sync_power()
                    raise
                return None
        return self._reply(join)

    def quick_join(self, payload):
        def join():
            if not isinstance(payload, dict):
                raise ValueError("Укажите ссылку и время окончания.")
            url = payload.get("url")
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError("Укажите ссылку на Телемост длиной до 2048 символов.")
            url = validate_telemost_url(url)
            now = datetime.now().replace(microsecond=0)
            end = _local_datetime(payload.get("ends_at"), "окончание лекции")
            if end <= now:
                raise ValueError("Время окончания должно быть в будущем.")
            lecture = Lecture(None, "Быстрое подключение", url, now, end, lead_minutes=0)
            with self._lock:
                scheduler = self._ensure_scheduler()
                if scheduler.active:
                    raise ValueError("Другая лекция уже запущена.")
                self._recording_active = True
                try:
                    self._sync_power()
                    lecture_id = scheduler.start_quick(lecture)
                    if lecture_id is None:
                        raise ValueError("Не удалось подключиться: другая лекция уже запущена или время истекло.")
                except Exception:
                    self._recording_active = False
                    self._sync_power()
                    raise
                return {"id": lecture_id}
        return self._reply(join)

    def set_scheduler(self, payload):
        def change():
            if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
                raise ValueError("Укажите состояние расписания.")
            with self._lock:
                scheduler = self._ensure_scheduler()
                if payload["enabled"]:
                    self._schedule_enabled = True
                    try:
                        self._sync_power()
                        scheduler.start()
                    except Exception:
                        self._schedule_enabled = False
                        self._sync_power()
                        raise
                    self._message = "Расписание активно · приложение должно оставаться открытым"
                else:
                    scheduler.stop()
                    self._schedule_enabled = False
                    self._sync_power()
                    self._message = ("Расписание на паузе · текущая лекция продолжается"
                                     if scheduler.active else "Расписание на паузе")
            return None
        return self._reply(change)

    def stop_active(self):
        def stop():
            with self._lock:
                if self._scheduler and self._scheduler.active:
                    self._scheduler.cancel_active()
                    self._message = "Завершаем запись и выходим из лекции…" if self._session_recording else "Выходим из лекции…"
            return None
        return self._reply(stop)

    def prepare_account(self, payload):
        def prepare():
            if not isinstance(payload, dict):
                raise ValueError("Укажите ссылку на Телемост.")
            url = validate_profile_url(payload.get("url", TELEMOST_HOME_URL))
            with self._lock:
                if self._closing:
                    raise ValueError("Приложение закрывается.")
                if self._account_active():
                    raise ValueError("Браузер для входа уже открыт.")
                if self._scheduler and (self._scheduler.running or self._scheduler.active):
                    raise ValueError("Приостановите расписание и завершите текущую лекцию перед входом в аккаунт.")
                self._account_cancel = threading.Event()
                self._message = "Войдите в Яндекс корпоративным аккаунтом, затем закройте браузер"
                def login():
                    try:
                        from integrations import prepare_browser_profile
                        prepare_browser_profile(url, cancel_event=self._account_cancel,
                                                status_callback=self._on_status)
                    except Exception as exc:
                        self._on_status(f"Вход в Яндекс: {str(exc) or type(exc).__name__}")
                self._account_thread = threading.Thread(target=login, name="account-login", daemon=True)
                self._account_thread.start()
                return None
        return self._reply(prepare)

    def _request_close(self, window):
        with self._lock:
            if self._close_allowed:
                return True
            if self._closing:
                return False
            self._closing = True
            self._alerts.close()
            self._schedule_enabled = False
            self._account_cancel.set()
            self._sync_power()
            scheduler = self._scheduler
            if scheduler:
                scheduler.stop()
                scheduler.cancel_active()
            if (not scheduler or not scheduler.active) and not self._account_active():
                self._close_allowed = True
                self._sleep.close()
                return True
            self._message = "Сохраняем запись и закрываем приложение…" if self._session_recording else "Выходим из лекции и закрываем приложение…"
        def cleanup():
            if scheduler:
                while not scheduler.wait_idle(1):
                    pass
            if self._account_thread:
                self._account_thread.join()
            with self._lock:
                self._close_allowed = True
                self._sleep.close()
            window.destroy()
        threading.Thread(target=cleanup, name="close-recording", daemon=True).start()
        return False


class _InstanceLock:
    """Hold the OS lock until the desktop window and its workers have exited."""

    def __init__(self, path: Path):
        self._path = path
        self._file = None
        self._activation_size = 0

    def __enter__(self):
        import os

        self._file = self._path.open("a+b")
        try:
            self._file.seek(0, 2)
            if self._file.tell() == 0:
                self._file.write(b"0")
                self._file.flush()
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Append beyond the locked first byte. The running instance watches
            # only file length, without executing input or opening a network port.
            try:
                self._file.write(b"+")
                self._file.flush()
            finally:
                self._file.close()
            raise _AlreadyRunning("Телекон уже открыт. Открываем существующее окно.") from None
        self._file.seek(0, 2)
        self._activation_size = self._file.tell()
        return self

    def consume(self):
        self._file.seek(0, 2)
        size = self._file.tell()
        changed = size != self._activation_size
        self._activation_size = size
        return changed

    def __exit__(self, *_):
        self._file.close()


class _AlreadyRunning(RuntimeError):
    pass


def ui_file() -> Path:
    """Nuitka data may live in MacOS or Resources inside a macOS bundle."""
    root = Path(__file__).resolve().parent
    candidates = [root / "ui" / "index.html", root.parent / "Resources" / "ui" / "index.html"]
    ui = next((path for path in candidates if path.is_file()), candidates[0])
    if not ui.is_file():
        raise RuntimeError("Не найдены файлы интерфейса ui/index.html. Проверьте установку приложения.")
    return ui


def main() -> None:
    import webview
    from ui_document import ui_window_content
    from tray import TrayManager

    ui = ui_file()
    content, http_server = ui_window_content(ui)
    try:
        with _InstanceLock(user_data_dir() / "desktop.lock") as instance:
            api = DesktopApi()
            window = webview.create_window("Телекон — календарь лекций", **content, js_api=api,
                                           width=1220, height=820, min_size=(820, 600),
                                           background_color="#E6F1FA", text_select=True)
            def tray_settings():
                with api._lock:
                    return {key: api._config[key] for key in TRAY_DEFAULTS}
            api._tray = TrayManager(window, tray_settings, api._request_close, activation=instance)
            api._tray.attach()
            api._alerts.backend.window = window
            window.events.closed += api._alerts.close
            webview.start(http_server=http_server, private_mode=True)
    except _AlreadyRunning:
        return


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--portable-self-check":
        from portable_diagnostics import run_portable_check
        raise SystemExit(run_portable_check(Path(sys.argv[2]).resolve()))
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        from diagnostics import run_self_check
        raise SystemExit(run_self_check(Path(sys.argv[2]).resolve()))
    main()
