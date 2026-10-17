"""Optional hidden-meeting alerts, independent of webview JavaScript timers."""
from __future__ import annotations

import ctypes
import io
import logging
import math
import struct
import sys
import threading
import uuid
import wave
from ctypes import wintypes


ALERT_TITLE = "Окно Телемоста свёрнуто"
ALERT_MESSAGE = "Откройте окно Телемоста, чтобы вернуть изображение. Сейчас записываются чёрное видео и звук встречи."


def alert_wave(volume: int) -> bytes:
    """Scale our PCM samples, never the Windows or meeting audio volume."""
    rate = 22050
    samples = []
    for frequency in (660, 880):
        count = round(rate * .16)
        for index in range(count):
            fade = min(1, index / (rate * .01), (count - 1 - index) / (rate * .01))
            samples.append(round(12000 * volume / 100 * fade * math.sin(2 * math.pi * frequency * index / rate)))
    output = io.BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(rate)
        sound.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


class _Guid(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _NotifyIconData(ctypes.Structure):
    # NOTIFYICONDATAW, including the timeout/version union's shared UINT.
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", _Guid),
                ("hBalloonIcon", wintypes.HICON)]


class WindowsAlerts:
    def __init__(self):
        self.window = None
        self._tray_hwnd = None
        self._sound_lock = threading.Lock()
        self.audio_error = ""

    def _hwnd(self):
        if sys.platform != "win32":
            raise RuntimeError("Системные уведомления поддерживаются только на Windows.")
        if self.window is None or self.window.native is None:
            raise RuntimeError("Окно Телекона ещё не готово для уведомлений.")
        return self.window.native.Handle.ToInt64()

    @staticmethod
    def _icon():
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        user.LoadIconW.restype = wintypes.HICON
        icon = user.LoadIconW(None, ctypes.c_void_p(32515))  # Shared IDI_WARNING.
        if not icon:
            raise ctypes.WinError(ctypes.get_last_error())
        return icon

    @staticmethod
    def _shell(operation, data):
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIconData)]
        shell.Shell_NotifyIconW.restype = wintypes.BOOL
        if not shell.Shell_NotifyIconW(operation, ctypes.byref(data)):
            raise RuntimeError("Windows не приняла уведомление Телемоста.")

    def notification(self):
        self.clear_notification()
        data = _NotifyIconData()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd, data.uID = self._hwnd(), 1
        data.uFlags = 0x02 | 0x04 | 0x10 | 0x40  # ICON, TIP, INFO, REALTIME.
        data.hIcon = self._icon()
        data.szTip = ALERT_TITLE
        data.szInfoTitle, data.szInfo = ALERT_TITLE, ALERT_MESSAGE
        # The sound channel is controlled separately; never use the default beep.
        data.dwInfoFlags = 0x02 | 0x10 | 0x80  # WARNING, NOSOUND, RESPECT_QUIET_TIME.
        self._shell(0, data)  # NIM_ADD.
        self._tray_hwnd = data.hWnd

    def clear_notification(self):
        if self._tray_hwnd is not None:
            data = _NotifyIconData()
            data.cbSize = ctypes.sizeof(data)
            data.hWnd, data.uID = self._tray_hwnd, 1
            self._shell(2, data)  # NIM_DELETE also removes the balloon.
            self._tray_hwnd = None

    def badge(self, enabled):
        hwnd = self._hwnd()
        ole = ctypes.WinDLL("ole32")
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole.CoInitializeEx.restype = ctypes.c_long
        initialized = ole.CoInitializeEx(None, 2)
        # A thread already initialized in another apartment can still use COM.
        if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE.
            raise OSError(f"CoInitializeEx: {initialized:#x}")
        pointer = ctypes.c_void_p()
        try:
            ole.CoCreateInstance.argtypes = [ctypes.POINTER(_Guid), ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(_Guid),
                                            ctypes.POINTER(ctypes.c_void_p)]
            ole.CoCreateInstance.restype = ctypes.c_long
            clsid = _Guid.parse("56FDF344-FD6D-11D0-958A-006097C9A090")
            iid = _Guid.parse("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF")
            result = ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer))
            if result < 0:
                raise OSError(f"ITaskbarList3: {result:#x}")
            table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            def call(index, types, *args):
                function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
                result = function(pointer, *args)
                if result < 0:
                    raise OSError(f"Taskbar method {index}: {result:#x}")
            call(3, [])  # HrInit.
            call(18, [wintypes.HWND, wintypes.HICON, wintypes.LPCWSTR],
                 hwnd, self._icon() if enabled else None, ALERT_TITLE if enabled else "")
        finally:
            if pointer.value:
                table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(pointer)  # Release.
            if initialized >= 0:
                ole.CoUninitialize()

    def sound(self, volume):
        if not volume:
            return
        # SND_MEMORY cannot be asynchronous; play on our own short-lived thread.
        def play():
            if not self._sound_lock.acquire(blocking=False):
                return
            try:
                import winsound
                winsound.PlaySound(alert_wave(volume), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                self.audio_error = ""
            except Exception as exc:
                self.audio_error = f"Звук предупреждения: {exc}"
                logging.getLogger(__name__).exception("Alert sound failed")
            finally:
                self._sound_lock.release()
        threading.Thread(target=play, name="hidden-window-sound", daemon=True).start()


class HiddenWindowAlerts:
    """One alert per hidden interval; live settings never stop the recording."""
    def __init__(self, settings, backend=None):
        self.backend = backend if backend is not None else WindowsAlerts()
        self._lock = threading.RLock()
        self._hidden = False
        self._applied = {name: False for name in ("sound", "notification", "badge")}
        self._settings = dict(settings)
        self._error = ""

    def configure(self, settings):
        with self._lock:
            self._settings = dict(settings)
            self._apply()

    def update(self, hidden):
        with self._lock:
            if self._hidden != hidden:
                self._error = ""
            self._hidden = hidden
            self._apply()

    def _apply(self):
        for channel in self._applied:
            desired = self._hidden and self._settings[f"alert_{channel}"]
            if channel == "sound":
                desired = desired and self._settings["alert_volume"] > 0
            if desired == self._applied[channel]:
                continue
            self._applied[channel] = desired
            try:
                if channel == "badge":
                    self.backend.badge(desired)
                elif channel == "notification":
                    self.backend.notification() if desired else self.backend.clear_notification()
                elif desired:
                    self.backend.sound(self._settings["alert_volume"])
            except Exception as exc:
                self._error = f"Не удалось показать предупреждение ({channel}): {exc}"
                logging.getLogger(__name__).exception("Hidden meeting alert failed")

    def state(self):
        with self._lock:
            return {"meeting_hidden": self._hidden,
                    "error": self._error or getattr(self.backend, "audio_error", "")}

    def preview(self, volume):
        if type(volume) is not int or not 0 <= volume <= 100:
            raise ValueError("Громкость должна быть целым числом от 0 до 100.")
        self.backend.sound(volume)

    def close(self):
        self.update(False)
