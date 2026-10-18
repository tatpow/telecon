import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess
import integrations
import recording

class LectureRecordingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now()
        self.lecture = integrations.Lecture(7, 'Lecture', 'https://telemost.yandex.ru/j/42574758760781', self.now, self.now+timedelta(seconds=2))
        self.p, self.context, self.page, self.meeting = Mock(), Mock(), Mock(), Mock()
        self.page.is_closed.return_value = False
        self.recorder = Mock()
        self.recorder.stop.return_value = 'C:/Videos/lecture.mkv'
        patches = [patch.object(integrations,'check_recorder'), patch.object(integrations,'browser_target'),
                   patch.object(integrations,'FFmpegRecorder',return_value=self.recorder),
                   patch.object(integrations,'_join_telemost',return_value=(self.p,self.context,self.page,self.meeting)),
                   patch.object(integrations,'datetime'),patch.object(integrations,'_wait',side_effect=self.advance)]
        self.preflight,self.target,self.factory,self.join,self.clock,self.wait = [item.start() for item in patches]
        for item in patches:self.addCleanup(item.stop)
        self.clock.now.side_effect = lambda tz=None:self.now
    def advance(self,event,seconds):self.now+=timedelta(seconds=seconds)
    def cleaned(self):
        self.context.close.assert_called_once();self.p.stop.assert_called_once()
    def test_recording_starts_after_join_and_finishes_before_browser_closes(self):
        events=[]
        self.join.side_effect=lambda *a: events.append('join') or (self.p,self.context,self.page,self.meeting)
        self.recorder.start.side_effect=lambda *_:events.append('start')
        self.recorder.stop.side_effect=lambda **_:events.append('stop') or 'C:/Videos/lecture.mkv'
        self.context.close.side_effect=lambda:events.append('close')
        self.assertEqual(integrations.run_lecture(self.lecture),'C:/Videos/lecture.mkv')
        self.assertEqual(events,['join','start','stop','close']);self.cleaned()
    def test_missing_ffmpeg_does_not_open_meeting(self):
        self.preflight.side_effect=RuntimeError('FFmpeg unavailable')
        with self.assertRaisesRegex(RuntimeError,'unavailable'):integrations.run_lecture(self.lecture)
        self.join.assert_not_called();self.factory.assert_not_called()
    def test_failed_join_does_not_start_recorder(self):
        self.join.side_effect=RuntimeError('join failed')
        with self.assertRaisesRegex(RuntimeError,'join failed'):integrations.run_lecture(self.lecture)
        self.factory.assert_not_called()
    def test_cancellation_stops_and_preserves_cancelled_result(self):
        event=threading.Event()
        self.recorder.check.side_effect=lambda:event.set()
        with self.assertRaises(integrations.LectureCancelled):integrations.run_lecture(self.lecture,cancel_event=event)
        self.recorder.stop.assert_called_once();self.cleaned()
    def test_cancel_before_launch_never_checks_or_opens(self):
        event=threading.Event();event.set()
        with self.assertRaises(integrations.LectureCancelled):integrations.run_lecture(self.lecture,cancel_event=event)
        self.preflight.assert_not_called();self.join.assert_not_called()
    def test_cancelled_start_is_not_success(self):
        self.recorder.start.side_effect=recording.RecordingCancelled('cancel')
        statuses=[]
        with self.assertRaises(recording.RecordingCancelled):integrations.run_lecture(self.lecture,status_callback=statuses.append)
        self.recorder.stop.assert_called_once_with(validate=False)
        self.assertFalse(any(message.startswith('Запись сохранена') for message in statuses))
        self.cleaned()
    def test_failed_save_overrides_cancelled_result(self):
        event=threading.Event();self.recorder.check.side_effect=lambda:event.set()
        self.recorder.stop.side_effect=RuntimeError('disk full')
        with self.assertRaisesRegex(RuntimeError,'disk full'):integrations.run_lecture(self.lecture,cancel_event=event)
        self.cleaned()
    def test_short_cancelled_recording_publishes_discard_reason_and_cleans_up(self):
        event = threading.Event()
        self.recorder.check.side_effect = lambda: event.set()
        self.recorder.stop.return_value = None
        self.recorder.recording_note = 'Видео не сохранено: меньше 5 минут.'
        self.recorder.result.side_effect = [
            {'status': 'partial', 'path': 'temporary.mkv'},
            {'status': 'discarded', 'path': None, 'duration_seconds': 1, 'note': self.recorder.recording_note},
        ]
        statuses, results = [], []
        with self.assertRaises(integrations.LectureCancelled):
            integrations.run_lecture(self.lecture, cancel_event=event, status_callback=statuses.append,
                                     recording_callback=results.append)
        self.assertEqual(results[-1]['status'], 'discarded')
        self.assertIn(self.recorder.recording_note, statuses)
        self.assertFalse(any(str(message).startswith('Запись сохранена') for message in statuses))
        self.cleaned()
    def test_recorder_failure_is_not_reported_success(self):
        self.recorder.check.side_effect=RuntimeError('audio died');statuses=[]
        with self.assertRaisesRegex(RuntimeError,'audio died'):integrations.run_lecture(self.lecture,status_callback=statuses.append)
        self.recorder.stop.assert_called_once();self.cleaned()
    def test_disconnect_stops_recording(self):
        self.page.is_closed.return_value=True
        with self.assertRaisesRegex(RuntimeError,'Соединение'):integrations.run_lecture(self.lecture)
        self.recorder.stop.assert_called_once();self.cleaned()
    def test_expired_lecture_never_launches(self):
        self.now=self.lecture.ends_at
        with self.assertRaisesRegex(RuntimeError,'уже прошло'):integrations.run_lecture(self.lecture)
        self.preflight.assert_not_called()
    def test_visibility_changes_are_reported_and_finally_cleared(self):
        callback = Mock()
        with patch.object(integrations, 'target_minimized', side_effect=[False, True, True, False]):
            integrations.run_lecture(self.lecture, visibility_callback=callback)
        self.assertEqual([call.args[0] for call in callback.call_args_list], [False, True, True, False, False])
        self.assertEqual(callback.call_args.args, (False, None))
        self.cleaned()

    def test_broken_alert_callback_does_not_skip_saving_and_cleanup(self):
        callback = Mock(side_effect=RuntimeError('alert unavailable'))
        with patch.object(integrations, 'target_minimized', return_value=True), \
                self.assertLogs('integrations', level='ERROR'):
            integrations.run_lecture(self.lecture, visibility_callback=callback)
        self.recorder.stop.assert_called_once_with(validate=True)
        self.cleaned()
    def test_browser_cleanup_error_never_skips_playwright_cleanup(self):
        self.context.close.side_effect=RuntimeError('close failed')
        with self.assertRaisesRegex(RuntimeError,'close failed'):integrations.run_lecture(self.lecture)
        self.p.stop.assert_called_once()

