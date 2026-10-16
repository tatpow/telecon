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

if __name__=='__main__':unittest.main()
