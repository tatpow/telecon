from __future__ import annotations

import os
import json
import re
import sqlite3
import sys
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlsplit, urlunsplit
from recording_names import DEFAULT_RECORDING_TEMPLATE, RECORDING_FORMATS
from video_settings import VIDEO_DEFAULTS, validate_video_settings


APP_NAME = "LectureAssistant"
TELEMOST_HOME_URL = "https://telemost.yandex.ru/"
ALERT_DEFAULTS = {
    "alert_sound": True,
    "alert_notification": True,
    "alert_badge": True,
    "alert_volume": 50,
}
SETTINGS_DEFAULTS = {
    "ffmpeg_path": "",
    "recordings_dir": "",
    "display_name": "",
    "theme": "dark",
    "auto_start": False,
    "default_lead_minutes": 2,
    "default_duration_minutes": 90,
    "minimum_recording_minutes": 0,
    "recording_name_template": DEFAULT_RECORDING_TEMPLATE,
    "recording_format": "mkv",
    "prevent_sleep": True,
    **VIDEO_DEFAULTS,
    **ALERT_DEFAULTS,
}


def _normalize_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("Укажите ссылку на встречу в Телемосте.")
    url = url.strip()
    if not url or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise ValueError("Укажите корректную ссылку telemost.yandex.ru/j/…")
    # Only a missing scheme is supplied; explicit HTTP and foreign hosts
    # must still pass the strict validators below.
    return url if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url) else "https://" + url


def validate_telemost_url(url: str) -> str:
    """Validate the meeting URL before saving or opening it in the local browser."""
    url = _normalize_url(url)
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme.lower() == "https"
            and parsed.hostname is not None
            and parsed.hostname.lower() == "telemost.yandex.ru"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and re.fullmatch(r"/j/[A-Za-z0-9_-]+/?", parsed.path) is not None
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Поддерживаются только ссылки https://telemost.yandex.ru/j/…")
    return urlunsplit(("https", "telemost.yandex.ru", parsed.path.rstrip("/"), parsed.query, parsed.fragment))


def validate_profile_url(url: str) -> str:
    """The account setup page may be the homepage; lectures still need /j/ID."""
    url = _normalize_url(url)
    if isinstance(url, str):
        try:
            parsed = urlsplit(url.strip())
            if (not re.search(r"[\s\\\x00-\x1f\x7f]", url.strip())
                    and parsed.scheme.lower() == "https"
                    and parsed.hostname and parsed.hostname.lower() == "telemost.yandex.ru"
                    and parsed.port in (None, 443)
                    and parsed.username is None and parsed.password is None
                    and parsed.path in ("", "/", "/.") and not parsed.query and not parsed.fragment):
                return TELEMOST_HOME_URL
        except ValueError:
            pass
    return validate_telemost_url(url)


def _local_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("Укажите корректную дату и время лекции.")
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone().replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _validate_settings(settings: dict) -> dict:
    if not isinstance(settings, dict):
        raise ValueError("Настройки должны быть объектом.")
    if set(settings) - SETTINGS_DEFAULTS.keys():
        raise ValueError("Неизвестная настройка.")
    validated = dict(settings)
    validate_video_settings(settings, partial=True)
    for key, value in settings.items():
        if key in ("alert_sound", "alert_notification", "alert_badge") and type(value) is not bool:
            raise ValueError("Способы предупреждения должны быть включены или выключены.")
        if key == "alert_volume" and (type(value) is not int or not 0 <= value <= 100):
            raise ValueError("Громкость должна быть целым числом от 0 до 100.")
        if key in ("ffmpeg_path", "recordings_dir"):
            if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
                raise ValueError("Укажите корректный путь к файлу или папке.")
            validated[key] = value.strip()
        if key == "default_lead_minutes" and (type(value) is not int or not 0 <= value <= 120):
            raise ValueError("Опережение должно быть целым числом от 0 до 120 минут.")
        if key == "default_duration_minutes" and (type(value) is not int or not 1 <= value <= 1440):
            raise ValueError("Длительность пары должна быть целым числом от 1 до 1440 минут.")
        if key == "minimum_recording_minutes" and (type(value) is not int or not 0 <= value <= 1440):
            raise ValueError("Минимальная длительность записи — целое число от 0 до 1440 минут.")
        if key == "recording_name_template" and (not isinstance(value, str) or len(value) > 300):
            raise ValueError("Шаблон имени файла должен содержать не более 300 символов.")
        if key == "recording_format" and value not in RECORDING_FORMATS:
            raise ValueError("Выберите формат видео MKV или MP4.")
        if key == "auto_start" and type(value) is not bool:
            raise ValueError("Автозапуск должен быть логическим значением.")
        if key == "prevent_sleep" and type(value) is not bool:
            raise ValueError("Предотвращение сна должно быть логическим значением.")
        if key == "theme" and value not in ("dark", "light", "system"):
            raise ValueError("Неизвестная тема оформления.")
        if key == "display_name":
            if not isinstance(value, str) or len(value.strip()) > 100:
                raise ValueError("Имя участника должно содержать не более 100 символов.")
            validated[key] = value.strip()
    return validated


