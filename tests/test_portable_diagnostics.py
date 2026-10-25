import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from portable_diagnostics import probe_ui_http


class LocalUiProbeTests(unittest.TestCase):
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
                probe_ui_http(base + "/wrong.html")
            with self.assertRaisesRegex(RuntimeError, "bundled UI"):
                probe_ui_http(base + "/redirect")
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
