"""Window lifetime and native tray integration, independent of meeting capture.

Native objects and timers are created by pywebview's synchronous before_show
event and disposed by its synchronous closing event, on the owning GUI thread.
Only immutable snapshots and queued commands cross into bridge/event workers.
"""
from __future__ import annotations

import queue
import sys
import threading


TRAY_DEFAULTS = {"minimize_to_tray": False, "close_to_tray": False}


def request_window_close(window):
    """Use the native close request, including Cocoa's cancellable delegate.

    pywebview 6.2.1's Cocoa destroy() calls NSWindow.close(), which bypasses
    windowShouldClose_ and therefore our graceful session cleanup. Marshal
    performClose_ to the main loop even when tray initialization has failed.
    """
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper
        AppHelper.callAfter(window.native.performClose_, None)
    else:
        window.destroy()


class TrayManager:
    def __init__(self, window, settings, request_close, *, backend_factory=None, activation=None):
        self.window = window
        self._settings = settings
        self._request_close = request_close
        self._factory = backend_factory or native_backend
        self._activation = activation
        self._backend = None
        self._commands = queue.SimpleQueue()
        self._lock = threading.RLock()
        self._available = False
        self._ready = False
        self._window_visible = True
        self._menu = []
        self._hidden = False
        self._quitting = False
        self._stopped = False
        self._error = ""

    def attach(self):
        self.window.events.before_show += self.start
        self.window.events.minimized += self.on_minimized
        self.window.events.closing += self.on_closing
        self.window.events.closed += self.on_closed

    def state(self):
        with self._lock:
            return {"initialized": self._ready, "available": self._available,
                    "hidden": self._hidden, "window_visible": self._window_visible,
                    "menu": list(self._menu), "error": self._error}

    def start(self):
        try:
            self._backend = self._factory(self.window, self.restore, self.exit)
            self._menu = self._backend.menu_labels()
            self._backend.start(self.tick)
            self._ready = True
            self.tick()
        except Exception as error:
            self._fail(error)

    def _fail(self, error):
        with self._lock:
            self._error = f"Трей недоступен: {error}"
            self._available = False
        if self._hidden:
            self._restore()

    def restore(self):
        self._commands.put("restore")

    def invoke_menu(self, index):
        """Queue a native menu action on its owning GUI thread (also used by CI)."""
        if type(index) is not int or index not in (0, 1):
            raise ValueError("Неизвестный пункт меню трея.")
        self._commands.put(("menu", index))

    def exit(self):
        with self._lock:
            if self._quitting:
                return
            self._quitting = True
        # This must also work without an initialized backend/native tray timer.
        request_window_close(self.window)

    def on_minimized(self):
        self._commands.put("minimize")

    def _restore(self):
        if self._backend:
            self._backend.restore_window()
        else:
            self.window.restore()
            self.window.show()
        with self._lock:
            self._hidden = False

    def _hide(self):
        if self._quitting or not self._available or self._backend is None:
            return False
        # Recheck on the GUI thread immediately before hiding. A Linux tray
        # host may disappear between ticks; keep a route back to the window.
        if not self._backend.available():
            self._available = False
            return False
        self._backend.set_visible(True)
        self.window.hide()
        with self._lock:
            self._hidden = True
        return True

    def tick(self):
        if self._stopped:
            return
        try:
            settings = self._settings()
            enabled = any(settings.get(key, False) for key in TRAY_DEFAULTS)
            if self._backend:
                available = bool(self._backend.available())
                with self._lock:
                    self._available = available
                if self._hidden and (not enabled or not available):
                    self._restore()
                self._backend.set_visible(enabled)
                self._window_visible = bool(self._backend.window_visible())
            if self._activation and self._activation.consume():
                self._restore()
            while not self._commands.empty():
                command = self._commands.get_nowait()
                if self._quitting:
                    continue
                if command == "restore":
                    self._restore()
                elif isinstance(command, tuple) and command[0] == "menu":
                    self._backend.invoke_menu(command[1])
                elif (command == "minimize" and settings.get("minimize_to_tray")
                      and self._backend and self._backend.minimized()):
                    self._hide()
        except Exception as error:
            self._fail(error)

    def on_closing(self):
        if not self._quitting and self._settings().get("close_to_tray"):
            try:
                if not self._hide():
                    # Do not unexpectedly stop a lecture or hide an inaccessible
                    # window when the tray host is absent or initialization fails.
                    self.window.minimize()
            except Exception as error:
                self._fail(error)
            return False
        self._quitting = True
        allowed = self._request_close(self.window)
        if allowed:
            self.stop()
        return allowed

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        if self._backend:
            self._backend.stop()
        with self._lock:
            self._available = False

    def on_closed(self):
        self._stopped = True


def native_backend(window, restore, exit):
    if sys.platform == "win32":
        from tray_windows import WindowsTray
        return WindowsTray(window, restore, exit)
    if sys.platform == "darwin":
        from tray_cocoa import CocoaTray
        return CocoaTray(window, restore, exit)
    if sys.platform == "linux":
        from tray_qt import QtTray
        return QtTray(window, restore, exit)
    raise RuntimeError("Эта платформа пока не поддерживается.")


def icon_path(extension="png"):
    # Resolve alongside the same UI document used by the actual app, including
    # Nuitka's macOS Resources layout. Never look in the working directory.
    from app import ui_file
    return ui_file().parent / f"tray.{extension}"
