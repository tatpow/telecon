import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from app import DesktopApi, _InstanceLock
from storage import LectureStore
from tray import TrayManager, TRAY_DEFAULTS, request_window_close


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(TRAY_DEFAULTS)
        self.window = Mock()
        self.backend = Mock()
        self.backend.available.return_value = True
        self.backend.minimized.return_value = True
        self.backend.window_visible.return_value = True
        self.backend.menu_labels.return_value = ["Открыть Телекон", "Выйти"]
        self.backend.restore_window.side_effect = lambda: (self.window.restore(), self.window.show())
        self.close = Mock(return_value=True)
        self.manager = TrayManager(self.window, lambda: dict(self.settings), self.close,
                                   backend_factory=lambda *_: self.backend)
        self.manager.start()

    def test_close_to_tray_preserves_scheduler_account_and_power_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(LectureStore(Path(directory) / "calendar.sqlite3"), sleep_inhibitor=Mock())
            api._scheduler = Mock(active=True)
            api._account_cancel.clear()
            api._schedule_enabled = True
            self.manager._request_close = api._request_close
            self.settings["close_to_tray"] = True
            self.assertFalse(self.manager.on_closing())
            self.window.hide.assert_called_once()
            self.assertFalse(api._closing)
            self.assertTrue(api._schedule_enabled)
            self.assertFalse(api._account_cancel.is_set())
            api._scheduler.stop.assert_not_called()
            api._scheduler.cancel_active.assert_not_called()
            api._sleep.close.assert_not_called()

    def test_minimize_is_opt_in_and_does_not_hide_a_restored_window(self):
        self.manager.on_minimized()
        self.manager.tick()
        self.window.hide.assert_not_called()
        self.settings["minimize_to_tray"] = True
        self.backend.minimized.return_value = False
        self.manager.on_minimized()
        self.manager.tick()
        self.window.hide.assert_not_called()
        self.backend.minimized.return_value = True
        self.manager.on_minimized()
        self.manager.tick()
        self.window.hide.assert_called_once()

    def test_explicit_exit_uses_graceful_cleanup_even_when_close_hides(self):
        self.settings["close_to_tray"] = True
        self.close.return_value = False  # Active recording still finalizes.
        self.window.destroy.side_effect = self.manager.on_closing
        with patch("tray.request_window_close", side_effect=lambda window: window.destroy()):
            self.manager.exit()
        self.close.assert_called_once_with(self.window)
        self.backend.stop.assert_not_called()
        self.close.return_value = True
        self.assertTrue(self.manager.on_closing())
        self.backend.stop.assert_called_once()
        self.window.hide.assert_not_called()

    def test_normal_close_still_exits_with_both_switches_off(self):
        self.assertTrue(self.manager.on_closing())
        self.close.assert_called_once_with(self.window)
        self.backend.stop.assert_called_once()

    def test_no_tray_host_keeps_window_in_taskbar_and_session_alive(self):
        self.backend.available.return_value = False
        self.manager.tick()
        self.settings.update(minimize_to_tray=True, close_to_tray=True)
        self.manager.on_minimized()
        self.manager.tick()
        self.assertFalse(self.manager.on_closing())
        self.window.minimize.assert_called_once()
        self.window.hide.assert_not_called()
        self.close.assert_not_called()

    def test_rechecks_host_immediately_before_hiding(self):
        self.settings["close_to_tray"] = True
        self.backend.available.return_value = False
        self.assertFalse(self.manager.on_closing())
        self.window.hide.assert_not_called()

    def test_host_disappears_or_last_switch_disabled_restores_hidden_window(self):
        for reason in ("host", "settings"):
            with self.subTest(reason=reason):
                self.settings["close_to_tray"] = True
                self.backend.available.return_value = True
                self.manager.tick()
                self.manager.on_closing()
                if reason == "host":
                    self.backend.available.return_value = False
                else:
                    self.settings["close_to_tray"] = False
                self.manager.tick()
                self.assertFalse(self.manager.state()["hidden"])
                self.window.show.assert_called()

    def test_open_menu_restores_without_changing_session(self):
        self.settings["close_to_tray"] = True
        self.manager.on_closing()
        self.manager.restore()
        self.manager.tick()
        self.window.restore.assert_called_once()
        self.window.show.assert_called_once()
        self.close.assert_not_called()

    def test_native_menu_is_queued_then_dispatches_actual_item_handler(self):
        self.backend.invoke_menu.side_effect = lambda index: self.manager.restore()
        self.manager.invoke_menu(0)
        self.backend.invoke_menu.assert_not_called()
        self.manager.tick()
        self.backend.invoke_menu.assert_called_once_with(0)
        self.window.show.assert_called_once()
        self.assertEqual(self.manager.state()["menu"], ["Открыть Телекон", "Выйти"])
        for index in (True, -1, 2, "0"):
            with self.assertRaises(ValueError):
                self.manager.invoke_menu(index)

    def test_initialization_failure_remains_visible_and_can_exit(self):
        manager = TrayManager(self.window, lambda: self.settings, self.close,
                              backend_factory=Mock(side_effect=RuntimeError("missing icon")))
        manager.start()
        self.assertFalse(manager.state()["initialized"])
        self.assertIn("missing icon", manager.state()["error"])
        with patch("tray.request_window_close") as close_window:
            manager.exit()
        close_window.assert_called_once_with(self.window)

    def test_cocoa_exit_uses_main_loop_close_delegate_and_waits_for_cleanup(self):
        self.settings["close_to_tray"] = True
        self.close.return_value = False
        self.window.native.performClose_.side_effect = lambda _: self.manager.on_closing()
        helper = SimpleNamespace(callAfter=Mock())
        with patch("tray.sys.platform", "darwin"), patch.dict("sys.modules", {
                "PyObjCTools": SimpleNamespace(AppHelper=helper)}):
            self.manager.exit()
            helper.callAfter.assert_called_once_with(self.window.native.performClose_, None)
            self.close.assert_not_called()  # Still on the requesting thread.
            callback, sender = helper.callAfter.call_args.args
            self.assertFalse(callback(sender))  # The native delegate cancels closing during cleanup.
            self.backend.stop.assert_not_called()
            self.close.return_value = True
            request_window_close(self.window)  # Cleanup requests the final close through the same delegate.
            callback, sender = helper.callAfter.call_args.args
            self.assertTrue(callback(sender))
        self.backend.stop.assert_called_once()
        self.window.destroy.assert_not_called()
        self.window.hide.assert_not_called()

    def test_second_instance_requests_restore_without_network_or_new_scheduler(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desktop.lock"
            with _InstanceLock(path) as owner:
                self.assertFalse(owner.consume())
                with self.assertRaises(RuntimeError):
                    with _InstanceLock(path):
                        self.fail("Second instance acquired the lock")
                self.assertTrue(owner.consume())
                self.assertFalse(owner.consume())
                self.manager._activation = Mock(consume=Mock(return_value=True))
                self.manager.tick()
                self.window.show.assert_called()
            with _InstanceLock(path) as owner:
                self.assertFalse(owner.consume())

    def test_tray_preferences_persist_and_only_accept_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calendar.sqlite3"
            store = LectureStore(path)
            self.assertFalse(store.get_settings()["close_to_tray"])
            self.assertFalse(store.get_settings()["minimize_to_tray"])
            store.save_settings(dict(minimize_to_tray=True, close_to_tray=True))
            self.assertTrue(LectureStore(path).get_settings()["close_to_tray"])
            for value in (1, "true", None):
                with self.assertRaises(ValueError):
                    store.save_settings({"close_to_tray": value})

    def test_restore_bounds_recover_iconic_coordinates_and_disconnected_monitor(self):
        from tray_windows import fit_to_work_area
        self.assertEqual(fit_to_work_area((-32000, -32000, 1024, 760), (0, 0, 1920, 1040)),
                         (0, 0, 1024, 760))
        self.assertEqual(fit_to_work_area((2400, 200, 1220, 820), (0, 0, 1920, 1040)),
                         (700, 200, 1220, 820))
        self.assertEqual(fit_to_work_area((-1600, 100, 1024, 760), (-1920, 0, 1920, 1040)),
                         (-1600, 100, 1024, 760))
