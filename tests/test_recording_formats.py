import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app import DesktopApi
from recording import BrowserTarget, FFmpegRecorder
from storage import Lecture, LectureStore


class RecordingFormatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.store = LectureStore(self.directory / 'calendar.sqlite3')

    def test_format_persists_and_old_settings_default_to_mkv(self):
        api = DesktopApi(self.store)
        self.assertEqual(api.get_settings()['data']['recording_format'], 'mkv')
        self.assertTrue(api.save_settings({'recording_format': 'mp4'})['ok'])
        self.assertEqual(DesktopApi(LectureStore(self.store.db_path)).get_settings()['data']['recording_format'], 'mp4')
        for value in ('avi', '.mp4', 'MP4', '../mp4', '', None, True, [], {}):
            with self.subTest(value=value):
                self.assertFalse(api.save_settings({'recording_format': value})['ok'])

    def test_preview_uses_unsaved_format_and_rejects_invalid_format(self):
        api = DesktopApi(self.store)
        result = api.preview_recording_name({'template': '{name}.mkv', 'format': 'mp4'})
        self.assertEqual(result['data']['filename'], 'Название_лекции.mp4')
        self.assertEqual(api.get_settings()['data']['recording_format'], 'mkv')
        self.assertFalse(api.preview_recording_name({'template': '{name}', 'format': 'avi'})['ok'])

    def test_existing_russian_duration_alias_is_migrated_and_persisted(self):
        self.store.save_settings({'recording_name_template': '{name}-{длительность}'})
        restored = LectureStore(self.store.db_path)
        self.assertEqual(restored.get_settings()['recording_name_template'], '{name}-{duration}')
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            value = connection.execute("SELECT value FROM settings WHERE name='recording_name_template'").fetchone()[0]
        self.assertNotIn('длительность', value)

    def test_mp4_has_the_same_owned_file_deletion_rules(self):
        now = datetime.now()
        lecture_id = self.store.add(Lecture(None, 'Test', 'telemost.yandex.ru/j/test', now, now + timedelta(minutes=1)))
        video = self.directory / 'lecture.mp4'
        video.write_bytes(b'synthetic')
        self.store.save_recording(lecture_id, {'status': 'saved', 'path': str(video)})
        self.assertTrue(self.store.delete(lecture_id, delete_recording=True))
        self.assertFalse(video.exists())


class MP4FinalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.recorder = FFmpegRecorder(BrowserTarget(1, 123), {'recordings_dir': str(self.directory),
                                      'recording_format': 'mp4', 'recording_name_template': 'lecture'}, 1)
        self.original = self.recorder.output
        self.original.write_bytes(b'original MKV')
        self.converted = self.original.with_suffix('.mp4')

    def command(self, arguments, **kwargs):
        if '-c' in arguments:
            self.assertEqual(arguments[arguments.index('-c') + 1], 'copy')
            self.assertIn('+faststart', arguments)
            self.assertIn('-n', arguments)
            self.converted.write_bytes(b'converted MP4')
        return Mock(returncode=0, stderr='Duration: 00:02:00.00')

    def test_remux_publishes_mp4_without_overwriting_and_removes_partial(self):
        existing = self.directory / 'lecture.mp4'
        existing.write_bytes(b'keep existing')
        with patch('recording._run_command', side_effect=self.command) as run:
            output = self.recorder.stop()
        self.assertEqual(Path(output).name, 'lecture-2.mp4')
        self.assertEqual(Path(output).read_bytes(), b'converted MP4')
        self.assertEqual(existing.read_bytes(), b'keep existing')
        self.assertFalse(self.original.exists())
        self.assertFalse(self.converted.exists())
        self.assertEqual(self.recorder.result()['status'], 'saved')
        self.assertEqual(run.call_count, 3)
        self.assertEqual(self.recorder.stop(), output)

    def test_minimum_filter_runs_before_conversion(self):
        self.recorder.minimum_minutes = 3
        with patch('recording._run_command', side_effect=self.command) as run:
            self.assertIsNone(self.recorder.stop())
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.original.exists())
        self.assertFalse(self.converted.exists())
        self.assertEqual(self.recorder.result()['status'], 'discarded')

    def test_failed_remux_keeps_original_and_removes_unfinished_mp4(self):
        def fail(arguments, **kwargs):
            if '-c' in arguments:
                self.converted.write_bytes(b'incomplete MP4')
                return Mock(returncode=1, stderr='disk error')
            return self.command(arguments, **kwargs)
        with patch('recording._run_command', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'Исходная запись сохранена'):
            self.recorder.stop()
        self.assertEqual(self.original.read_bytes(), b'original MKV')
        self.assertFalse(self.converted.exists())
        self.assertEqual(self.recorder.result()['path'], str(self.original))
        self.assertEqual(self.recorder.result()['status'], 'partial')

    def test_invalid_mp4_tracks_preserve_original(self):
        def invalid(arguments, **kwargs):
            if str(self.converted) in arguments and '-c' not in arguments:
                return Mock(returncode=1, stderr='missing audio')
            return self.command(arguments, **kwargs)
        with patch('recording._run_command', side_effect=invalid), self.assertRaisesRegex(RuntimeError, 'missing audio'):
            self.recorder.stop()
        self.assertTrue(self.original.is_file())
        self.assertFalse(self.converted.exists())

    def test_conversion_timeout_keeps_original(self):
        def timeout(arguments, **kwargs):
            if '-c' in arguments:
                raise subprocess.TimeoutExpired(arguments, 900)
            return self.command(arguments, **kwargs)
        with patch('recording._run_command', side_effect=timeout), self.assertRaisesRegex(RuntimeError, 'MP4'):
            self.recorder.stop()
        self.assertTrue(self.original.is_file())

    def test_existing_temporary_mp4_is_never_deleted(self):
        self.converted.write_bytes(b'unrelated file')
        with patch('recording._run_command', side_effect=self.command), self.assertRaisesRegex(RuntimeError, 'уже существует'):
            self.recorder.stop()
        self.assertEqual(self.converted.read_bytes(), b'unrelated file')
        self.assertTrue(self.original.is_file())


if __name__ == '__main__':
    unittest.main()
