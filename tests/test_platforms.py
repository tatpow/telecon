import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app import DesktopApi
from capabilities import desktop_capabilities
from storage import Lecture, LectureStore


class PlatformBehaviorTests(unittest.TestCase):
    def test_non_windows_join_does_not_request_windows_power_or_recording(self):
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                store = LectureStore(Path(temporary) / "calendar.sqlite3")
                store.save_settings({"recording_enabled": True, "prevent_sleep": True})
                sleep = Mock(active=False)
                sleep.set_active.side_effect = lambda enabled: self.assertFalse(enabled)
                runner = Mock(return_value=None)
                with patch("app.desktop_capabilities", return_value=desktop_capabilities(platform)):
                    api = DesktopApi(store, sleep_inhibitor=sleep, event_runner=runner)
                self.assertFalse(api.get_settings()["data"]["recording_enabled"])
                self.assertTrue(api.save_settings({"default_duration_minutes": 120})["ok"])
                start = datetime.now()
                lecture = Lecture(1, "Synthetic", "telemost.yandex.ru/j/test", start, start + timedelta(minutes=90))
                api._run(lecture, threading.Event())
                runner.assert_called_once()
                self.assertFalse(runner.call_args.args[1]["recording_enabled"])
                self.assertFalse(api._recording_active)
                result = api.save_settings({"recording_enabled": True})
                self.assertFalse(result["ok"])
                self.assertIn("Windows", result["error"])
                api._alerts.close()

    def test_windows_retains_recording_and_power_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = LectureStore(Path(temporary) / "calendar.sqlite3")
            with patch("app.desktop_capabilities", return_value=desktop_capabilities("win32")):
                api = DesktopApi(store, sleep_inhibitor=Mock(active=False))
            self.assertTrue(api.save_settings({"recording_enabled": True})["ok"])
            self.assertTrue(api.get_settings()["data"]["capabilities"]["recording"])
            api._alerts.close()
