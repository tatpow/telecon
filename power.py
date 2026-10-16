"""Temporary Windows power requests; no power-plan or lock-screen changes."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class SleepInhibitor:
    def __init__(self):
        self._handle = None
        self._kernel = None

    @property
    def active(self) -> bool:
        return self._handle is not None

    def set_active(self, enabled: bool) -> None:
        if enabled == self.active:
            return
        if not enabled:
            handle, self._handle = self._handle, None
            try:
                self._kernel.PowerClearRequest(handle, 0)
                self._kernel.PowerClearRequest(handle, 1)
            finally:
                self._kernel.CloseHandle(handle)
            return
        if os.name != "nt":
            raise RuntimeError("Предотвращение сна пока поддерживается только на Windows.")

        class DetailedReason(ctypes.Structure):
            _fields_ = [("module", wintypes.HMODULE), ("reason_id", wintypes.ULONG),
                        ("count", wintypes.ULONG), ("strings", ctypes.POINTER(wintypes.LPWSTR))]

        class ReasonUnion(ctypes.Union):
            _fields_ = [("simple", wintypes.LPWSTR), ("detailed", DetailedReason)]

        class ReasonContext(ctypes.Structure):
            _fields_ = [("version", wintypes.ULONG), ("flags", wintypes.DWORD),
                        ("reason", ReasonUnion)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.PowerCreateRequest.argtypes = [ctypes.POINTER(ReasonContext)]
        kernel.PowerCreateRequest.restype = wintypes.HANDLE
        for name in ("PowerSetRequest", "PowerClearRequest"):
            function = getattr(kernel, name)
            function.argtypes = [wintypes.HANDLE, ctypes.c_int]
            function.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        reason = ReasonContext(0, 1, ReasonUnion(simple="Телекон: активное расписание или запись лекции"))
        handle = kernel.PowerCreateRequest(ctypes.byref(reason))
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # SystemRequired prevents idle sleep; DisplayRequired keeps capture
            # usable. Neither request disables automatic/manual session locking.
            if not kernel.PowerSetRequest(handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.PowerSetRequest(handle, 0):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            kernel.PowerClearRequest(handle, 1)
            kernel.CloseHandle(handle)
            raise
        self._kernel, self._handle = kernel, handle

    def close(self) -> None:
        self.set_active(False)
