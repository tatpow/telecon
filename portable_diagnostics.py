"""Native connection/UI smoke check for CI; deliberately does not certify recording."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


def run_portable_check(report_path: Path) -> int:
    report = {"ok": False, "mode": "portable", "recording_checked": False,
              "platform": sys.platform, "checked_at": datetime.now(timezone.utc).isoformat(),
              "checks": {}, "errors": []}
    try:
        from app import DesktopApi
        from storage import LectureStore
        from integrations import _start_playwright
        with tempfile.TemporaryDirectory(prefix="telecon-smoke-") as temporary:
            called = threading.Event()

            class ProbeApi(DesktopApi):
                def get_state(self):
                    result = super().get_state()
                    called.set()
                    return result

            store = LectureStore(Path(temporary) / "calendar.sqlite3")
            api = ProbeApi(store)
            assert api.get_settings()["data"]["recording_enabled"] is False
            report["checks"]["connection_default"] = True
            start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
            saved = api.save_lecture({"title": "CI demonstration", "url": "telemost.yandex.ru/j/synthetic-test",
                                      "starts_at": start.isoformat(),
                                      "ends_at": (start + timedelta(minutes=90)).isoformat(), "lead_minutes": 2})
            assert saved["ok"], saved
            assert len(store.list_all()) == 1
            assert api.delete_lecture({"id": saved["data"]["id"]})["ok"]
            assert not store.list_all()
            report["checks"]["temporary_calendar_crud"] = True
            playwright = _start_playwright()
            try:
                # Validate the full browser shipped for actual meetings. Windows
                # packages intentionally omit Playwright's separate headless shell.
                browser = playwright.chromium.launch(headless=True, channel="chromium")
                try:
                    page = browser.new_page()
                    page.set_content("<title>Telecon smoke fixture</title><p id='fixture'>Local fixture</p>")
                    assert page.locator("#fixture").inner_text() == "Local fixture"
                finally:
                    browser.close()
            finally:
                playwright.stop()
            report["checks"]["chromium_launch"] = True

            import webview
            from app import ui_file
            called.clear()
            window = webview.create_window("Телекон — проверка интерфейса", str(ui_file()),
                                           js_api=api, width=1024, height=760, hidden=True,
                                           background_color="#E6F1FA")
            api._alerts.backend.window = window

            def check_bridge():
                try:
                    assert called.wait(35), "Native UI did not call the Python bridge"
                    assert window.evaluate_js("typeof motion.open === 'function'")
                    assert window.evaluate_js("document.querySelectorAll('[role=tab]').length") == 3
                    assert window.evaluate_js("getComputedStyle(document.documentElement).getPropertyValue('--frost').trim()") == "#E6F1FA"
                    assert window.evaluate_js("Array.from(document.querySelectorAll('input[type=checkbox]')).every(x=>x.getAttribute('role')==='switch')")
                    report["checks"]["native_ui_js_bridge"] = True
                except Exception as error:
                    report["checks"]["native_ui_js_bridge"] = False
                    report["errors"].append(str(error))
                finally:
                    window.destroy()

            try:
                webview.start(check_bridge, gui="qt" if sys.platform == "linux" else None,
                              http_server=True, private_mode=True)
            finally:
                api._alerts.close()
                api._sleep.close()
            report["ok"] = len(report["checks"]) == 4 and all(report["checks"].values())
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
