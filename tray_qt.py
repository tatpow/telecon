"""Linux tray on the same Qt application that owns pywebview's window."""


class QtTray:
    def __init__(self, window, restore, exit):
        from qtpy.QtCore import QTimer
        from qtpy.QtGui import QIcon
        from qtpy.QtWidgets import QMenu, QSystemTrayIcon
        from tray import icon_path
        self.window = window
        self.menu = QMenu(window.native)
        self.menu.addAction("Открыть Телекон").triggered.connect(lambda *_: restore())
        self.menu.addAction("Выйти").triggered.connect(lambda *_: exit())
        icon = QIcon(str(icon_path()))
        if icon.isNull():
            raise RuntimeError("Не найдена иконка Телекона.")
        self.tray = QSystemTrayIcon(icon, window.native)
        self.tray.setToolTip("Телекон")
        self.tray.setContextMenu(self.menu)
        def activated(reason):
            if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                          QSystemTrayIcon.ActivationReason.DoubleClick):
                restore()
        self.tray.activated.connect(activated)
        self.timer = QTimer(window.native)
        self.timer.setInterval(200)

    def start(self, tick):
        self.timer.timeout.connect(tick)
        self.timer.start()

    def available(self):
        return self.tray.isSystemTrayAvailable()

    def minimized(self):
        return self.window.native.isMinimized()

    def window_visible(self):
        return self.window.native.isVisible() and not self.window.native.isMinimized()

    def menu_labels(self):
        return [action.text() for action in self.menu.actions()]

    def restore_window(self):
        self.window.restore()
        self.window.show()

    def invoke_menu(self, index):
        self.menu.actions()[index].trigger()

    def set_visible(self, enabled):
        self.tray.setVisible(bool(enabled))

    def stop(self):
        self.timer.stop()
        self.tray.hide()
        self.tray.deleteLater()
        self.menu.deleteLater()
