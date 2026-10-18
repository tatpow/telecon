import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from recording_names import DEFAULT_RECORDING_TEMPLATE, recording_stem
from recording import _CaptureRuntime


class RecordingNamesTests(unittest.TestCase):
    def render(self, template=DEFAULT_RECORDING_TEMPLATE, **values):
        arguments = {"name": "История архитектуры", "url": "https://telemost.yandex.ru/j/63943399915234",
                     "started_at": datetime(2026, 10, 18, 11, 56),
                     "ended_at": datetime(2026, 10, 18, 13, 21, 59), "duration_seconds": 5159.9}
        arguments.update(values)
        return recording_stem(template, **arguments)

    def test_all_approved_attributes(self):
        result = self.render("{name}-{date}-{date_iso}-{time}-{time_end}-{duration}")
        self.assertEqual(result["stem"], "История_архитектуры-181026-2026-10-18-11-56-13-21-85")
        self.assertFalse(result["fallback"])

    def test_name_with_forbidden_characters_uses_meeting_id_without_modifying_other_attributes(self):
        for name in ('Лекция: история', 'name/file', 'name\\file', 'name?file', 'name*file', 'name"file', 'line\nbreak', 'CON', 'nul.mkv', 'LPT1', 'end.', '', '   ', 'x' * 300):
            with self.subTest(name=name):
                result = self.render(name=name)
                self.assertEqual(result["stem"], "63943399915234-181026-85")
                self.assertTrue(result["fallback"])

    def test_broken_template_uses_only_meeting_id(self):
        for template in ("", "{name", "{wrong}", "{name!r}", "{name:>20}", "{name.__class__}",
                         "../{name}", "folder/{name}", "{name}\\video", "CON", "{name}?", "{{name}}"):
            with self.subTest(template=template):
                result = self.render(template)
                self.assertEqual(result["stem"], "63943399915234")
                self.assertTrue(result["fallback"])

    def test_unapproved_attributes_are_not_exposed(self):
        for field in ("id", "meeting_id", "duration_seconds", "planned_duration", "end_time", "длительность"):
            with self.subTest(field=field):
                self.assertEqual(self.render("{" + field + "}")["stem"], "63943399915234")

    def test_extension_is_added_once_and_literal_names_are_allowed(self):
        self.assertEqual(self.render("{name}.mkv")["stem"], "История_архитектуры")
        self.assertEqual(self.render("{name}.mp4")["stem"], "История_архитектуры")
        self.assertEqual(self.render("lecture")["stem"], "lecture")

    def test_date_belongs_to_start_when_recording_crosses_midnight(self):
        result = self.render("{date}-{time}-{time_end}", started_at=datetime(2026, 10, 18, 23, 50), ended_at=datetime(2026, 10, 19, 1, 15))
        self.assertEqual(result["stem"], "181026-23-50-01-15")

    def test_unknown_duration_does_not_invent_a_value(self):
        self.assertEqual(self.render(duration_seconds=None)["stem"], "63943399915234")
        self.assertEqual(self.render("{name}", duration_seconds=None)["stem"], "История_архитектуры")

    def test_template_and_preview_are_persisted_through_bridge(self):
        import tempfile
        from app import DesktopApi
        from storage import LectureStore
        with tempfile.TemporaryDirectory() as directory:
            store = LectureStore(Path(directory) / "test.sqlite3")
            api = DesktopApi(store)
            self.assertTrue(api.save_settings({"recording_name_template": "{name}-{date_iso}-{duration}"})["ok"])
            self.assertEqual(LectureStore(store.db_path).get_settings()["recording_name_template"], "{name}-{date_iso}-{duration}")
            self.assertTrue(api.save_settings({"recording_name_template": "{broken"})["ok"])
            self.assertEqual(api.preview_recording_name({"template": "{broken"})["data"], {"filename": "1234567890.mkv", "fallback": True})
            for value in (None, 123, True, "x" * 301):
                self.assertFalse(api.save_settings({"recording_name_template": value})["ok"])


class CaptureRuntimeTests(unittest.TestCase):
    def test_runtime_survives_between_workers_and_is_released_once(self):
        runtime = _CaptureRuntime()
        ole = Mock()
        def increment(pointer):
            pointer._obj.value = 42
            return 0
        ole.CoIncrementMTAUsage.side_effect = increment
        with patch("recording.ctypes.WinDLL", return_value=ole), patch("recording.atexit.register") as register:
            runtime.ensure()
            runtime.ensure()
        ole.CoIncrementMTAUsage.assert_called_once()
        register.assert_called_once_with(runtime.close)
        runtime.close()
        runtime.close()
        ole.CoDecrementMTAUsage.assert_called_once()

    def test_failed_runtime_initialization_is_reported_without_a_cookie(self):
        runtime = _CaptureRuntime()
        ole = Mock()
        ole.CoIncrementMTAUsage.return_value = -1
        with patch("recording.ctypes.WinDLL", return_value=ole), patch("recording.atexit.register") as register:
            with self.assertRaisesRegex(RuntimeError, "COM"):
                runtime.ensure()
        register.assert_not_called()
        runtime.close()
        ole.CoDecrementMTAUsage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
