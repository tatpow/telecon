import unittest
from datetime import date, datetime, timedelta

from app import (
    _date_separator_label,
    _default_lecture_start,
    _group_lectures_by_date,
)
from storage import Lecture


class LectureDialogDefaultsTests(unittest.TestCase):
    def test_default_start_is_five_minutes_ahead_and_rolls_over_day(self):
        self.assertEqual(
            _default_lecture_start(datetime(2030, 5, 10, 12, 34, 56)),
            datetime(2030, 5, 10, 12, 39),
        )
        self.assertEqual(
            _default_lecture_start(datetime(2030, 5, 10, 23, 57)),
            datetime(2030, 5, 11, 0, 2),
        )

    def test_lecture_list_groups_dates_in_chronological_order(self):
        lectures = [
            Lecture(2, "Поздняя", "https://telemost.yandex.ru/j/2",
                    datetime(2030, 5, 11, 12), datetime(2030, 5, 11, 13)),
            Lecture(3, "Вторая", "https://telemost.yandex.ru/j/3",
                    datetime(2030, 5, 10, 11), datetime(2030, 5, 10, 12)),
            Lecture(1, "Первая", "https://telemost.yandex.ru/j/1",
                    datetime(2030, 5, 10, 9), datetime(2030, 5, 10, 10)),
        ]
        groups = _group_lectures_by_date(lectures)
        self.assertEqual([day for day, _ in groups], [date(2030, 5, 10), date(2030, 5, 11)])
        self.assertEqual(
            [lecture.title for lecture in groups[0][1]], ["Первая", "Вторая"]
        )

    def test_date_separator_highlights_today_and_tomorrow(self):
        today = date(2030, 5, 10)
        self.assertEqual(
            _date_separator_label(today, today),
            "СЕГОДНЯ  ·  пятница, 10 мая 2030",
        )
        self.assertEqual(
            _date_separator_label(today + timedelta(days=1), today),
            "ЗАВТРА  ·  суббота, 11 мая 2030",
        )



