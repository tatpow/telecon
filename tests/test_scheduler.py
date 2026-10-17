import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scheduler import LectureScheduler
from storage import Lecture, LectureStore


class LectureSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store = LectureStore(Path(self.temp_dir.name) / "lectures.sqlite3")
        starts_at = datetime.now() - timedelta(seconds=5)
        self.lecture_id = self.store.add(
            Lecture(
                None,
                "Лекция",
                "https://telemost.yandex.ru/j/abc",
                starts_at,
                starts_at + timedelta(minutes=10),
                lead_minutes=0,
            )
        )
        self.schedulers = []

    def tearDown(self):
        for scheduler in self.schedulers:
            scheduler.stop()
            scheduler.cancel_active()
            scheduler.wait_idle(3)
            if scheduler._thread:
                scheduler._thread.join(timeout=2)
        self.temp_dir.cleanup()

    def scheduler(self, runner, callback=None):
        scheduler = LectureScheduler(self.store, runner, callback or (lambda message: None))
        self.schedulers.append(scheduler)
        return scheduler

    def test_stopping_schedule_preserves_active_recording_until_cancelled(self):
        runner_started = threading.Event()
        runner_finished = threading.Event()
        lecture_finished = threading.Event()
        runner_cancel = []

        def runner(lecture, cancel_event):
            runner_cancel.append(cancel_event)
            runner_started.set()
            cancel_event.wait(2)
            runner_finished.set()

        def status_callback(message):
            if message.startswith("Остановлена:"):
                lecture_finished.set()

        scheduler = self.scheduler(runner, status_callback)
        scheduler.start()
        try:
            self.assertTrue(runner_started.wait(3))
            scheduler.stop()
            self.assertTrue(scheduler.active)
            self.assertFalse(runner_cancel[0].is_set())

            scheduler.cancel_active()
            self.assertTrue(runner_finished.wait(2))
            self.assertTrue(lecture_finished.wait(2))
            scheduler._thread.join(timeout=2)
            self.assertFalse(scheduler.active)
            lecture = self.store.list_all()[0]
            self.assertEqual(lecture.status, "cancelled")
            self.assertEqual(self.store.error_for(self.lecture_id), "Остановлена пользователем")
        finally:
            scheduler.stop()
            scheduler.cancel_active()
            if scheduler._thread:
                scheduler._thread.join(timeout=2)

    def test_start_now_works_without_enabling_schedule(self):
        called = threading.Event()
        scheduler = self.scheduler(lambda lecture, cancel: called.set())
        self.assertTrue(scheduler.start_now(self.lecture_id))
        self.assertTrue(called.wait(2))
        self.assertTrue(scheduler.wait_idle(2))
        self.assertFalse(scheduler.running)
        self.assertEqual(self.store.get(self.lecture_id).status, "done")

    def test_manual_start_and_scheduler_share_one_active_slot(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def runner(lecture, cancel):
            calls.append(lecture.id)
            started.set()
            release.wait(3)

        scheduler = self.scheduler(runner)
        scheduler.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertFalse(scheduler.start_now(self.lecture_id))
            self.assertEqual(scheduler.active_lecture_id, self.lecture_id)
            self.assertEqual(calls, [self.lecture_id])
        finally:
            scheduler.stop()
            release.set()
        self.assertTrue(scheduler.wait_idle(2))

    def test_runner_and_callback_errors_do_not_strand_active_slot(self):
        def runner(lecture, cancel):
            raise RuntimeError("OBS недоступен")

        def callback(message):
            raise RuntimeError("Окно закрыто")

        scheduler = self.scheduler(runner, callback)
        self.assertTrue(scheduler.start_now(self.lecture_id))
        self.assertTrue(scheduler.wait_idle(2))
        self.assertFalse(scheduler.active)
        self.assertIsNone(scheduler.active_lecture_id)
        self.assertEqual(self.store.get(self.lecture_id).status, "failed")
        self.assertEqual(self.store.error_for(self.lecture_id), "OBS недоступен")

    def test_result_persistence_failure_does_not_report_success_or_strand_active(self):
        messages = []
        scheduler = self.scheduler(lambda lecture, cancel: None, messages.append)
        with patch.object(self.store, "finish", side_effect=OSError("Диск недоступен")):
            self.assertTrue(scheduler.start_now(self.lecture_id))
            self.assertTrue(scheduler.wait_idle(2))
        self.assertFalse(scheduler.active)
        self.assertTrue(any("Диск недоступен" in message for message in messages))
        self.assertFalse(any("Запись завершена" in message for message in messages))

    def test_failure_during_cancellation_is_reported_as_failed(self):
        started = threading.Event()

        def runner(lecture, cancel):
            started.set()
            cancel.wait(3)
            raise RuntimeError("Не удалось остановить запись OBS")

        scheduler = self.scheduler(runner)
        scheduler.start_now(self.lecture_id)
        self.assertTrue(started.wait(2))
        scheduler.cancel_active()
        self.assertTrue(scheduler.wait_idle(2))
        self.assertEqual(self.store.get(self.lecture_id).status, "failed")
        self.assertIn("OBS", self.store.error_for(self.lecture_id))

    def test_explicit_cancellation_exception_is_saved_as_cancelled(self):
        class LectureCancelled(RuntimeError):
            pass

        def runner(lecture, cancel):
            raise LectureCancelled("Остановлена пользователем")

        scheduler = self.scheduler(runner)
        scheduler.start_now(self.lecture_id)
        self.assertTrue(scheduler.wait_idle(2))
        self.assertEqual(self.store.get(self.lecture_id).status, "cancelled")

    def test_concurrent_start_calls_create_one_schedule_thread(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def runner(lecture, cancel):
            calls.append(lecture.id)
            started.set()
            release.wait(3)

        scheduler = self.scheduler(runner)
        threads = [threading.Thread(target=scheduler.start) for _ in range(8)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            self.assertTrue(started.wait(2))
            self.assertEqual(calls, [self.lecture_id])
        finally:
            scheduler.stop()
            release.set()

    def test_quick_stop_and_restart_keeps_schedule_running(self):
        scheduler = self.scheduler(lambda lecture, cancel: None)
        for _ in range(10):
            scheduler.start()
            scheduler.stop()
        scheduler.start()
        self.assertTrue(scheduler.running)
        self.assertTrue(scheduler.wait_idle(2))

    def test_failed_worker_start_releases_claim_and_active_state(self):
        scheduler = self.scheduler(lambda lecture, cancel: None)
        with patch("scheduler.threading.Thread.start", side_effect=RuntimeError("Нет потока")):
            with self.assertRaisesRegex(RuntimeError, "Нет потока"):
                scheduler.start_now(self.lecture_id)
        self.assertFalse(scheduler.active)
        self.assertTrue(scheduler.wait_idle(0))
        self.assertEqual(self.store.get(self.lecture_id).status, "failed")

    def test_invalid_manual_lecture_ids_are_rejected(self):
        scheduler = self.scheduler(lambda lecture, cancel: None)
        for lecture_id in (True, 1.0, None, 0, -1):
            with self.subTest(lecture_id=lecture_id), self.assertRaises(ValueError):
                scheduler.start_now(lecture_id)

    def test_concurrent_quick_joins_create_only_one_lecture(self):
        started = threading.Event()
        results = []
        scheduler = self.scheduler(lambda lecture, cancel: (started.set(), cancel.wait(3)))
        now = datetime.now()
        lecture = Lecture(None, "Сейчас", "telemost.yandex.ru/j/quick", now, now + timedelta(minutes=5), lead_minutes=0)
        threads = [threading.Thread(target=lambda: results.append(scheduler.start_quick(lecture))) for _ in range(8)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
            self.assertTrue(started.wait(2))
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(len(self.store.list_all()), 2)  # Existing planned + one quick join.
            self.assertEqual(self.store.get(self.lecture_id).status, "scheduled")
        finally:
            scheduler.cancel_active()
            self.assertTrue(scheduler.wait_idle(3))

    def test_quick_join_failed_claim_does_not_leave_an_automatic_launch(self):
        scheduler = self.scheduler(lambda lecture, cancel: None)
        now = datetime.now()
        lecture = Lecture(None, "Сейчас", "telemost.yandex.ru/j/quick", now, now + timedelta(minutes=5))
        with patch.object(self.store, "claim", side_effect=RuntimeError("database error")):
            with self.assertRaisesRegex(RuntimeError, "database error"):
                scheduler.start_quick(lecture)
        self.assertEqual([item.id for item in self.store.list_all()], [self.lecture_id])

    def test_expired_quick_join_does_not_persist(self):
        scheduler = self.scheduler(lambda lecture, cancel: None)
        now = datetime.now()
        lecture = Lecture(None, "Сейчас", "telemost.yandex.ru/j/quick", now - timedelta(minutes=5), now - timedelta(minutes=1))
        self.assertIsNone(scheduler.start_quick(lecture))
        self.assertEqual([item.id for item in self.store.list_all()], [self.lecture_id])


if __name__ == "__main__":
    unittest.main()