def user_data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Lecture:
    id: Optional[int]
    title: str
    url: str
    starts_at: datetime
    ends_at: datetime
    status: str = "scheduled"
    lead_minutes: int = 2


class LectureStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else user_data_dir() / "lectures.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lectures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    error TEXT,
                    lead_minutes INTEGER NOT NULL DEFAULT 2
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(lectures)")}
            if "lead_minutes" not in columns:
                connection.execute(
                    "ALTER TABLE lectures ADD COLUMN lead_minutes INTEGER NOT NULL DEFAULT 2"
                )
            if "error" not in columns:
                connection.execute("ALTER TABLE lectures ADD COLUMN error TEXT")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS recordings ("
                "lecture_id INTEGER PRIMARY KEY, path TEXT, status TEXT NOT NULL, "
                "duration_seconds REAL, note TEXT, file_dev TEXT, file_ino TEXT)"
            )
            row = connection.execute("SELECT value FROM settings WHERE name = 'recording_name_template'").fetchone()
            if row:
                try:
                    template = json.loads(row["value"])
                except (ValueError, TypeError):
                    template = None
                if isinstance(template, str) and "{длительность}" in template:
                    connection.execute("UPDATE settings SET value = ? WHERE name = 'recording_name_template'",
                                       (json.dumps(template.replace("{длительность}", "{duration}"), ensure_ascii=False),))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def add(self, lecture: Lecture) -> int:
        title, url, starts_at, ends_at = self._validated_values(lecture)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO lectures (title, url, starts_at, ends_at, lead_minutes) "
                "VALUES (?, ?, ?, ?, ?)",
                (title, url, starts_at, ends_at, lecture.lead_minutes),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _validated_values(lecture: Lecture) -> tuple[str, str, str, str]:
        if not isinstance(lecture.title, str) or not lecture.title.strip():
            raise ValueError("Укажите название лекции.")
        url = validate_telemost_url(lecture.url)
        start = _local_datetime(lecture.starts_at).replace(microsecond=0)
        end = _local_datetime(lecture.ends_at).replace(microsecond=0)
        if end <= start:
            raise ValueError("Время окончания должно быть позже времени начала.")
        if type(lecture.lead_minutes) is not int or not 0 <= lecture.lead_minutes <= 120:
            raise ValueError("Опережение должно быть целым числом от 0 до 120 минут.")
        return lecture.title.strip(), url, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")

    def update(self, lecture: Lecture) -> bool:
        values = self._validated_values(lecture)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE lectures SET title = ?, url = ?, starts_at = ?, ends_at = ?, "
                "lead_minutes = ?, error = NULL WHERE id = ? AND status = 'scheduled'",
                (*values, lecture.lead_minutes, lecture.id),
            )
            return cursor.rowcount == 1

    def get(self, lecture_id: int) -> Optional[Lecture]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
        return self._to_lecture(row) if row else None

    def list_all(self) -> list[Lecture]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, title, url, starts_at, ends_at, status, lead_minutes FROM lectures "
                "ORDER BY datetime(starts_at), id"
            ).fetchall()
        return [self._to_lecture(row) for row in rows]

    def delete(self, lecture_id: int, *, delete_recording: bool = False) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
            if row is None or row["status"] == "running":
                return False
            if delete_recording:
                recording = connection.execute("SELECT * FROM recordings WHERE lecture_id = ?", (lecture_id,)).fetchone()
                if recording is not None and recording["path"]:
                    self._delete_recording_file(recording)
            connection.execute("DELETE FROM recordings WHERE lecture_id = ?", (lecture_id,))
            cursor = connection.execute(
                "DELETE FROM lectures WHERE id = ? AND status != 'running'", (lecture_id,)
            )
            return cursor.rowcount == 1

    @staticmethod
    def _delete_recording_file(recording) -> None:
        path = Path(recording["path"])
        source = Path(__file__).resolve().parent
        if not path.is_absolute() or path.suffix.lower().lstrip(".") not in RECORDING_FORMATS or path.resolve() != path or path.is_relative_to(source):
            raise ValueError("Путь файла записи изменился. Удаление отменено.")
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                or (hex(info.st_dev), hex(info.st_ino)) != (recording["file_dev"], recording["file_ino"])):
            raise ValueError("Файл записи заменён или больше не является обычным файлом. Удаление отменено.")
        path.unlink()

    def save_recording(self, lecture_id: int, result: dict) -> None:
        path = result.get("path")
        device = inode = None
        if path:
            path = Path(path).absolute()
            if (path.resolve() != path or path.suffix.lower().lstrip(".") not in RECORDING_FORMATS
                    or path.is_relative_to(Path(__file__).resolve().parent) or path.is_symlink()):
                raise ValueError("Некорректный путь файла записи.")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Файл записи не является обычным файлом.")
            # Windows can expose 128-bit file IDs, wider than SQLite INTEGER.
            device, inode = hex(info.st_dev), hex(info.st_ino)
            path = str(path)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO recordings (lecture_id, path, status, duration_seconds, note, file_dev, file_ino) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(lecture_id) DO UPDATE SET "
                "path=excluded.path, status=excluded.status, duration_seconds=excluded.duration_seconds, "
                "note=excluded.note, file_dev=excluded.file_dev, file_ino=excluded.file_ino",
                (lecture_id, path, result["status"], result.get("duration_seconds"), result.get("note"), device, inode),
            )

    def recording_for(self, lecture_id: int) -> Optional[dict]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM recordings WHERE lecture_id = ?", (lecture_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def claim_due(self, now: datetime) -> Optional[Lecture]:
        return self._claim(now)

    def claim(self, lecture_id: int, now: datetime) -> Optional[Lecture]:
        """Claim a scheduled lecture for an explicit immediate connection."""
        return self._claim(now, lecture_id)

    def _claim(self, now: datetime, lecture_id: Optional[int] = None) -> Optional[Lecture]:
        now_text = _local_datetime(now).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE lectures SET status = 'missed', error = 'Время лекции прошло' "
                "WHERE status = 'scheduled' AND datetime(ends_at) <= datetime(?)",
                (now_text,),
            )
            if connection.execute("SELECT 1 FROM lectures WHERE status = 'running' LIMIT 1").fetchone():
                return None
            if lecture_id is None:
                row = connection.execute(
                    "SELECT * FROM lectures WHERE status = 'scheduled' AND datetime(ends_at) > datetime(?) "
                    "AND datetime(starts_at, printf('-%d minutes', lead_minutes)) <= datetime(?) "
                    "ORDER BY datetime(starts_at), id LIMIT 1",
                    (now_text, now_text),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM lectures WHERE id = ? AND status = 'scheduled' "
                    "AND datetime(ends_at) > datetime(?)",
                    (lecture_id, now_text),
                ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE lectures SET status = 'running', error = NULL WHERE id = ?",
                (row["id"],),
            )
            return self._to_lecture(
                connection.execute("SELECT * FROM lectures WHERE id = ?", (row["id"],)).fetchone()
            )

    def finish(self, lecture_id: int, error: Optional[str] = None, *, cancelled: bool = False) -> None:
        status = "cancelled" if cancelled else "failed" if error else "done"
        if cancelled and error is None:
            error = "Остановлена пользователем"
        with self._connect() as connection:
            connection.execute(
                "UPDATE lectures SET status = ?, error = ? WHERE id = ?",
                (status, error, lecture_id),
            )

    def get_settings(self) -> dict:
        settings = dict(SETTINGS_DEFAULTS)
        with self._connect() as connection:
            rows = connection.execute("SELECT name, value FROM settings").fetchall()
        for row in rows:
            try:
                settings.update(_validate_settings({row["name"]: json.loads(row["value"])}))
            except (ValueError, TypeError):
                continue
        return settings

    def save_settings(self, settings: dict) -> dict:
        validated = _validate_settings(settings)
        validate_video_settings({**self.get_settings(), **validated})
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO settings (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                [(name, json.dumps(value, ensure_ascii=False)) for name, value in validated.items()],
            )
        return self.get_settings()

    def recover_interrupted(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE lectures SET status = 'failed', error = ? WHERE status = 'running'",
                ("Приложение было закрыто во время лекции; проверьте файл записи.",),
            )

    def error_for(self, lecture_id: int) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT error FROM lectures WHERE id = ?", (lecture_id,)
            ).fetchone()
        return row["error"] if row else None

    @staticmethod
    def _to_lecture(row: sqlite3.Row) -> Lecture:
        return Lecture(
            id=row["id"],
            title=row["title"],
            url=row["url"],
            starts_at=_local_datetime(datetime.fromisoformat(row["starts_at"])),
            ends_at=_local_datetime(datetime.fromisoformat(row["ends_at"])),
            status=row["status"],
            lead_minutes=row["lead_minutes"],
        )
