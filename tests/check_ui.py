"""Headless UI regressions on synthetic data; not a native OS/recording check."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import DesktopApi
from capabilities import desktop_capabilities
from integrations import _start_playwright
from storage import Lecture, LectureStore
from video_settings import resolve_video_settings
from ui_document import inline_ui_document


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
        self.api = api
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
        methods = ["get_state", "get_settings", "save_settings", "get_recording_options", "preview_recording_name"]
        self.page.expose_function("testBridge", lambda method, args: getattr(api, method)(*args))
        self.page.add_init_script("window.pywebview={api:{}};for(const m of " + json.dumps(methods) +
                                  "){window.pywebview.api[m]=(...args)=>window.testBridge(m,args);}")
        self.page.goto((ROOT / "ui" / "index.html").as_uri())
        self.page.wait_for_function("()=>ui.ready&&!ui.refreshing")

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def test_cocoa_document_runs_offline_and_blocks_untrusted_inline_content(self):
        document = inline_ui_document(ROOT / "ui" / "index.html")
        document = document.replace("</head>", "<style>body{display:none}</style></head>")
        document = document.replace("</body>", "<script>window.untrustedScriptRan=true</script></body>")
        requests = []
        self.page.on("request", lambda request: requests.append(request.resource_type))
        self.page.route("http://127.0.0.1:43124/**", lambda route: route.fulfill(content_type="text/html", body=document))
        self.page.goto("http://127.0.0.1:43124/")
        self.page.wait_for_function("()=>ui.ready&&!ui.refreshing")
        self.assertEqual(self.page.locator(".lecture-card").count(), 1)
        self.assertTrue(self.page.locator("#page-title").is_visible())
        self.assertFalse(self.page.evaluate("Boolean(window.untrustedScriptRan)"))
        self.assertEqual(self.page.evaluate("typeof motion.open"), "function")
        self.assertEqual(self.page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--frost').trim()"), "#E6F1FA")
        self.assertNotIn("script", requests)
        self.assertNotIn("stylesheet", requests)

    def test_tray_switches_save_and_keyboard_operation_keeps_footer_accessible(self):
        page = self.page
        page.locator("#open-settings").click()
        page.wait_for_function("()=>document.querySelector('#settings-dialog').open")
        for name in ("minimize_to_tray", "close_to_tray"):
            control = page.locator(f'input[name="{name}"]')
            self.assertFalse(control.is_checked())
            self.assertEqual(control.get_attribute("role"), "switch")
            control.focus()
            page.keyboard.press("Space")
            self.assertTrue(control.is_checked())
        self.assertTrue(page.locator("#tray-status").is_visible())
        page.locator('#settings-form button[type="submit"]').click()
        page.wait_for_function("()=>!document.querySelector('#settings-dialog').open")
        self.assertTrue(self.store.get_settings()["minimize_to_tray"])
        self.assertTrue(self.store.get_settings()["close_to_tray"])
        page.locator("#open-settings").click()
        page.wait_for_function("()=>document.querySelector('#settings-dialog').open")
        self.assertTrue(page.locator('input[name="close_to_tray"]').is_checked())
        for width in (1220, 820, 390):
            page.set_viewport_size({"width": width, "height": 820})
            footer = page.locator('#settings-form button[type="submit"]')
            self.assertTrue(footer.is_visible())
            box = footer.bounding_box()
            self.assertLessEqual(box["y"] + box["height"], 820)
        page.set_viewport_size({"width": 1220, "height": 820})
        page.locator("#panel-general").evaluate("panel=>panel.scrollTop=150")
        page.screenshot(path=str(ROOT / ".qa" / "tray-settings-desktop.png"))

    def test_csp_allows_bridge_functions_but_blocks_inline_scripts_and_styles(self):
        # Ordinary page scripts enforce CSP, unlike DevTools page.evaluate.
        # Serve the real UI under a routed local origin without opening a server.
        probe = """
        window.bridgeProbe = {};
        try { window.bridgeProbe.dynamic = new Function('return 42')(); }
        catch (error) { window.bridgeProbe.error = String(error); }
        const style = document.createElement('style');
        style.textContent = '#bridge-style-probe { width: 37px; }';
        document.head.append(style);
        const box = document.createElement('div');
        box.id = 'bridge-style-probe'; document.body.append(box);
        window.bridgeProbe.width = getComputedStyle(box).width;
        const script = document.createElement('script');
        script.textContent = 'window.inlineScriptExecuted = true';
        document.head.append(script);
        """
        def serve(route):
            path = urlsplit(route.request.url).path.lstrip("/") or "index.html"
            if path == "bridge-csp-probe.js":
                route.fulfill(content_type="application/javascript", body=probe)
            elif path in {"index.html", "app.js", "motion.js", "styles.css"}:
                content_type = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}[Path(path).suffix]
                body = (ROOT / "ui" / path).read_text(encoding="utf-8")
                if path == "index.html":
                    body = body.replace("</body>", '<script src="bridge-csp-probe.js"></script></body>')
                route.fulfill(content_type=content_type, body=body)
            else:
                route.fulfill(status=404, body="not found")
        self.page.route("http://127.0.0.1:43123/**", serve)
        self.page.goto("http://127.0.0.1:43123/")
        result = self.page.evaluate("window.bridgeProbe")
        self.assertEqual(result.get("dynamic"), 42, result)
        self.assertNotEqual(result["width"], "37px")
        self.assertFalse(self.page.evaluate("Boolean(window.inlineScriptExecuted)"))

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
