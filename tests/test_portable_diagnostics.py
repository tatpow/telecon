import threading
import unittest
import io
import json
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from portable_diagnostics import probe_ui_http, prepare_smoke_window, run_portable_check


class LocalUiProbeTests(unittest.TestCase):
    def test_http_probe_waits_for_listening_before_validating_the_document(self):
        refused, ready = Mock(), Mock()
        refused.request.side_effect = ConnectionRefusedError("server is not listening yet")
        response = ready.getresponse.return_value
        response.status = 200
        response.read.return_value = b'<html lang="ru"><script src="app.js"></script></html>'
        response.headers.get_content_type.return_value = "text/html"
        with (patch("portable_diagnostics.HTTPConnection", side_effect=[refused, ready]) as factory,
              patch("portable_diagnostics.time.monotonic", return_value=0),
              patch("portable_diagnostics.time.sleep")):
            result = probe_ui_http("http://127.0.0.1:15600/index.html", startup_timeout=1)
        self.assertEqual(result, {"status": 200, "content_type": "text/html"})
        self.assertEqual(factory.call_count, 2)
        refused.close.assert_called_once()
        ready.close.assert_called_once()

    def test_http_probe_still_fails_if_server_never_starts(self):
        connection = Mock()
        connection.request.side_effect = ConnectionRefusedError("server never started")
        with (patch("portable_diagnostics.HTTPConnection", return_value=connection) as factory,
              patch("portable_diagnostics.time.monotonic", side_effect=[0, .5, 1]),
              patch("portable_diagnostics.time.sleep") as sleep):
            with self.assertRaisesRegex(ConnectionRefusedError, "never started"):
                probe_ui_http("http://127.0.0.1:15600/index.html", startup_timeout=1)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(connection.close.call_count, 2)
        sleep.assert_called_once()

    def test_http_startup_wait_does_not_retry_other_transport_errors(self):
        for error in (ConnectionResetError("reset"), TimeoutError("timeout")):
            with self.subTest(error=type(error).__name__):
                connection = Mock()
                connection.request.side_effect = error
                with patch("portable_diagnostics.HTTPConnection", return_value=connection) as factory:
                    with self.assertRaises(type(error)):
                        probe_ui_http("http://127.0.0.1:15600/index.html", startup_timeout=15)
                factory.assert_called_once()
                connection.close.assert_called_once()

    def test_visible_smoke_window_has_no_delayed_open_that_can_undo_minimize(self):
        pending = []
        tray = SimpleNamespace(state=lambda: {"window_visible": True}, invoke_menu=pending.append)
        def wait_for(predicate, description):
            self.assertTrue(predicate(), description)
        prepare_smoke_window(tray, wait_for, initially_hidden=False)
        self.assertEqual(pending, [])

    def test_hidden_smoke_window_must_open_before_minimize(self):
        visible, pending = False, []
        tray = SimpleNamespace(state=lambda: {"window_visible": visible}, invoke_menu=pending.append)
        def wait_for(predicate, description):
            nonlocal visible
            self.assertFalse(predicate())
            self.assertEqual(pending.pop(), 0)
            visible = True
            self.assertTrue(predicate(), description)
        prepare_smoke_window(tray, wait_for, initially_hidden=True)
        self.assertEqual(pending, [])

    def test_failed_portable_check_prints_the_saved_diagnostic_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            console = io.StringIO()
            with patch("storage.LectureStore", side_effect=RuntimeError("synthetic calendar failure")), redirect_stderr(console):
                self.assertEqual(run_portable_check(path), 1)
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(console.getvalue()), report)
            self.assertFalse(report["ok"])
            self.assertIn("RuntimeError: synthetic calendar failure", report["errors"])

    def test_loopback_probe_checks_the_actual_http_document(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/not-allowed")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'<html lang="ru"><script src="app.js"></script></html>'
                                 if self.path == "/index.html" else b"unrelated response")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(probe_ui_http(base + "/index.html"), {"status": 200, "content_type": "text/html"})
            with self.assertRaisesRegex(RuntimeError, "bundled UI"):
                probe_ui_http(base + "/wrong.html", startup_timeout=15)
            with self.assertRaisesRegex(RuntimeError, "bundled UI"):
                probe_ui_http(base + "/redirect", startup_timeout=15)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_probe_refuses_non_loopback_addresses_before_connecting(self):
        with patch("portable_diagnostics.HTTPConnection") as connection:
            for url in ("https://example.org/", "http://192.168.1.1/", "file:///index.html",
                        "http://user:password@127.0.0.1/", "http://127.0.0.1.example.org/"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    probe_ui_http(url)
            connection.assert_not_called()
