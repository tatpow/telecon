"""Headless UI regressions on synthetic data; not a native OS/recording check."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import DesktopApi
from capabilities import desktop_capabilities
from integrations import _start_playwright
from storage import Lecture, LectureStore
from video_settings import resolve_video_settings


class BrowserRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="telecon-ui-test-")
        self.addCleanup(temporary.cleanup)
        self.store = LectureStore(Path(temporary.name) / "calendar.sqlite3")
        start = datetime.now() + timedelta(days=1)
        self.store.add(Lecture(None, "Demonstration lecture", "telemost.yandex.ru/j/synthetic-test",
                               start, start + timedelta(minutes=90)))
        with patch("app.desktop_capabilities", return_value=desktop_capabilities("win32")):
            api = DesktopApi(self.store, sleep_inhibitor=Mock(active=False),
                             alert_backend=Mock(window=None, audio_error=""))
        self.addCleanup(api._alerts.close)
        api.get_recording_options = lambda: {"ok": True, "data": {
            "screen_width": 1920, "screen_height": 1080, "max_fps": 60,
            "effective": resolve_video_settings({}, (1920, 1080))}}
        playwright = _start_playwright()
        self.addCleanup(playwright.stop)
        browser = playwright.chromium.launch(headless=True)
        self.addCleanup(browser.close)
        self.page = browser.new_page(viewport={"width": 1220, "height": 820})
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        methods = ["get_state", "get_settings", "get_recording_options", "preview_recording_name"]
        self.page.expose_function("testBridge", lambda method, args: getattr(api, method)(*args))
        self.page.add_init_script("window.pywebview={api:{}};for(const m of " + json.dumps(methods) +
                                  "){window.pywebview.api[m]=(...args)=>window.testBridge(m,args);}")
        self.page.goto((ROOT / "ui" / "index.html").as_uri())
        self.page.wait_for_function("()=>ui.ready&&!ui.refreshing")

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def test_reduced_motion_includes_new_dialog_backdrops(self):
        page = self.page
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_function("()=>motion.reduced.matches&&document.getAnimations().every(a=>a.playState!=='running')")
        page.locator("#quick-connect").click()
        page.wait_for_function("()=>document.querySelector('#quick-dialog').open")
        self.assertEqual(page.evaluate("document.getAnimations().filter(a=>a.playState==='running').length"), 0)
        page.keyboard.press("Escape")
        page.wait_for_function("()=>!document.querySelector('#quick-dialog').open")
        self.assertEqual(page.evaluate("document.activeElement.id"), "quick-connect")

    def test_interrupted_close_cannot_close_a_reopened_dialog(self):
        page = self.page
        page.locator("#quick-connect").click()
        page.wait_for_function("()=>document.querySelector('#quick-dialog').open")
        page.evaluate("()=>{const d=document.querySelector('#quick-dialog');motion.close(d);motion.open(d);}")
        page.wait_for_timeout(280)
        self.assertTrue(page.locator("#quick-dialog").evaluate("d=>d.open"))
        page.keyboard.press("Escape")
        page.wait_for_function("()=>!document.querySelector('#quick-dialog').open")

    def test_polling_does_not_reanimate_unchanged_cards_or_overflow(self):
        page = self.page
        page.wait_for_timeout(280)
        page.evaluate("renderLectures()")
        self.assertEqual(page.locator(".lecture-card").count(), 1)
        self.assertTrue(page.locator(".lecture-card").evaluate("card=>card.getAnimations().length===0"))
        for width in (1536, 820, 390):
            page.set_viewport_size({"width": width, "height": 844})
            page.wait_for_function("()=>document.documentElement.scrollWidth<=innerWidth", timeout=2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