class FakeSleep:
    def __init__(self):
        self.active = False
        self.requests = []
    def set_active(self, value):
        self.active = value
        self.requests.append(value)
    def close(self):
        self.set_active(False)


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from capabilities import desktop_capabilities
        capabilities = patch('app.desktop_capabilities', return_value=desktop_capabilities('win32'))
        capabilities.start()
        self.addCleanup(capabilities.stop)
        import tempfile
        from pathlib import Path
        from app import DesktopApi
        from storage import LectureStore
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LectureStore(Path(self.temp.name) / "lectures.sqlite3")
        self.sleep = FakeSleep()
        self.api = DesktopApi(self.store, sleep_inhibitor=self.sleep)
        self.payload = {
            "title": "Лекция", "url": "https://telemost.yandex.ru/j/42574758760781",
            "starts_at": "2035-10-16T12:00", "ends_at": "2035-10-16T13:30", "lead_minutes": 2,
        }

    def test_bridge_crud_sorts_earliest_first_and_reports_missing_edits(self):
        later = self.api.save_lecture(dict(self.payload, title="Поздняя", starts_at="2035-10-16T14:00", ends_at="2035-10-16T15:00"))
        first = self.api.save_lecture(self.payload)
        self.assertTrue(later["ok"])
        self.assertTrue(first["ok"])
        lectures = self.api.get_state()["data"]["lectures"]
        self.assertEqual([lecture["title"] for lecture in lectures], ["Лекция", "Поздняя"])
        lecture_id = first["data"]["id"]
        self.assertTrue(self.api.save_lecture(dict(self.payload, id=lecture_id, title="Изменена"))["ok"])
        self.assertEqual(self.store.get(lecture_id).title, "Изменена")
        self.assertTrue(self.api.delete_lecture(lecture_id)["ok"])
        self.assertFalse(self.api.save_lecture(dict(self.payload, id=lecture_id))["ok"])
        self.assertFalse(self.api.delete_lecture(lecture_id)["ok"])

    def test_invalid_bridge_inputs_do_not_persist(self):
        malformed = [
            None, {}, dict(self.payload, url="https://example.org/j/1"),
            dict(self.payload, ends_at="2035-10-16T11:00"),
            dict(self.payload, starts_at="2035-10-16T12:00+03:00"),
            dict(self.payload, lead_minutes=True), dict(self.payload, lead_minutes=121),
            dict(self.payload, id=True), dict(self.payload, title=4),
        ]
        for payload in malformed:
            with self.subTest(payload=payload):
                self.assertFalse(self.api.save_lecture(payload)["ok"])
        self.assertEqual(self.store.list_all(), [])
        for value in (True, -1, "1", None):
            self.assertFalse(self.api.delete_lecture(value)["ok"])
        self.assertFalse(self.api.set_scheduler({"enabled": "yes"})["ok"])

    def test_recording_settings_persist_and_old_obs_keys_are_rejected(self):
        from app import DesktopApi
        captured = []
        def checker(settings):
            captured.append(settings)
            return {"version": "ffmpeg test"}
        api = DesktopApi(self.store, recording_checker=checker, sleep_inhibitor=FakeSleep())
        config = {"recordings_dir": str(self.store.db_path.parent), "prevent_sleep": False}
        self.assertTrue(api.save_settings(config)["ok"])
        self.assertTrue(api.check_recording()["ok"])
        self.assertEqual(captured[0]["recordings_dir"], config["recordings_dir"])
        restored = DesktopApi(self.store, sleep_inhibitor=FakeSleep()).get_settings()["data"]
        self.assertEqual(restored["recordings_dir"], config["recordings_dir"])
        self.assertFalse(restored["prevent_sleep"])
        for payload in ({"obs_password": "secret"}, {"obs_port": 4455}, {"prevent_sleep": "yes"}, {"recordings_dir": 12}):
            self.assertFalse(api.save_settings(payload)["ok"])
        source = __import__("pathlib").Path(__file__).resolve().parents[1]
        self.assertFalse(api.save_settings({"recordings_dir": str(source / "recordings")})["ok"])

    def test_sleep_follows_schedule_and_setting_changes(self):
        self.assertFalse(self.sleep.active)
        self.assertTrue(self.api.set_scheduler({"enabled": True})["ok"])
        self.assertTrue(self.sleep.active)
        self.assertTrue(self.api.save_settings({"prevent_sleep": False})["ok"])
        self.assertFalse(self.sleep.active)
        self.assertTrue(self.api.save_settings({"prevent_sleep": True})["ok"])
        self.assertTrue(self.sleep.active)
        self.assertTrue(self.api.set_scheduler({"enabled": False})["ok"])
        self.assertFalse(self.sleep.active)

    def test_sleep_request_failure_does_not_start_schedule(self):
        from unittest.mock import Mock
        self.sleep.set_active = Mock(side_effect=RuntimeError("power request failed"))
        self.assertFalse(self.api.set_scheduler({"enabled": True})["ok"])
        self.assertFalse(self.api._scheduler.running)

    def test_running_lecture_cannot_be_edited_or_deleted(self):
        lecture_id = self.api.save_lecture(self.payload)["data"]["id"]
        self.store.claim(lecture_id, datetime(2035, 10, 16, 12))
        self.assertFalse(self.api.save_lecture(dict(self.payload, id=lecture_id))["ok"])
        self.assertFalse(self.api.delete_lecture(lecture_id)["ok"])
        self.assertEqual(self.store.get(lecture_id).status, "running")

    def test_default_duration_is_exposed_and_survives_bridge_restart(self):
        from app import DesktopApi
        self.assertEqual(self.api.get_settings()["data"]["default_duration_minutes"], 90)
        self.assertTrue(self.api.save_settings({"default_duration_minutes": 120})["ok"])
        self.assertFalse(self.api.save_settings({"default_duration_minutes": True})["ok"])
        restored = DesktopApi(self.store, sleep_inhibitor=FakeSleep())
        self.assertEqual(restored.get_settings()["data"]["default_duration_minutes"], 120)

    def test_alert_preferences_persist_and_bad_types_are_rejected(self):
        from app import DesktopApi
        config = {"alert_sound": False, "alert_notification": True, "alert_badge": False, "alert_volume": 17}
        self.assertTrue(self.api.save_settings(config)["ok"])
        restored = DesktopApi(self.store, sleep_inhibitor=FakeSleep()).get_settings()["data"]
        for key, value in config.items():
            self.assertEqual(restored[key], value)
        for invalid in ({"alert_sound": "false"}, {"alert_notification": 1}, {"alert_badge": None},
                        {"alert_volume": True}, {"alert_volume": 101}, {"alert_volume": -1}, {"alert_volume": 12.5}):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.api.save_settings(invalid)["ok"])

    def test_visibility_bridge_updates_alerts_and_closing_clears_them(self):
        from app import DesktopApi
        from unittest.mock import Mock
        backend = Mock(audio_error="")
        api = DesktopApi(self.store, sleep_inhibitor=self.sleep, alert_backend=backend)
        api._on_visibility(True, Mock())
        self.assertTrue(api.get_state()["data"]["alerts"]["meeting_hidden"])
        backend.sound.assert_called_once_with(50)
        self.assertTrue(api.save_settings({"alert_badge": False})["ok"])
        backend.badge.assert_called_with(False)
        self.assertTrue(api._request_close(Mock()))
        self.assertFalse(api.get_state()["data"]["alerts"]["meeting_hidden"])
        backend.clear_notification.assert_called_once()

    def test_sound_preview_uses_requested_volume_without_saving(self):
        from app import DesktopApi
        from unittest.mock import Mock
        backend = Mock(audio_error="")
        api = DesktopApi(self.store, sleep_inhibitor=self.sleep, alert_backend=backend)
        self.assertTrue(api.preview_alert_sound({"volume": 8})["ok"])
        backend.sound.assert_called_once_with(8)
        self.assertEqual(api.get_settings()["data"]["alert_volume"], 50)
        self.assertFalse(api.preview_alert_sound({"volume": True})["ok"])
        self.assertFalse(api.preview_alert_sound(None)["ok"])

    def test_restore_meeting_rejects_missing_target_and_uses_exact_target(self):
        from unittest.mock import Mock, patch
        self.assertFalse(self.api.restore_meeting()["ok"])
        target = Mock()
        self.api._meeting_target = target
        with patch("recording.restore_target") as restore:
            self.assertTrue(self.api.restore_meeting()["ok"])
        restore.assert_called_once_with(target)

    def test_quick_join_creates_and_runs_lecture_with_schedule_paused(self):
        import threading
        from app import DesktopApi
        started = threading.Event()
        captured = []
        def runner(lecture, config, *, cancel_event, status_callback):
            captured.append(lecture)
            started.set()
            cancel_event.wait(3)
        api = DesktopApi(self.store, event_runner=runner, sleep_inhibitor=self.sleep)
        end = (datetime.now() + timedelta(minutes=20)).replace(microsecond=0)
        try:
            result = api.quick_join({"url": "telemost.yandex.ru/j/abc", "ends_at": end.isoformat()})
            self.assertTrue(result["ok"], result)
            self.assertTrue(started.wait(2))
            self.assertFalse(api.get_state()["data"]["scheduler"]["running"])
            self.assertTrue(self.sleep.active)
            lecture = captured[0]
            self.assertEqual(lecture.url, "https://telemost.yandex.ru/j/abc")
            self.assertEqual(lecture.ends_at, end)
            self.assertLessEqual(lecture.starts_at, datetime.now())
            self.assertEqual(lecture.lead_minutes, 0)
            self.assertEqual(lecture.id, result["data"]["id"])
            self.assertFalse(api.quick_join({"url": lecture.url, "ends_at": end.isoformat()})["ok"])
            self.assertEqual(len(self.store.list_all()), 1)
        finally:
            api.stop_active()
            self.assertTrue(api._scheduler.wait_idle(3))
        self.assertEqual(self.store.get(lecture.id).status, "cancelled")
        self.assertFalse(self.sleep.active)

    def test_invalid_quick_join_does_not_create_scheduled_lectures(self):
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        for payload in (None, {}, {"url": "example.org", "ends_at": future},
                        {"url": "telemost.yandex.ru/j/1", "ends_at": "2000-01-01T10:00"},
                        {"url": "telemost.yandex.ru/j/1", "ends_at": future + "+03:00"},
                        {"url": "telemost.yandex.ru/j/1", "ends_at": True}):
            with self.subTest(payload=payload):
                self.assertFalse(self.api.quick_join(payload)["ok"])
        self.assertEqual(self.store.list_all(), [])
        self.assertFalse(self.sleep.active)

    def test_quick_join_power_failure_does_not_create_a_lecture(self):
        from unittest.mock import Mock
        self.sleep.set_active = Mock(side_effect=RuntimeError("power request failed"))
        result = self.api.quick_join({"url": "telemost.yandex.ru/j/1",
                                     "ends_at": (datetime.now() + timedelta(minutes=5)).isoformat()})
        self.assertFalse(result["ok"])
        self.assertEqual(self.store.list_all(), [])

    def test_manual_join_uses_settings_and_cancel_cleans_up_before_close(self):
        import threading
        from app import DesktopApi
        from unittest.mock import Mock, patch
        close_dispatch = patch("app.request_window_close", side_effect=lambda window: window.destroy())
        close_dispatch.start()
        self.addCleanup(close_dispatch.stop)
        started, released = threading.Event(), threading.Event()
        captured = []
        def runner(lecture, config, *, cancel_event, status_callback):
            captured.append(config)
            status_callback("Тестовая запись")
            started.set()
            cancel_event.wait(3)
            released.wait(3)
        api = DesktopApi(self.store, event_runner=runner, sleep_inhibitor=self.sleep)
        api.save_settings({"prevent_sleep": True})
        lecture_id = api.save_lecture(self.payload)["data"]["id"]
        self.assertTrue(api.join_now(lecture_id)["ok"])
        self.assertTrue(started.wait(2))
        self.assertTrue(captured[0]["prevent_sleep"])
        self.assertTrue(self.sleep.active)
        self.assertFalse(api.join_now(lecture_id)["ok"])
        window = Mock()
        self.assertFalse(api._request_close(window))
        self.assertFalse(window.destroy.called)
        released.set()
        self.assertTrue(api._scheduler.wait_idle(3))
        self.assertEqual(self.store.get(lecture_id).status, "cancelled")
        import time
        deadline = time.monotonic() + 2
        while not window.destroy.called and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(window.destroy.called)
        self.assertTrue(api._request_close(window))
        self.assertFalse(self.sleep.active)

    def test_account_login_excludes_scheduler_and_is_cancelled_on_close(self):
        import sys
        import threading
        import types
        from unittest.mock import Mock, patch
        started, cleaned, close_requested = threading.Event(), threading.Event(), threading.Event()
        def prepare(url, *, cancel_event, status_callback):
            started.set()
            cancel_event.wait(3)
            cleaned.set()
        with (patch.dict(sys.modules, {"integrations": types.SimpleNamespace(prepare_browser_profile=prepare)}),
              patch("app.request_window_close", side_effect=lambda _: close_requested.set())):
            self.assertFalse(self.api.prepare_account({"url": "https://example.org"})["ok"])
            self.assertTrue(self.api.prepare_account({"url": "https://telemost.yandex.ru/"})["ok"])
            self.assertTrue(started.wait(2))
            self.assertTrue(self.api.get_state()["data"]["account_login"])
            self.assertFalse(self.api.set_scheduler({"enabled": True})["ok"])
            self.assertFalse(self.api.prepare_account({"url": self.payload["url"]})["ok"])
            self.api._request_close(Mock())
            self.assertTrue(cleaned.wait(2))
            self.api._account_thread.join(2)
            self.assertTrue(close_requested.wait(2))

    def test_app_import_needs_neither_gui_nor_integrations(self):
        import subprocess
        import sys
        from pathlib import Path
        result = subprocess.run([sys.executable, "-c", "import app, sys; assert 'webview' not in sys.modules; assert 'integrations' not in sys.modules"], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_second_instance_is_rejected_and_lock_releases(self):
        from app import _InstanceLock
        lock_path = self.store.db_path.parent / "desktop.lock"
        with _InstanceLock(lock_path):
            with self.assertRaises(RuntimeError):
                with _InstanceLock(lock_path):
                    pass
        with _InstanceLock(lock_path):
            pass


if __name__ == "__main__":
    unittest.main()
