"""Native connection/UI smoke check for CI; deliberately does not certify recording."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import faulthandler
import time
from urllib.parse import urlsplit
from http.client import HTTPConnection
from datetime import datetime, timedelta, timezone
from pathlib import Path


def probe_ui_http(url: str) -> dict:
    """Read only the local UI page; never contact a remote or proxied origin."""
    target = urlsplit(url)
    if target.scheme != "http" or target.hostname != "127.0.0.1" or target.username or target.password:
        raise ValueError("Native smoke-check requires a loopback HTTP UI URL")
    connection = HTTPConnection(target.hostname, target.port or 80, timeout=5)
    try:
        path = (target.path or "/") + ("?" + target.query if target.query else "")
        connection.request("GET", path)
        response = connection.getresponse()
        content = response.read(1024 * 1024).decode("utf-8")
        if response.status != 200 or '<html lang="ru">' not in content or 'src="app.js"' not in content:
            raise RuntimeError("Local server did not return the bundled UI document")
        return {"status": response.status, "content_type": response.headers.get_content_type()}
    finally:
        connection.close()


def run_portable_check(report_path: Path) -> int:
    report = {"ok": False, "mode": "portable", "recording_checked": False,
              "platform": sys.platform, "checked_at": datetime.now(timezone.utc).isoformat(),
              "checks": {}, "errors": []}
    try:
        from app import DesktopApi, _InstanceLock, _AlreadyRunning
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
            from ui_document import ui_window_content
            ui_path = ui_file()
            assets = {name: (ui_path.parent / name).is_file()
                      for name in ("index.html", "styles.css", "motion.js", "app.js")}
            if not all(assets.values()):
                raise RuntimeError(f"Native UI assets missing: {assets}")
            content, http_server = ui_window_content(ui_path)
            report["native_ui_mode"] = "http" if http_server else "inline"
            called.clear()
            window = webview.create_window("Телекон — проверка интерфейса", **content,
                                           js_api=api, width=1024, height=760, hidden=sys.platform != "darwin",
                                           background_color="#E6F1FA", text_select=True)
            api._alerts.backend.window = window
            from tray import TrayManager
            activation = _InstanceLock(Path(temporary) / "desktop.lock")
            activation.__enter__()
            api._tray = TrayManager(window, lambda: dict(api._config), api._request_close, activation=activation)
            api._tray.attach()

            def wait_for(predicate, description):
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    time.sleep(.05)
                raise AssertionError(description)

            def check_tray():
                tray = api._tray
                state = tray.state()
                assert state["initialized"], state["error"] or "Native tray did not initialize"
                report["tray_available"] = state["available"]
                report["tray_checks"] = {"initialized": True}
                assert state["menu"] == ["Открыть Телекон", "Выйти"], f"Unexpected native menu: {state['menu']}"
                report["tray_checks"]["native_menu_layout"] = True
                # Windows/Linux bridge smoke starts hidden. Exercise minimizing
                # a real shown window, just as normal application startup does.
                tray.invoke_menu(0)
                wait_for(lambda: tray.state()["window_visible"], "Initial native window did not show")
                saved = api.save_settings({"minimize_to_tray": True, "close_to_tray": True})
                assert saved["ok"], saved
                window.minimize()
                if state["available"]:
                    wait_for(lambda: tray.state()["hidden"] and not tray.state()["window_visible"],
                             "Minimize did not hide the actual native window")
                else:
                    time.sleep(.5)
                    assert not tray.state()["hidden"], "Window hidden without an available tray"
                report["tray_checks"]["minimize_or_safe_fallback"] = True
                tray.invoke_menu(0)
                wait_for(lambda: not tray.state()["hidden"] and tray.state()["window_visible"],
                         "Tray Open did not restore the native window")
                report["tray_checks"]["restore"] = True
                window.destroy()  # Same closing event as the window's cross.
                if state["available"]:
                    wait_for(lambda: tray.state()["hidden"] and not tray.state()["window_visible"],
                             "Close did not hide the actual native window")
                else:
                    time.sleep(.5)
                    assert not tray.state()["hidden"]
                assert not api._closing and not window.events.closed.is_set(), "Close stopped the app instead of hiding"
                report["tray_checks"]["close_keeps_app_alive"] = True
                try:
                    with _InstanceLock(Path(temporary) / "desktop.lock"):
                        raise AssertionError("A duplicate instance acquired the app lock")
                except _AlreadyRunning:
                    pass
                wait_for(lambda: not tray.state()["hidden"] and tray.state()["window_visible"],
                         "Second launch did not restore the existing native window")
                report["tray_checks"]["second_launch_restores"] = True

            def check_bridge():
                menu_checked = False
                try:
                    if http_server:
                        report["native_ui_http"] = probe_ui_http(window.real_url)
                    assert called.wait(35), "Native UI did not call the Python bridge"
                    assert window.evaluate_js("typeof motion.open === 'function'")
                    assert window.evaluate_js("document.querySelectorAll('[role=tab]').length") == 3
                    assert window.evaluate_js("getComputedStyle(document.documentElement).getPropertyValue('--frost').trim()") == "#E6F1FA"
                    assert window.evaluate_js("Array.from(document.querySelectorAll('input[type=checkbox]')).every(x=>x.getAttribute('role')==='switch')")
                    check_tray()
                    report["checks"]["native_ui_js_bridge"] = True
                    menu_checked = True
                except Exception as error:
                    report["checks"]["native_ui_js_bridge"] = False
                    report["errors"].append(str(error))
                    report["tray_diagnostics"] = api._tray.state()
                    # Read Python-side events without making another JS bridge
                    # call, which could itself block when bridge setup failed.
                    report["native_ui_diagnostics"] = {
                        "assets": assets,
                        "url": window.real_url,
                        "visible_window": not window.hidden,
                        "initialized": window.events.initialized.is_set(),
                        "before_show": window.events.before_show.is_set(),
                        "shown": window.events.shown.is_set(),
                        "before_load": window.events.before_load.is_set(),
                        "bridge_injected": window.events._pywebviewready.is_set(),
                        "loaded": window.events.loaded.is_set(),
                    }
                    try:
                        report_path.parent.mkdir(parents=True, exist_ok=True)
                        with report_path.with_suffix(".threads.log").open("w", encoding="utf-8") as trace:
                            faulthandler.dump_traceback(file=trace, all_threads=True)
                    except Exception as diagnostic_error:
                        report["diagnostic_error"] = str(diagnostic_error)
                finally:
                    if menu_checked:
                        try:
                            # Invoke the actual native Exit item, not the manager
                            # callback directly. This catches unbound/duplicate
                            # menu entries in the compiled application.
                            api._tray.invoke_menu(1)
                            wait_for(window.events.closed.is_set, "Native tray Exit item did not close the app")
                        except Exception as error:
                            report["checks"]["native_ui_js_bridge"] = False
                            report["errors"].append(str(error))
                            api._tray.exit()
                    else:
                        api._tray.exit()

            try:
                webview.start(check_bridge, gui="qt" if sys.platform == "linux" else None,
                              http_server=http_server, private_mode=True)
            finally:
                api._alerts.close()
                api._sleep.close()
                activation.__exit__()
            report.setdefault("tray_checks", {})["graceful_exit"] = api._closing and window.events.closed.is_set()
            assert report["tray_checks"]["graceful_exit"], "Tray Exit did not finish closing the app"
            report["ok"] = len(report["checks"]) == 4 and all(report["checks"].values())
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