class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.recorder=recording.FFmpegRecorder(recording.BrowserTarget(7,12345),{'recordings_dir':self.temp.name},7)
    def test_command_has_exact_window_and_process_audio_pipe_not_desktop_or_mic(self):
        cmd=recording.recording_command('ffmpeg','tcp://127.0.0.1:12345',self.directory/'a.mkv')
        self.assertIn('tcp://127.0.0.1:12345',cmd);self.assertIn('rawvideo',cmd);self.assertIn('pipe:0',cmd)
        self.assertNotIn('desktop',cmd);self.assertNotIn('dshow',cmd)
        self.assertIn('-n',cmd);self.assertIn('-shortest',cmd)
    def test_output_paths_do_not_overwrite_other_recordings(self):
        second=recording.FFmpegRecorder(recording.BrowserTarget(7,12345),{'recordings_dir':self.temp.name},7)
        self.assertNotEqual(self.recorder.output,second.output)
    def test_recordings_in_source_are_rejected(self):
        with self.assertRaises(ValueError):recording.recordings_directory(str(recording.SOURCE_DIR/'recordings'))
    def test_audio_overflow_is_detected(self):
        import queue
        self.recorder._queue=queue.Queue(maxsize=1)
        self.recorder._on_audio(b'x',1);self.recorder._on_audio(b'y',1)
        with self.assertRaisesRegex(RuntimeError,'не успевает'):self.recorder.check()
    def test_audio_process_failure_is_detected(self):
        self.recorder.process=Mock();self.recorder.process.poll.return_value=None
        self.recorder.audio=Mock(is_running=False)
        with self.assertRaisesRegex(RuntimeError,'звука'):self.recorder.check()
    def test_minimized_video_is_black_and_restores_only_fresh_frames(self):
        self.recorder._video_frame = b'old slide'
        with patch.object(recording, 'target_minimized', return_value=True):
            frame = self.recorder._output_frame()
        self.assertEqual(len(frame), 1920 * 1080 * 4)
        self.assertFalse(any(frame))
        with patch.object(recording, 'target_minimized', return_value=False):
            self.assertEqual(self.recorder._output_frame(), frame)
            self.recorder._video_frame = b'fresh slide'
            self.assertEqual(self.recorder._output_frame(), b'fresh slide')

    def test_recording_can_start_when_minimized_without_wgc_frames(self):
        capture = Mock()
        capture.start_free_threaded.return_value.is_finished.return_value = False
        self.recorder._first_frame = Mock()
        self.recorder._first_frame.wait.return_value = False
        with patch('windows_capture.WindowsCapture', return_value=capture), \
                patch.object(recording, 'target_minimized', return_value=True):
            self.recorder._start_video(None)
        capture.start_free_threaded.assert_called_once()
        self.assertIsNone(self.recorder._error)

    def test_minimized_start_still_detects_real_capture_failure(self):
        capture = Mock()
        capture.start_free_threaded.return_value.is_finished.return_value = True
        self.recorder._first_frame = Mock()
        self.recorder._first_frame.wait.return_value = False
        with patch('windows_capture.WindowsCapture', return_value=capture), \
                patch.object(recording, 'target_minimized', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'не передал изображение'):
                self.recorder._start_video(None)
    def test_same_frame_progress_does_not_hide_stalled_encoder(self):
        self.recorder.process=Mock()
        self.recorder.process.stdout=[b'frame=1\n',b'frame=1\n',b'frame=1\n']
        with patch.object(recording.time,'monotonic',side_effect=[123]):
            self.recorder._read_progress()
        self.assertEqual(self.recorder._frame_count,1)
        self.assertEqual(self.recorder._last_frame,123)
    def test_abnormal_ffmpeg_exit_is_detected(self):
        self.recorder.process=Mock();self.recorder.process.poll.return_value=1
        with self.assertRaisesRegex(RuntimeError,'FFmpeg завершился'):self.recorder.check()
    def test_missing_or_unreadable_tracks_fail_validation(self):
        with self.assertRaisesRegex(RuntimeError,'отсутствует'):self.recorder.stop()
        self.recorder.output.write_bytes(b'not media')
        with patch.object(recording,'_run_command',return_value=Mock(returncode=1,stderr='missing audio')):
            with self.assertRaisesRegex(RuntimeError,'аудиодорожки'):self.recorder.stop()
    def test_stop_validates_both_tracks(self):
        self.recorder.output.write_bytes(b'media placeholder')
        with patch.object(recording,'_run_command',return_value=Mock(returncode=0)) as command:
            self.assertEqual(self.recorder.stop(),str(self.recorder.output))
        self.assertIn('0:a:0',command.call_args.args[0]);self.assertIn('0:v:0',command.call_args.args[0])
    def test_failed_process_is_never_success(self):
        self.recorder.output.write_bytes(b'partial')
        process=Mock(returncode=1);self.recorder.process=process
        process.stdin.closed=process.stdout.closed=process.stderr.closed=True
        with self.assertRaisesRegex(RuntimeError,'ошибкой'):self.recorder.stop()
    def test_hanging_process_is_terminated_and_reported(self):
        process=Mock(returncode=1);self.recorder.process=process
        process.wait.side_effect=[subprocess.TimeoutExpired('ffmpeg',15),None]
        process.stdin.closed=process.stdout.closed=process.stderr.closed=True
        with self.assertRaisesRegex(RuntimeError,'не завершился штатно'):self.recorder.stop()
        process.terminate.assert_called_once()

