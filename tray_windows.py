"""WinForms NotifyIcon, using pywebview's existing CLR and GUI event loop."""


def fit_to_work_area(bounds, area):
    """Keep the restored title bar/window on a currently connected monitor."""
    x, y, width, height = bounds
    left, top, available_width, available_height = area
    width = max(1, min(width, available_width))
    height = max(1, min(height, available_height))
    return (min(max(x, left), left + available_width - width),
            min(max(y, top), top + available_height - height), width, height)


class WindowsTray:
    def __init__(self, window, restore, exit):
        from System.Drawing import Icon
        from System.Windows.Forms import ContextMenuStrip, NotifyIcon, Timer
        from tray import icon_path
        self.window = window
        self._disposed = False
        self._restoring = False
        self._restore_bounds = window.native.Bounds
        self._restore_state = window.native.WindowState
        self._geometry_handler = self._remember_geometry
        form = window.native
        form.Resize += self._geometry_handler
        form.Move += self._geometry_handler
        self.icon = Icon(str(icon_path("ico")))
        self.menu = ContextMenuStrip()
        # Nuitka 2.8 re-evaluates a call used as an augmented-assignment
        # attribute target. Keep creation separate: otherwise Add(...).Click +=
        # creates two visible items, one of which has no working handler.
        open_item = self.menu.Items.Add("Открыть Телекон")
        exit_item = self.menu.Items.Add("Выйти")
        open_item.Click += lambda *_: restore()
        exit_item.Click += lambda *_: exit()
        self.tray = NotifyIcon()
        self.tray.Icon = self.icon
        self.tray.Text = "Телекон"
        self.tray.ContextMenuStrip = self.menu
        self.tray.DoubleClick += lambda *_: restore()
        self.timer = Timer()
        self.timer.Interval = 200

    def start(self, tick):
        self.timer.Tick += lambda *_: tick()
        self.timer.Start()

    def available(self):
        import ctypes
        # A running WinForms loop does not imply Explorer has a tray host
        # (notably on CI/server sessions). Never hide without that host.
        return not self._disposed and bool(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None))

    def minimized(self):
        from System.Windows.Forms import FormWindowState
        return self.window.native.WindowState == FormWindowState.Minimized

    def window_visible(self):
        import ctypes
        from ctypes import wintypes
        from System.Drawing import Rectangle
        from System.Windows.Forms import FormWindowState, Screen
        form = self.window.native
        # Visible is also true for an iconic/minimized Form. It is not enough
        # to prove that restoring from the tray actually brought the window back.
        if not form.Visible or form.WindowState == FormWindowState.Minimized or form.Opacity <= 0:
            return False
        user = ctypes.windll.user32
        handle = wintypes.HWND(form.Handle.ToInt64())
        rect = wintypes.RECT()
        if (not user.IsWindowVisible(handle) or user.IsIconic(handle)
                or not user.GetWindowRect(handle, ctypes.byref(rect))):
            return False
        actual = Rectangle(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        return any(screen.Bounds.IntersectsWith(actual) for screen in Screen.AllScreens)

    def _remember_geometry(self, *_):
        from System.Windows.Forms import FormWindowState, Screen
        form = self.window.native
        if self._restoring or form.WindowState == FormWindowState.Minimized:
            return
        bounds = form.RestoreBounds if form.WindowState == FormWindowState.Maximized else form.Bounds
        if bounds.Width > 0 and bounds.Height > 0 and any(
                screen.WorkingArea.IntersectsWith(bounds) for screen in Screen.AllScreens):
            self._restore_bounds = bounds
            self._restore_state = form.WindowState

    def restore_window(self):
        from System.Drawing import Rectangle
        from System.Windows.Forms import FormWindowState, Screen
        form = self.window.native
        saved, state = self._restore_bounds, self._restore_state
        area = Screen.FromRectangle(saved).WorkingArea
        bounds = fit_to_work_area((saved.X, saved.Y, saved.Width, saved.Height),
                                  (area.X, area.Y, area.Width, area.Height))
        self._restoring = True
        try:
            # Showing a hidden, iconic WinForms Form can reapply its cached
            # minimized placement. Normalize AFTER Show and explicitly restore
            # real bounds; Normal before Show can leave it at (-32000, -32000).
            form.Show()
            form.WindowState = FormWindowState.Normal
            form.Bounds = Rectangle(*bounds)
            form.Opacity = 1
            if state == FormWindowState.Maximized:
                form.WindowState = FormWindowState.Maximized
            form.BringToFront()
            form.Activate()
        finally:
            self._restoring = False
        self._remember_geometry()

    def menu_labels(self):
        return [str(item.Text) for item in self.menu.Items]

    def invoke_menu(self, index):
        self.menu.Items[index].PerformClick()

    def set_visible(self, enabled):
        self.tray.Visible = bool(enabled)

    def stop(self):
        self._disposed = True
        form = self.window.native
        form.Resize -= self._geometry_handler
        form.Move -= self._geometry_handler
        self.timer.Stop()
        self.timer.Dispose()
        self.tray.Visible = False
        self.tray.Dispose()
        self.menu.Dispose()
        self.icon.Dispose()
