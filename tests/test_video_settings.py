import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import DesktopApi
from recording import BrowserTarget, FFmpegRecorder, recording_command
from storage import LectureStore
from video_settings import VIDEO_DEFAULTS, resolve_video_settings, validate_video_settings


class VideoSettingsTests(unittest.TestCase):
    def test_default_stays_1080p_15fps_and_balanced_on_large_screen(self):
        self.assertEqual(resolve_video_settings({}, (3840, 2160)),
                         {'width': 1920, 'height': 1080, 'fps': 15, 'crf': 23, 'limited': False})

    def test_auto_fits_a_smaller_screen_with_even_dimensions(self):
        result = resolve_video_settings({}, (1366, 768))
        self.assertEqual((result['width'], result['height']), (1364, 768))
        self.assertTrue(result['limited'])

    def test_saved_large_resolution_is_reduced_on_the_meeting_monitor(self):
        result = resolve_video_settings({'recording_width': 3840, 'recording_height': 2160}, (1280, 720))
        self.assertEqual((result['width'], result['height']), (1280, 720))

    def test_portrait_and_custom_aspect_ratio_are_preserved(self):
        portrait = resolve_video_settings({'recording_width': 1080, 'recording_height': 1920}, (720, 1280))
        self.assertEqual((portrait['width'], portrait['height']), (720, 1280))
        custom = resolve_video_settings({'recording_width': 1000, 'recording_height': 700}, (1920, 1080))
        self.assertEqual((custom['width'], custom['height']), (1000, 700))

    def test_fps_boundaries_and_custom_value(self):
        for value in (1, 7, 59, 60):
            self.assertEqual(resolve_video_settings({'recording_fps': value}, (1920, 1080))['fps'], value)
        for value in (0, 61, -1, True, 29.97, '30', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_video_settings({'recording_fps': value})

    def test_dimensions_reject_odd_incomplete_and_invalid_numbers(self):
        for width, height in ((1281, 720), (1280, 719), (0, 720), (1280, 0), (62, 64),
                              (True, 720), ('1280', 720), (16386, 1080), (None, None)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                validate_video_settings({'recording_width': width, 'recording_height': height})

    def test_quality_changes_compression_and_rejects_unknown_values(self):
        values = [resolve_video_settings({'recording_quality': quality}, (1920, 1080))['crf']
                  for quality in ('maximum', 'balanced', 'compact', 'economy')]
        self.assertEqual(values, sorted(set(values)))
        for value in ('ultra', None, True, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_video_settings({'recording_quality': value})

    def test_ffmpeg_receives_custom_dimensions_fps_and_compression(self):
        command = recording_command('ffmpeg', 'tcp://127.0.0.1:1', Path('test.mkv'),
                                    width=1000, height=700, fps=7, crf=32)
        for flag, value in (('-video_size', '1000x700'), ('-framerate', '7'), ('-crf', '32'),
                            ('-b:a', '160k')):
            self.assertEqual(command[command.index(flag) + 1], value)


class VideoBridgeTests(unittest.TestCase):
    def setUp(self):
        from capabilities import desktop_capabilities
        capabilities = patch('app.desktop_capabilities', return_value=desktop_capabilities('win32'))
        capabilities.start()
        self.addCleanup(capabilities.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = LectureStore(self.root / 'calendar.sqlite3')
        self.api = DesktopApi(self.store)
        monitor = patch('video_settings.monitor_size', return_value=(1920,1080))
        monitor.start()
        self.addCleanup(monitor.stop)

    def test_new_defaults_preserve_existing_settings_and_restart(self):
        self.assertEqual({key: self.api.get_settings()['data'][key] for key in VIDEO_DEFAULTS}, VIDEO_DEFAULTS)
        settings = {'recording_quality': 'economy', 'recording_fps': 7,
                    'recording_width': 1000, 'recording_height': 700}
        self.assertTrue(self.api.save_settings(settings)['ok'])
        restored = DesktopApi(LectureStore(self.store.db_path)).get_settings()['data']
        self.assertEqual({key: restored[key] for key in settings}, settings)

    def test_resolution_above_monitor_is_rejected_without_saving(self):
        for width, height in ((1922,1080), (1920,1082), (3840,2160)):
            result = self.api.save_settings({'recording_width': width, 'recording_height': height})
            self.assertFalse(result['ok'])
            self.assertIn('1920', result['error'])
        self.assertEqual(self.store.get_settings()['recording_width'], 0)

    def test_partial_dimension_update_does_not_persist_invalid_pair(self):
        self.assertFalse(self.api.save_settings({'recording_width': 1280})['ok'])
        self.assertEqual(self.store.get_settings()['recording_width'], 0)

    def test_monitor_error_is_exposed_and_not_saved_as_success(self):
        with patch('video_settings.monitor_size', side_effect=RuntimeError('monitor unavailable')):
            self.assertFalse(self.api.get_recording_options()['ok'])
            self.assertFalse(self.api.save_settings({'recording_width':1280,'recording_height':720})['ok'])

    def test_options_report_physical_pixels_and_effective_size(self):
        result = self.api.get_recording_options()['data']
        self.assertEqual((result['screen_width'],result['screen_height'],result['max_fps']), (1920,1080,60))


@unittest.skipUnless(sys.platform == "win32", "Windows native backend")
class CaptureDimensionsTests(unittest.TestCase):
    def test_capture_frame_black_frame_and_interval_match_custom_settings(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temporary, patch('recording.monitor_size', return_value=(1280,720)):
            recorder = FFmpegRecorder(BrowserTarget(1,1), {'recordings_dir':temporary,
                                     'recording_width':640,'recording_height':360,'recording_fps':40}, 1)
            self.assertEqual(len(recorder._black_frame), 640*360*4)
            capture = Mock()
            frame = SimpleNamespace(width=400,height=400,frame_buffer=np.full((400,400,4),150,dtype=np.uint8))
            capture.start_free_threaded.side_effect = lambda: (capture.frame_handler(frame, Mock()), Mock())[1]
            with patch('windows_capture.WindowsCapture', return_value=capture) as native, patch('recording._capture_runtime.ensure'):
                recorder._start_video(None)
            self.assertEqual(native.call_args.kwargs['minimum_update_interval'],25)
            image = np.frombuffer(recorder._video_frame,dtype=np.uint8).reshape(360,640,4)
            self.assertFalse(image[:,0].any())
            self.assertTrue((image[180,320] == 150).all())

    def test_runtime_caps_saved_resolution_before_allocating_frames(self):
        with tempfile.TemporaryDirectory() as temporary, patch('recording.monitor_size', return_value=(1280,720)):
            recorder = FFmpegRecorder(BrowserTarget(1,1), {'recordings_dir':temporary,
                                     'recording_width':3840,'recording_height':2160,'recording_fps':60}, 1)
            self.assertEqual(len(recorder._black_frame),1280*720*4)
            self.assertEqual(recorder.video['fps'],60)