class WindowTargetTests(unittest.TestCase):
    def setUp(self):
        self.target = recording.BrowserTarget(7, 12345)
        self.user = Mock()
        self.user.IsWindow.return_value = True
        self.user.IsWindowVisible.return_value = True
        self.user.IsIconic.return_value = True
        self.user.OpenInputDesktop.return_value = 456
        self.user.GetWindowThreadProcessId.side_effect = lambda hwnd, owner: setattr(owner._obj, 'value', 7)
        def desktop_name(handle, index, buffer, size, required):
            buffer.value = 'Default'
            return True
        self.user.GetUserObjectInformationW.side_effect = desktop_name
        patcher = patch.object(recording.ctypes, 'WinDLL', return_value=self.user)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_minimized_window_remains_valid(self):
        recording.check_target(self.target)
        self.assertTrue(recording.target_minimized(self.target))
        self.user.CloseDesktop.assert_called_once_with(456)

    def test_closed_window_and_changed_owner_still_fail(self):
        self.user.IsWindow.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'закрыто'):
            recording.check_target(self.target)
        self.user.IsWindow.return_value = True
        self.user.GetWindowThreadProcessId.side_effect = lambda hwnd, owner: setattr(owner._obj, 'value', 8)
        with self.assertRaisesRegex(RuntimeError, 'не принадлежит'):
            recording.check_target(self.target)

    def test_restore_requires_verified_target_and_never_restores_another_process(self):
        self.user.IsIconic.return_value = False
        recording.restore_target(self.target)
        self.user.ShowWindow.assert_called_once_with(12345, 9)
        self.user.SetForegroundWindow.assert_called_once_with(12345)
        self.user.ShowWindow.reset_mock()
        self.user.GetWindowThreadProcessId.side_effect = lambda hwnd, owner: setattr(owner._obj, 'value', 8)
        with self.assertRaisesRegex(RuntimeError, 'не принадлежит'):
            recording.restore_target(self.target)
        self.user.ShowWindow.assert_not_called()

    def test_locked_desktop_still_fails_and_releases_handle(self):
        def locked_name(handle, index, buffer, size, required):
            buffer.value = 'Winlogon'
            return True
        self.user.GetUserObjectInformationW.side_effect = locked_name
        with self.assertRaisesRegex(RuntimeError, 'заблокирована'):
            recording.check_target(self.target)
        self.user.CloseDesktop.assert_called_once_with(456)


if __name__=='__main__':unittest.main()
