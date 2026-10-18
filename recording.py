"""FFmpeg window recording with WASAPI audio from our browser process tree."""
from __future__ import annotations

import ctypes
import atexit
import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from recording_names import DEFAULT_RECORDING_TEMPLATE, RECORDING_FORMATS, recording_stem


SOURCE_DIR = Path(__file__).resolve().parent


class _CaptureRuntime:
    """Keep COM's MTA alive between sequential recording worker threads."""
    def __init__(self):
        self._lock = threading.Lock()
        self._ole = None
        self._cookie = None

    def ensure(self):
        with self._lock:
            if self._cookie is not None:
                return
            ole = ctypes.WinDLL("ole32")
            ole.CoIncrementMTAUsage.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            ole.CoIncrementMTAUsage.restype = ctypes.c_long
            ole.CoDecrementMTAUsage.argtypes = [ctypes.c_void_p]
            ole.CoDecrementMTAUsage.restype = ctypes.c_long
            cookie = ctypes.c_void_p()
            result = ole.CoIncrementMTAUsage(ctypes.byref(cookie))
            if result < 0 or not cookie.value:
                raise RuntimeError(f"Не удалось подготовить захват окна: COM {result:#x}")
            self._ole, self._cookie = ole, cookie
            atexit.register(self.close)

    def close(self):
        with self._lock:
            if self._cookie is not None:
                cookie, self._cookie = self._cookie, None
                self._ole.CoDecrementMTAUsage(cookie)


_capture_runtime = _CaptureRuntime()


class RecordingCancelled(RuntimeError):
    cancelled = True


def recordings_directory(value: str = "") -> Path:
    destination = (Path(value).expanduser() if value else Path.home() / "Videos" / "Telecon").resolve()
    if destination.is_relative_to(SOURCE_DIR):
        raise ValueError("Выберите папку записей вне исходного каталога приложения.")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def ffmpeg_executable(value: str = "") -> str:
    if value:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError("Не найден указанный ffmpeg.exe.")
        return str(path)
    bundled = Path(sys.executable).resolve().parent / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run_command(arguments: list[str], timeout: int = 15):
    return subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def check_recorder(settings: dict | None = None) -> dict:
    settings = settings or {}
    if sys.platform != "win32" or sys.getwindowsversion().build < 20348:
        raise RuntimeError("Захват окна и звука процесса требует Windows 11 или Windows build 20348+.")
    import psutil  # noqa: F401 - report missing dependencies before joining
    from proctap import ProcessAudioCapture  # noqa: F401
    from proctap._native import ProcessLoopback  # noqa: F401
    from windows_capture import WindowsCapture  # noqa: F401
    executable = ffmpeg_executable(settings.get("ffmpeg_path", ""))
    version = _run_command([executable, "-version"])
    formats = _run_command([executable, "-hide_banner", "-formats"])
    encoders = _run_command([executable, "-hide_banner", "-encoders"])
    if any(result.returncode for result in (version, formats, encoders)):
        raise RuntimeError("FFmpeg не прошёл проверку запуска.")
    if not all(name in formats.stdout for name in ("rawvideo", "f32le")):
        raise RuntimeError("FFmpeg не поддерживает входные потоки видео и звука.")
    if not all(name in encoders.stdout for name in ("libx264", " aac ")):
        raise RuntimeError("FFmpeg должен поддерживать кодирование libx264 и AAC.")
    directory = recordings_directory(settings.get("recordings_dir", ""))
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b"telecon directory check")
    return {"ffmpeg_path": executable, "version": version.stdout.splitlines()[0],
            "recordings_dir": str(directory), "capture": "Окно Телемоста и звук его процесса"}


@dataclass(frozen=True)
class BrowserTarget:
    pid: int
    hwnd: int


