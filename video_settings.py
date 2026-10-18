"""Validated recording controls and physical pixels of the selected monitor."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

VIDEO_DEFAULTS = {"recording_quality": "balanced", "recording_fps": 15,
                  "recording_width": 0, "recording_height": 0}
QUALITY_CRF = {"maximum": 18, "balanced": 23, "compact": 28, "economy": 32}


def validate_video_settings(settings: dict, *, partial=False) -> None:
    quality = settings.get("recording_quality", "balanced")
    if not isinstance(quality, str) or quality not in QUALITY_CRF:
        raise ValueError("Выберите качество записи из списка.")
    fps = settings.get("recording_fps", 15)
    if type(fps) is not int or not 1 <= fps <= 60:
        raise ValueError("FPS — целое число от 1 до 60.")
    width, height = (settings.get(key, 0) for key in ("recording_width", "recording_height"))
    for value in (width, height):
        if type(value) is not int or (value != 0 and not (64 <= value <= 16384 and value % 2 == 0)):
            raise ValueError("Ширина и высота — чётные целые числа от 64 до 16384 пикселей.")
    if (not partial or {"recording_width", "recording_height"} <= settings.keys()) and (width == 0) != (height == 0):
        raise ValueError("Для автоматического разрешения обе стороны должны быть равны 0.")


def monitor_size(hwnd: int = 0) -> tuple[int, int]:
    if sys.platform != "win32":
        raise RuntimeError("Определение экрана для записи поддерживается только на Windows.")
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT),
                    ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]
    class DisplayMode(ctypes.Structure):
        _fields_ = [("device", wintypes.WCHAR * 32), ("spec", wintypes.WORD),
                    ("driver", wintypes.WORD), ("size", wintypes.WORD), ("extra", wintypes.WORD),
                    ("fields", wintypes.DWORD), ("display", ctypes.c_byte * 16),
                    ("color", ctypes.c_short), ("duplex", ctypes.c_short), ("yres", ctypes.c_short),
                    ("tt", ctypes.c_short), ("collate", ctypes.c_short), ("form", wintypes.WCHAR * 32),
                    ("logpixels", wintypes.WORD), ("bits", wintypes.DWORD),
                    ("width", wintypes.DWORD), ("height", wintypes.DWORD),
                    ("remaining", wintypes.DWORD * 10)]
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.MonitorFromWindow.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DisplayMode)]
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    mode = DisplayMode(size=ctypes.sizeof(DisplayMode))
    monitor = user.MonitorFromWindow(hwnd, 2)
    if (not monitor or not user.GetMonitorInfoW(monitor, ctypes.byref(info))
            or not user.EnumDisplaySettingsW(info.device, 0xFFFFFFFF, ctypes.byref(mode))
            or mode.width < 64 or mode.height < 64):
        raise RuntimeError("Не удалось определить разрешение монитора. Проверьте подключение экрана.")
    return mode.width, mode.height


def resolve_video_settings(settings: dict, screen: tuple[int, int]) -> dict:
    validate_video_settings(settings)
    screen_width, screen_height = screen
    width, height = settings.get("recording_width", 0), settings.get("recording_height", 0)
    if width == 0:
        width, height = 1920, 1080
    ratio = min(1, screen_width / width, screen_height / height)
    actual_width = max(2, int(width * ratio) // 2 * 2)
    actual_height = max(2, int(height * ratio) // 2 * 2)
    return {"width": actual_width, "height": actual_height,
            "fps": settings.get("recording_fps", 15),
            "crf": QUALITY_CRF[settings.get("recording_quality", "balanced")],
            "limited": ratio < 1}
