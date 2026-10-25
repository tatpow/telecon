import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock

from app import DesktopApi
from storage import Lecture, LectureStore


class OptionalRecordingTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from capabilities import desktop_capabilities
        capabilities = patch('app.desktop_capabilities', return_value=desktop_capabilities('win32'))
        capabilities.start()
        self.addCleanup(capabilities.stop)
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store=LectureStore(Path(temporary.name)/'calendar.sqlite3')
        self.api=DesktopApi(self.store)

    def test_fresh_and_existing_databases_default_to_connection_only(self):
        self.assertFalse(self.api.get_settings()['data']['recording_enabled'])
        self.store.save_settings({'recording_quality':'compact','recording_fps':7})
        restored=DesktopApi(LectureStore(self.store.db_path))
        self.assertFalse(restored.get_settings()['data']['recording_enabled'])
        self.assertEqual(restored.get_settings()['data']['recording_fps'],7)

    def test_toggle_persists_without_erasing_video_preferences(self):
        for enabled in (True,False):
            self.assertTrue(self.api.save_settings({'recording_enabled':enabled})['ok'])
            self.assertEqual(DesktopApi(LectureStore(self.store.db_path)).get_settings()['data']['recording_enabled'],enabled)
        for value in (1,'true',None,[]):
            self.assertFalse(self.api.save_settings({'recording_enabled':value})['ok'])

    def test_changing_toggle_during_a_session_applies_only_to_next_session(self):
        entered,leave=threading.Event(),threading.Event()
        captured=[]
        def runner(lecture,config,**kwargs):
            captured.append(config['recording_enabled'])
            entered.set()
            leave.wait(3)
        api=DesktopApi(self.store,event_runner=runner,sleep_inhibitor=Mock(active=False))
        api.save_settings({'recording_enabled':True})
        now=datetime.now()
        lecture=Lecture(1,'Synthetic','telemost.yandex.ru/j/test',now,now+timedelta(minutes=1))
        worker=threading.Thread(target=api._run,args=(lecture,threading.Event()))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            api.save_settings({'recording_enabled':False})
            self.assertTrue(api._session_recording)
            self.assertEqual(captured,[True])
        finally:
            leave.set()
            worker.join(3)
        self.assertFalse(api._session_recording)