def browser_target(profile: Path) -> BrowserTarget:
    """Find only the Chromium root using this app's exact persistent profile."""
    import psutil
    expected = os.path.normcase(str(profile.resolve()))
    candidates = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            args = process.info["cmdline"] or []
            if (process.info["name"] or "").lower() not in ("chrome.exe", "chromium.exe"):
                continue
            if any(argument.startswith("--type=") for argument in args):
                continue
            profiles = [argument.split("=", 1)[1] for argument in args if argument.startswith("--user-data-dir=")]
            if any(os.path.normcase(str(Path(item).resolve())) == expected for item in profiles):
                candidates.append(process.info["pid"])
        except (psutil.Error, OSError):
            continue
    if len(candidates) != 1:
        raise RuntimeError("Не удалось однозначно определить процесс профиля Телемоста.")
    pid = candidates[0]
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            class_name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(wintypes.HWND(hwnd), class_name, len(class_name))
            if class_name.value == "Chrome_WidgetWin_1":
                windows.append(int(hwnd))
        return True

    callback = callback_type(visit)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    if not user.EnumWindows(callback, 0) or len(windows) != 1:
        raise RuntimeError("Для записи оставьте одно окно выделенного браузера Телемоста.")
    target = BrowserTarget(pid, windows[0])
    check_target(target)
    return target


def check_target(target: BrowserTarget) -> None:
    user = ctypes.WinDLL("user32", use_last_error=True)
    for name in ("IsWindow", "IsWindowVisible"):
        getattr(user, name).argtypes = [wintypes.HWND]
    if not user.IsWindow(target.hwnd) or not user.IsWindowVisible(target.hwnd):
        raise RuntimeError("Окно Телемоста закрыто; запись прервана.")
    owner = wintypes.DWORD()
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId(target.hwnd, ctypes.byref(owner))
    if owner.value != target.pid:
        raise RuntimeError("Окно больше не принадлежит процессу Телемоста.")
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    desktop = user.OpenInputDesktop(0, False, 1)
    if not desktop:
        raise RuntimeError("Рабочий стол недоступен для записи. Разблокируйте Windows.")
    try:
        name, required = ctypes.create_unicode_buffer(256), wintypes.DWORD()
        if not user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(required)):
            raise RuntimeError("Не удалось проверить доступность рабочего стола.")
        if name.value.lower() != "default":
            raise RuntimeError("Windows заблокирована или открыт защищённый рабочий стол; запись прервана.")
    finally:
        user.CloseDesktop(desktop)


def target_minimized(target: BrowserTarget) -> bool:
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsIconic.argtypes = [wintypes.HWND]
    return bool(user.IsIconic(target.hwnd))


def restore_target(target: BrowserTarget) -> None:
    """Restore only the verified dedicated browser window at the user's request."""
    check_target(target)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.ShowWindow(target.hwnd, 9)  # SW_RESTORE.
    user.SetForegroundWindow(target.hwnd)
    if target_minimized(target):
        raise RuntimeError("Windows не восстановила окно Телемоста. Откройте его на панели задач.")


