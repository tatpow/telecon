import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app import DesktopApi
from recording import BrowserTarget, FFmpegRecorder
from storage import Lecture, LectureStore


class RecordingFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = LectureStore(self.root / "lectures.sqlite3")
        now = datetime.now()
        self.lecture_id = self.store.add(Lecture(None, "Лекция", "telemost.yandex.ru/j/test", now, now + timedelta(hours=1)))
        self.file = self.root / "lecture.mkv"
        self.file.write_bytes(b"synthetic recording")
        self.store.save_recording(self.lecture_id, {"path": str(self.file), "status": "saved", "duration_seconds": 85})

    def test_calendar_only_keeps_video_and_removes_metadata(self):
        self.assertTrue(self.store.delete(self.lecture_id))
        self.assertTrue(self.file.is_file())
        self.assertIsNone(self.store.recording_for(self.lecture_id))

    def test_delete_with_recording_removes_only_owned_video(self):
        other = self.root / "other.mkv"
        other.write_bytes(b"other recording")
        self.assertTrue(self.store.delete(self.lecture_id, delete_recording=True))
        self.assertFalse(self.file.exists())
        self.assertTrue(other.is_file())

    def test_file_deletion_requires_explicit_confirmation(self):
        api = DesktopApi(self.store)
        self.assertFalse(api.delete_lecture({"id": self.lecture_id, "delete_recording": True})["ok"])
        self.assertTrue(self.file.is_file())
        self.assertTrue(api.delete_lecture({"id": self.lecture_id, "delete_recording": True, "confirmed": True})["ok"])
        self.assertFalse(self.file.exists())

    def test_missing_video_does_not_prevent_calendar_deletion(self):
        self.file.unlink()
        self.assertTrue(self.store.delete(self.lecture_id, delete_recording=True))

    def test_running_lecture_never_deletes_video(self):
        self.store.claim(self.lecture_id, datetime.now())
        self.assertFalse(self.store.delete(self.lecture_id, delete_recording=True))
        self.assertTrue(self.file.is_file())

    def test_disk_error_keeps_calendar_and_metadata(self):
        with patch.object(Path, "unlink", side_effect=PermissionError("file is busy")):
            with self.assertRaises(PermissionError):
                self.store.delete(self.lecture_id, delete_recording=True)
        self.assertIsNotNone(self.store.get(self.lecture_id))
        self.assertIsNotNone(self.store.recording_for(self.lecture_id))
        self.assertTrue(self.file.exists())

    def test_replaced_file_is_not_deleted(self):
        original = self.root / "original.mkv"
        self.file.rename(original)
        self.file.write_bytes(b"a different file")
        with self.assertRaisesRegex(ValueError, "заменён"):
            self.store.delete(self.lecture_id, delete_recording=True)
        self.assertEqual(self.file.read_bytes(), b"a different file")
        self.assertTrue(original.exists())
        self.assertIsNotNone(self.store.get(self.lecture_id))

    def test_source_paths_and_links_are_never_registered(self):
        source = Path(__file__).resolve().parents[1]
        with self.assertRaises(ValueError):
            self.store.save_recording(self.lecture_id, {"path": str(source / "not-a-recording.mkv"), "status": "saved"})
        with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(ValueError):
            self.store.save_recording(self.lecture_id, {"path": str(self.file), "status": "saved"})

    def test_recording_metadata_survives_restart_and_is_exposed_without_disk_paths(self):
        restored = LectureStore(self.store.db_path)
        result = DesktopApi(restored).get_state()["data"]["lectures"][0]["recording"]
        self.assertEqual(result["filename"], "lecture.mkv")
        self.assertEqual(result["duration_seconds"], 85)
        self.assertNotIn("path", result)

    def test_minimum_length_setting_accepts_zero_and_rejects_invalid_values(self):
        api = DesktopApi(self.store)
        for value in (0, 1, 1440):
            self.assertTrue(api.save_settings({"minimum_recording_minutes": value})["ok"])
        for value in (True, -1, 1441, 1.5, "5", None):
            self.assertFalse(api.save_settings({"minimum_recording_minutes": value})["ok"])
        self.assertEqual(LectureStore(self.store.db_path).get_settings()["minimum_recording_minutes"], 1440)


class MinimumRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.recorder = FFmpegRecorder(BrowserTarget(1, 123), {"recordings_dir": self.temp.name, "minimum_recording_minutes": 1}, 8)
        self.partial = self.recorder.output
        self.partial.write_bytes(b"synthetic media")

    def validation(self, duration):
        return patch("recording._run_command", return_value=Mock(returncode=0, stderr=f"Duration: {duration}, start: 0.000000"))

    def test_short_video_is_removed_and_reason_is_available(self):
        with self.validation("00:00:59.99"):
            self.assertIsNone(self.recorder.stop())
        self.assertFalse(self.partial.exists())
        result = self.recorder.result()
        self.assertEqual(result["status"], "discarded")
        self.assertEqual(result["duration_seconds"], 59.99)
        self.assertIsNone(result["path"])
        self.assertIn("короче 1 мин.", result["note"])
        self.assertEqual(list(Path(self.temp.name).glob("*.mkv")), [])
        self.assertIsNone(self.recorder.stop())

    def test_exact_minimum_is_saved_and_published_after_validation(self):
        with self.validation("00:01:00.00"):
            output = self.recorder.stop()
        self.assertFalse(self.partial.exists())
        self.assertEqual(output, str(self.recorder.output))
        self.assertEqual(self.recorder.recording_status, "saved")
        self.assertTrue(Path(output).is_file())
        self.assertNotIn(".partial", output)
        self.assertEqual(self.recorder.stop(), output)

    def test_zero_minimum_keeps_a_short_recording(self):
        self.recorder.minimum_minutes = 0
        with self.validation("00:00:00.50"):
            output = self.recorder.stop()
        self.assertTrue(Path(output).is_file())

    def test_unknown_duration_keeps_partial_and_reports_failure(self):
        with self.validation("N/A"), self.assertRaisesRegex(RuntimeError, "длительность"):
            self.recorder.stop()
        self.assertTrue(self.partial.exists())
        self.assertEqual(self.recorder.recording_status, "partial")

    def test_invalid_media_is_not_discarded_or_reported_saved(self):
        with patch("recording._run_command", return_value=Mock(returncode=1, stderr="corrupt media")):
            with self.assertRaises(RuntimeError):
                self.recorder.stop()
        self.assertTrue(self.partial.exists())
        self.assertEqual(self.recorder.recording_status, "partial")

    def test_existing_destination_is_preserved_and_new_file_gets_suffix(self):
        self.recorder.name_template = "lecture"
        existing = Path(self.temp.name) / "lecture.mkv"
        existing.write_bytes(b"existing recording")
        with self.validation("00:02:00.00"):
            output = self.recorder.stop()
        self.assertEqual(existing.read_bytes(), b"existing recording")
        self.assertEqual(Path(output).name, "lecture-2.mkv")
        self.assertFalse(self.partial.exists())


if __name__ == "__main__":
    unittest.main()
