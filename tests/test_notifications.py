import sys
import io
import struct
import unittest
import wave
from unittest.mock import Mock, patch

from notifications import HiddenWindowAlerts, WindowsAlerts, alert_wave
from storage import ALERT_DEFAULTS


class HiddenWindowAlertsTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock(audio_error="")
        self.settings = dict(ALERT_DEFAULTS)
        self.alerts = HiddenWindowAlerts(self.settings, self.backend)

    def test_hidden_interval_alerts_once_and_restoring_clears_indicators(self):
        self.alerts.update(False)
        self.backend.sound.assert_not_called()
        for _ in range(10):
            self.alerts.update(True)
        self.backend.sound.assert_called_once_with(50)
        self.backend.notification.assert_called_once()
        self.backend.badge.assert_called_once_with(True)
        self.assertTrue(self.alerts.state()["meeting_hidden"])
        self.alerts.update(False)
        self.backend.clear_notification.assert_called_once()
        self.assertEqual(self.backend.badge.call_args.args, (False,))
        self.assertFalse(self.alerts.state()["meeting_hidden"])
        self.alerts.update(True)
        self.assertEqual(self.backend.sound.call_count, 2)
        self.assertEqual(self.backend.notification.call_count, 2)

    def test_each_channel_can_be_disabled_independently(self):
        for disabled in ("sound", "notification", "badge"):
            with self.subTest(channel=disabled):
                backend = Mock(audio_error="")
                alerts = HiddenWindowAlerts(dict(self.settings, **{f"alert_{disabled}": False}), backend)
                alerts.update(True)
                for channel in ("sound", "notification", "badge"):
                    self.assertEqual(getattr(backend, channel).call_count, int(channel != disabled))

    def test_zero_volume_mutes_sound_without_disabling_other_channels(self):
        self.alerts.configure(dict(self.settings, alert_volume=0))
        self.alerts.update(True)
        self.backend.sound.assert_not_called()
        self.backend.notification.assert_called_once()
        self.backend.badge.assert_called_once_with(True)

    def test_live_settings_clear_badge_and_notification_without_repeating_sound(self):
        self.alerts.update(True)
        self.alerts.configure(dict(self.settings, alert_badge=False, alert_notification=False, alert_volume=25))
        self.backend.sound.assert_called_once_with(50)
        self.assertEqual(self.backend.badge.call_args.args, (False,))
        self.backend.clear_notification.assert_called_once()
        self.alerts.configure(dict(self.settings, alert_volume=25))
        self.assertEqual(self.backend.notification.call_count, 2)
        self.backend.sound.assert_called_once_with(50)
        self.alerts.update(False)
        self.alerts.update(True)
        self.assertEqual(self.backend.sound.call_args.args, (25,))

    def test_all_channels_off_remains_quiet(self):
        self.alerts.configure(dict(self.settings, alert_sound=False, alert_notification=False, alert_badge=False))
        self.alerts.update(True)
        self.assertTrue(self.alerts.state()["meeting_hidden"])
        self.assertEqual(self.backend.method_calls, [])

    def test_failed_notification_does_not_break_other_channels_or_repeat_forever(self):
        self.backend.notification.side_effect = RuntimeError("shell unavailable")
        with self.assertLogs("notifications", level="ERROR"):
            self.alerts.update(True)
        self.alerts.update(True)
        self.backend.notification.assert_called_once()
        self.backend.badge.assert_called_once_with(True)
        self.assertIn("shell unavailable", self.alerts.state()["error"])
        self.alerts.close()
        self.assertFalse(self.alerts.state()["meeting_hidden"])

    def test_preview_validates_volume_and_uses_unsaved_level(self):
        self.alerts.preview(12)
        self.backend.sound.assert_called_once_with(12)
        for value in (True, None, "50", -1, 101, 25.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.alerts.preview(value)

    def test_audio_errors_are_visible(self):
        self.backend.audio_error = "audio device unavailable"
        self.assertIn("audio device", self.alerts.state()["error"])


class NativeNotificationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows native backend")
    def test_balloon_never_plays_system_sound_and_is_deleted_on_restore(self):
        backend = WindowsAlerts()
        calls = []
        with patch.object(backend, "_hwnd", return_value=123), \
                patch.object(backend, "_icon", return_value=456), \
                patch.object(backend, "_shell", side_effect=lambda operation, data: calls.append((operation, data))):
            backend.notification()
            backend.clear_notification()
        self.assertEqual([operation for operation, _ in calls], [0, 2])
        data = calls[0][1]
        self.assertEqual(data.hWnd, 123)
        self.assertTrue(data.dwInfoFlags & 0x10)  # NIIF_NOSOUND.
        self.assertTrue(data.uFlags & 0x40)  # NIF_REALTIME avoids stale messages.
        self.assertIsNone(backend._tray_hwnd)

    def test_wave_amplitude_follows_volume_with_fixed_duration(self):
        def samples(volume):
            with wave.open(io.BytesIO(alert_wave(volume)), "rb") as sound:
                self.assertEqual(sound.getnchannels(), 1)
                self.assertEqual(sound.getframerate(), 22050)
                count = sound.getnframes()
                self.assertGreater(count, 22050)
                self.assertLess(count, 33075)
                return struct.unpack(f"<{count}h", sound.readframes(count))
        full, quarter, silent = samples(100), samples(25), samples(0)
        self.assertAlmostEqual(max(quarter) / max(full), .25, places=3)
        self.assertEqual(max(map(abs, silent)), 0)
        self.assertEqual(full[0], 0)
        self.assertEqual(full[-1], 0)
        self.assertLess(max(abs(b-a) for a, b in zip(full, full[1:])), 1800)


if __name__ == "__main__":
    unittest.main()