def recording_command(executable: str, video_url: str, output: Path) -> list[str]:
    return [executable, "-hide_banner", "-loglevel", "warning", "-n", "-nostats",
            "-progress", "pipe:1", "-stats_period", "0.25",
            "-thread_queue_size", "1024", "-probesize", "32", "-analyzeduration", "0",
            "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", "pipe:0",
            "-thread_queue_size", "16", "-f", "rawvideo", "-pixel_format", "bgra",
            "-video_size", "1920x1080", "-framerate", "15", "-probesize", "32",
            "-analyzeduration", "0", "-i", video_url,
            "-map", "1:v:0", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-shortest", "-shortest_buf_duration", "1", str(output)]


class FFmpegRecorder:
    def __init__(self, target: BrowserTarget, settings: dict, lecture_id: int, *, lecture=None):
        self.target = target
        self.executable = ffmpeg_executable(settings.get("ffmpeg_path", ""))
        directory = recordings_directory(settings.get("recordings_dir", ""))
        token = uuid.uuid4().hex
        self._final_output = None
        self.output = directory / f".recording-{token}.partial.mkv"
        self.recording_format = settings.get("recording_format", "mkv")
        if self.recording_format not in RECORDING_FORMATS:
            raise ValueError("Выберите формат видео MKV или MP4.")
        self.name_template = settings.get("recording_name_template", DEFAULT_RECORDING_TEMPLATE)
        self._title = lecture.title if lecture is not None else "Лекция"
        self._url = lecture.url if lecture is not None else "https://telemost.yandex.ru/j/meeting"
        self._started_at = datetime.now()
        self.minimum_minutes = settings.get("minimum_recording_minutes", 0)
        self.duration_seconds = None
        self.recording_status = "partial"
        self.recording_note = None
        self._finalized = False
        self._result_path = None
        self.process = None
        self.audio = None
        self._capture = None
        self._video_control = None
        self._video_server = None
        self._video_connection = None
        self._video_frame = None
        self._black_frame = bytes(1920 * 1080 * 4)
        self._first_frame = threading.Event()
        self._queue = queue.Queue(maxsize=1024)
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._threads = []
        self._error = None
        self._log = deque(maxlen=30)
        self._last_audio = time.monotonic()
        self._last_frame = time.monotonic()
        self._frame_count = 0
        self._audio_bytes = 0

    def _start_video(self, cancel_event):
        _capture_runtime.ensure()
        from windows_capture import WindowsCapture
        import cv2
        import numpy as np
        self._capture = WindowsCapture(cursor_capture=False, draw_border=True,
                                       secondary_window=False, minimum_update_interval=66,
                                       window_hwnd=self.target.hwnd)
        def on_frame_arrived(frame, control):
            if self._stopping.is_set():
                control.stop()
                return
            try:
                ratio = min(1920 / frame.width, 1080 / frame.height)
                width, height = max(1, round(frame.width*ratio)), max(1, round(frame.height*ratio))
                resized = cv2.resize(frame.frame_buffer, (width, height), interpolation=cv2.INTER_AREA)
                image = np.zeros((1080,1920,4), dtype=np.uint8)
                x, y = (1920-width)//2, (1080-height)//2
                image[y:y+height,x:x+width] = resized
                self._video_frame = image.tobytes()
                self._first_frame.set()
            except Exception as exc:
                self._error = RuntimeError(f"Получение кадров Телемоста прервано: {exc}")
                control.stop()
        def on_closed():
            if not self._stopping.is_set():
                self._error = RuntimeError("Захват окна Телемоста завершился неожиданно.")
        self._capture.frame_handler = on_frame_arrived
        self._capture.closed_handler = on_closed
        self._video_control = self._capture.start_free_threaded()
        deadline = time.monotonic()+8
        while not self._first_frame.wait(.1):
            if cancel_event is not None and cancel_event.is_set():
                raise RecordingCancelled("Запуск записи отменён пользователем.")
            if self._error:
                raise self._error
            if target_minimized(self.target) and not self._video_control.is_finished():
                # WGC may suspend frames while minimized, including at startup.
                # The video writer supplies black; process audio stays live.
                break
            if time.monotonic()>deadline or self._video_control.is_finished():
                raise RuntimeError("Windows Graphics Capture не передал изображение окна Телемоста.")

    def _output_frame(self):
        if target_minimized(self.target):
            # Do not reuse the last slide when the window is restored before
            # WGC has delivered its first fresh frame.
            self._video_frame = None
            return self._black_frame
        return self._video_frame or self._black_frame

    def _write_video(self):
        try:
            while not self._stopping.is_set():
                try:
                    connection, _ = self._video_server.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            self._video_connection = connection
            connection.settimeout(3)
            deadline = time.monotonic()
            while not self._stopping.is_set():
                connection.sendall(self._output_frame())
                deadline += 1/15
                self._stopping.wait(max(0, deadline-time.monotonic()))
        except OSError as exc:
            if not self._stopping.is_set():
                self._error = RuntimeError(f"Передача изображения в FFmpeg прервана: {exc}")
        finally:
            if self._video_connection is not None:
                self._video_connection.close()

    def _on_audio(self, data, _frames):
        if self._stopping.is_set() or not data:
            return
        try:
            self._queue.put_nowait(data)
            self._last_audio = time.monotonic()
            self._audio_bytes += len(data)
        except queue.Full:
            self._error = RuntimeError("FFmpeg не успевает принимать звук; запись остановлена.")

    def _write_audio(self):
        try:
            while not self._stopping.is_set() or not self._queue.empty():
                try:
                    data = self._queue.get(timeout=.1)
                except queue.Empty:
                    continue
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            if not self._stopping.is_set():
                self._error = RuntimeError(f"Передача звука в FFmpeg прервана: {exc}")
        finally:
            try:
                self.process.stdin.close()
            except OSError:
                pass

    def _read_progress(self):
        for line in self.process.stdout:
            key, _, value = line.decode("utf-8", "replace").strip().partition("=")
            if key == "frame" and value.strip().isdigit() and int(value) > self._frame_count:
                self._frame_count = int(value)
                self._last_frame = time.monotonic()
                self._ready.set()

    def _read_log(self):
        for line in self.process.stderr:
            self._log.append(line.decode("utf-8", "replace").strip())

    def start(self, cancel_event: threading.Event | None = None):
        from proctap import ProcessAudioCapture
        check_target(self.target)
        try:
            self._start_video(cancel_event)
            self.audio = ProcessAudioCapture(pid=self.target.pid, on_data=self._on_audio)
            self._video_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._video_server.bind(("127.0.0.1", 0))
            self._video_server.listen(1)
            self._video_server.settimeout(.2)
            video_url = f"tcp://127.0.0.1:{self._video_server.getsockname()[1]}"
            self._started_at = datetime.now()
            self.process = subprocess.Popen(recording_command(self.executable, video_url, self.output),
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for function in (self._write_audio, self._write_video, self._read_progress, self._read_log):
                thread = threading.Thread(target=function, daemon=True, name=f"ffmpeg-{function.__name__}")
                self._threads.append(thread)
                thread.start()
            self.audio.start()
            self._last_audio = self._last_frame = time.monotonic()
            deadline = time.monotonic() + 10
            while not self._ready.wait(.1):
                if cancel_event is not None and cancel_event.is_set():
                    raise RecordingCancelled("Запуск записи отменён пользователем.")
                self.check()
                if time.monotonic() > deadline:
                    raise RuntimeError("FFmpeg не подтвердил начало записи видео и звука.")
            self.check()
            if not self._audio_bytes:
                raise RuntimeError("Захват звука процесса не передал данные в FFmpeg.")
        except Exception:
            self.stop(validate=False)
            raise

    def check(self):
        if self._error:
            raise self._error
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("FFmpeg завершился во время записи: " + "\n".join(self._log))
        if self._video_control is not None and self._video_control.is_finished():
            raise RuntimeError("Захват окна Телемоста остановился.")
        if self.audio is not None and not self.audio.is_running:
            raise RuntimeError("Захват звука процесса Телемоста остановился.")
        if time.monotonic() - self._last_audio > 5:
            raise RuntimeError("Захват звука Телемоста не передаёт данные более 5 секунд.")
        if time.monotonic() - self._last_frame > 10:
            raise RuntimeError("FFmpeg не передаёт кадры видео более 10 секунд.")
        check_target(self.target)

    def result(self) -> dict:
        return {"path": str(self.output) if self.output.is_file() else None,
                "status": self.recording_status, "duration_seconds": self.duration_seconds,
                "note": self.recording_note}

    def _prepare_mp4(self):
        original = self.output
        converted = original.with_suffix(".mp4")
        if converted.exists() or converted.is_symlink():
            raise RuntimeError(f"Временный файл MP4 уже существует: {converted}. Исходная запись: {original}")
        try:
            result = _run_command([self.executable, "-hide_banner", "-loglevel", "error", "-n",
                                   "-i", str(original), "-map", "0:v:0", "-map", "0:a:0",
                                   "-c", "copy", "-movflags", "+faststart", str(converted)], timeout=900)
            if result.returncode or not converted.is_file() or converted.stat().st_size == 0:
                raise RuntimeError(result.stderr[-2000:] or "итоговый файл отсутствует или пуст")
            check = _run_command([self.executable, "-hide_banner", "-i", str(converted),
                                  "-map", "0:v:0", "-map", "0:a:0", "-t", "0.1", "-f", "null", "-"])
            if check.returncode:
                raise RuntimeError(check.stderr[-2000:])
        except Exception as exc:
            converted.unlink(missing_ok=True)
            raise RuntimeError(f"Не удалось сохранить MP4: {exc}. Исходная запись сохранена: {original}") from exc
        self.output = converted
        original.unlink()

    def stop(self, *, validate: bool = True) -> str | None:
        if self._finalized:
            return self._result_path
        ended_at = datetime.now()
        self._stopping.set()
        errors = []
        video_stopped = self._video_control is None
        if self._video_control is not None:
            try:
                self._video_control.stop()
                video_stopped = True
            except Exception as exc:
                errors.append(f"остановка захвата окна: {exc}")
            self._video_control = None
        if self._capture is not None and video_stopped:
            # Release the native capture and bound callbacks on the thread that
            # created them, rather than in a later GC cycle on another worker.
            self._capture.frame_handler = None
            self._capture.closed_handler = None
            self._capture.capture = None
            self._capture = None
        if self.audio is not None:
            try:
                self.audio.close()
            except Exception as exc:
                errors.append(f"остановка звука: {exc}")
            self.audio = None
        if self.process is not None:
            try:
                # EOF on the raw audio pipe + -shortest flushes the MKV cleanly.
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
                errors.append("FFmpeg не завершился штатно; файл может быть неполным")
            if validate and self.process.returncode != 0:
                errors.append("FFmpeg завершился с ошибкой: " + "\n".join(self._log))
            for thread in self._threads:
                thread.join(timeout=2)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            self.process = None
        if self._video_server is not None:
            self._video_server.close()
            self._video_server = None
        if validate:
            if not self.output.is_file() or self.output.stat().st_size == 0:
                errors.append("файл записи отсутствует или пуст")
            elif not errors:
                result = _run_command([self.executable, "-hide_banner", "-i", str(self.output),
                                       "-map", "0:v:0", "-map", "0:a:0", "-t", "0.1", "-f", "null", "-"])
                if result.returncode:
                    errors.append("не подтверждены читаемые видео- и аудиодорожки: " + result.stderr[-2000:])
                elif isinstance(result.stderr, str):
                    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
                    if duration:
                        hours, minutes, seconds = map(float, duration.groups())
                        self.duration_seconds = hours * 3600 + minutes * 60 + seconds
                if self.minimum_minutes and self.duration_seconds is None:
                    errors.append("не удалось определить фактическую длительность записи")
        if errors:
            raise RuntimeError("; ".join(errors) + f". Файл: {self.output}")
        if validate:
            if self.minimum_minutes and self.duration_seconds < self.minimum_minutes * 60:
                self.output.unlink()
                self.recording_status = "discarded"
                self.recording_note = f"Видео не сохранено: запись короче {self.minimum_minutes} мин."
                self._finalized = True
                return None
            if self.recording_format == "mp4":
                self._prepare_mp4()
            rendered = recording_stem(self.name_template, name=self._title, url=self._url,
                                      started_at=self._started_at, ended_at=ended_at,
                                      duration_seconds=self.duration_seconds)
            number = 1
            while True:
                suffix = f"-{number}" if number > 1 else ""
                self._final_output = self.output.parent / f"{rendered['stem']}{suffix}.{self.recording_format}"
                try:
                    if os.name == "nt":
                        self.output.rename(self._final_output)
                    else:
                        os.link(self.output, self._final_output)
                        self.output.unlink()
                    break
                except FileExistsError:
                    number += 1
            self.output = self._final_output
            self.recording_status = "saved"
            self._finalized = True
        self._result_path = str(self.output)
        return self._result_path
