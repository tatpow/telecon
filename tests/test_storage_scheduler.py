import tempfile
import sqlite3
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from storage import Lecture, LectureStore, validate_telemost_url


class LectureStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store = LectureStore(Path(self.temp_dir.name) / "lectures.sqlite3")
        self.now = datetime(2030, 5, 10, 12, 0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_lecture(
        self, start_delta, duration_minutes=60, url="https://telemost.yandex.ru/j/abc", lead_minutes=2
    ):
        start = self.now + start_delta
        return self.store.add(
            Lecture(
                None,
                "Лекция",
                url,
                start,
                start + timedelta(minutes=duration_minutes),
                lead_minutes=lead_minutes,
            )
        )

    def test_add_and_list_lecture(self):
        lecture_id = self.add_lecture(timedelta(hours=1))
        lecture = self.store.list_all()[0]
        self.assertEqual(lecture.id, lecture_id)
        self.assertEqual(lecture.title, "Лекция")
        self.assertEqual(lecture.status, "scheduled")

    def test_claim_due_at_two_minute_lead(self):
        self.add_lecture(timedelta(minutes=2))
        self.assertIsNone(self.store.claim_due(self.now - timedelta(seconds=1)))
        claimed = self.store.claim_due(self.now)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(self.store.list_all()[0].status, "running")
        self.assertIsNone(self.store.claim_due(self.now + timedelta(seconds=2)))

    def test_custom_reminder_lead_is_used(self):
        self.add_lecture(timedelta(minutes=10), lead_minutes=10)
        claimed = self.store.claim_due(self.now)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(claimed.lead_minutes, 10)

    def test_expired_lecture_is_marked_missed(self):
        self.add_lecture(timedelta(minutes=-70), duration_minutes=60)
        self.assertIsNone(self.store.claim_due(self.now))
        self.assertEqual(self.store.list_all()[0].status, "missed")

    def test_invalid_url_and_time_are_rejected(self):
        with self.assertRaises(ValueError):
            self.add_lecture(timedelta(hours=1), url="file:///private")
        start = self.now + timedelta(hours=1)
        with self.assertRaises(ValueError):
            self.store.add(Lecture(None, "Invalid", "https://example.test", start, start))

    def test_failed_event_keeps_error(self):
        lecture_id = self.add_lecture(timedelta(hours=1))
        self.store.finish(lecture_id, "FFmpeg недоступен")
        self.assertEqual(self.store.list_all()[0].status, "failed")
        self.assertEqual(self.store.error_for(lecture_id), "FFmpeg недоступен")

    def test_only_configured_telemost_meeting_urls_are_allowed(self):
        self.assertEqual(
            validate_telemost_url(" HTTPS://TELEMOST.YANDEX.RU:443/j/42574758760781/ "),
            "https://telemost.yandex.ru/j/42574758760781",
        )
        for url in (
            "http://telemost.yandex.ru/j/123",
            "https://telemost.yandex.ru.evil.test/j/123",
            "https://www.telemost.yandex.ru/j/123",
            "https://evil.test@telemost.yandex.ru/j/123",
            "https://telemost.yandex.ru:444/j/123",
            "https://telemost.yandex.ru/j/123/other",
            "https://telemost.yandex.ru/",
            "https://telemost.yandex.ru/j/123\n?x=1",
            "https://telemost.yandex.ru\\@evil.test/j/123",
            "https://telemost.yandex.ru/j/%2e%2e",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_telemost_url(url)

    def test_lead_minutes_rejects_boolean_and_non_integral_numbers(self):
        for lead in (True, False, 1.5, "2", -1, 121):
            with self.subTest(lead=lead), self.assertRaises(ValueError):
                self.add_lecture(timedelta(hours=1), lead_minutes=lead)

    def test_lectures_are_returned_in_chronological_order(self):
        later = self.add_lecture(timedelta(hours=2))
        earlier = self.add_lecture(timedelta(hours=1))
        self.assertEqual([lecture.id for lecture in self.store.list_all()], [earlier, later])

    def test_manual_claim_is_allowed_before_start(self):
        lecture_id = self.add_lecture(timedelta(hours=1))
        self.assertIsNone(self.store.claim_due(self.now))
        self.assertEqual(self.store.claim(lecture_id, self.now).status, "running")
        self.assertIsNone(self.store.claim(lecture_id, self.now))

    def test_running_lecture_blocks_another_automatic_or_manual_claim(self):
        first = self.add_lecture(timedelta(minutes=-1))
        second = self.add_lecture(timedelta())
        self.assertEqual(self.store.claim_due(self.now).id, first)
        self.assertIsNone(self.store.claim_due(self.now))
        self.assertIsNone(self.store.claim(second, self.now))
        self.store.finish(first)
        self.assertEqual(self.store.claim_due(self.now).id, second)

    def test_concurrent_stores_cannot_claim_two_lectures(self):
        self.add_lecture(timedelta(minutes=-1))
        self.add_lecture(timedelta())
        other = LectureStore(self.store.db_path)
        barrier = threading.Barrier(2)
        claims = []
        errors = []

        def claim(store):
            try:
                barrier.wait(timeout=2)
                claims.append(store.claim_due(self.now))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=claim, args=(store,)) for store in (self.store, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sum(lecture is not None for lecture in claims), 1)

    def test_update_applies_only_to_scheduled_and_delete_preserves_running_lectures(self):
        lecture_id = self.add_lecture(timedelta(hours=1))
        original = self.store.get(lecture_id)
        updated = Lecture(lecture_id, "Новое название", original.url, original.starts_at, original.ends_at)
        self.assertTrue(self.store.update(updated))
        self.assertEqual(self.store.get(lecture_id).title, "Новое название")
        self.store.claim(lecture_id, self.now)
        self.assertFalse(self.store.update(original))
        self.assertFalse(self.store.delete(lecture_id))
        self.assertEqual(self.store.get(lecture_id).status, "running")
        scheduled = self.add_lecture(timedelta(hours=2))
        self.assertTrue(self.store.delete(scheduled))
        self.assertIsNone(self.store.get(scheduled))

    def test_completed_failed_cancelled_and_missed_lectures_can_be_deleted(self):
        for status in ("done", "failed", "cancelled", "missed"):
            with self.subTest(status=status):
                lecture_id = self.add_lecture(timedelta(hours=1))
                if status == "missed":
                    self.store.claim_due(self.now + timedelta(hours=3))
                else:
                    self.store.finish(
                        lecture_id, "Ошибка FFmpeg" if status == "failed" else None,
                        cancelled=status == "cancelled",
                    )
                self.assertEqual(self.store.get(lecture_id).status, status)
                self.assertTrue(self.store.delete(lecture_id))
                self.assertIsNone(self.store.get(lecture_id))

    def test_cancelled_and_interrupted_lectures_are_not_successful(self):
        lecture_id = self.add_lecture(timedelta())
        self.store.claim_due(self.now)
        self.store.finish(lecture_id, cancelled=True)
        self.assertEqual(self.store.get(lecture_id).status, "cancelled")
        self.assertEqual(self.store.error_for(lecture_id), "Остановлена пользователем")
        next_id = self.add_lecture(timedelta())
        self.store.claim_due(self.now)
        self.store.recover_interrupted()
        self.assertEqual(self.store.get(next_id).status, "failed")
        self.assertIn("файл записи", self.store.error_for(next_id))

    def test_settings_survive_reopening_without_storing_passwords(self):
        self.store.save_settings({"recordings_dir": "C:/Videos/Telecon", "prevent_sleep": False, "default_lead_minutes": 0, "theme": "light"})
        settings = LectureStore(self.store.db_path).get_settings()
        self.assertEqual(settings["recordings_dir"], "C:/Videos/Telecon")
        self.assertFalse(settings["prevent_sleep"])
        self.assertEqual(settings["default_lead_minutes"], 0)
        self.assertFalse(settings["auto_start"])
        with self.assertRaises(ValueError):
            self.store.save_settings({"obs_password": "secret", "obs_port": 4457})
        self.assertEqual(self.store.get_settings()["recordings_dir"], "C:/Videos/Telecon")
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            self.assertFalse(connection.execute("SELECT 1 FROM settings WHERE name = 'obs_password'").fetchone())

    def test_settings_reject_invalid_types_and_obsolete_obs(self):
        for settings in (
            {"prevent_sleep": "false"}, {"prevent_sleep": 1}, {"recordings_dir": 42}, {"ffmpeg_path": "a\n"},
            {"default_lead_minutes": False}, {"default_lead_minutes": 2.5},
            {"auto_start": "false"}, {"obs_host": "example.test"},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.store.save_settings(settings)

    def test_existing_database_is_migrated_without_losing_lectures(self):
        legacy = Path(self.temp_dir.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute(
                "CREATE TABLE lectures (id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, "
                "starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled')"
            )
            connection.execute(
                "INSERT INTO lectures VALUES (1, 'Старая лекция', 'https://telemost.yandex.ru/j/123', ?, ?, 'scheduled')",
                (self.now.isoformat(), (self.now + timedelta(hours=1)).isoformat()),
            )
            connection.commit()
        migrated = LectureStore(legacy)
        self.assertEqual(migrated.get(1).title, "Старая лекция")
        self.assertEqual(migrated.get(1).lead_minutes, 2)
        self.assertIsNone(migrated.error_for(1))


if __name__ == "__main__":
    unittest.main()
