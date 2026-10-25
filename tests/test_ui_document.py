import re
import tempfile
import unittest
from pathlib import Path

from ui_document import inline_ui_document, ui_window_content

ROOT = Path(__file__).resolve().parents[1]


class UiDocumentTests(unittest.TestCase):
    def test_other_platforms_keep_the_original_file_and_http_mode(self):
        entry = Path("unchanged/index.html")
        for platform in ("win32", "linux"):
            self.assertEqual(ui_window_content(entry, platform), ({"url": str(entry)}, True))

    def test_cocoa_document_has_fresh_nonce_and_preserves_script_order_after_dom(self):
        content, http = ui_window_content(ROOT / "ui" / "index.html", "darwin")
        self.assertFalse(http)
        self.assertEqual(set(content), {"html"})
        document = content["html"]
        nonces = re.findall(r'nonce="([^"]+)"', document)
        self.assertEqual(len(nonces), 3)
        self.assertEqual(len(set(nonces)), 1)
        self.assertEqual(document.count("'nonce-" + nonces[0] + "'"), 2)
        self.assertNotIn("'unsafe-inline'", document)
        self.assertNotIn('<script src="', document)
        self.assertNotIn('<link rel="stylesheet"', document)
        motion = document.index('data-bundled-source="motion.js"')
        app = document.index('data-bundled-source="app.js"')
        self.assertLess(document.index('id="toast"'), motion)
        self.assertLess(motion, app)
        self.assertNotIn('nonce="' + nonces[0] + '"', inline_ui_document(ROOT / "ui" / "index.html"))

    def test_embedded_assets_cannot_close_their_html_elements(self):
        with tempfile.TemporaryDirectory() as temporary:
            entry = Path(temporary) / "index.html"
            entry.write_text((ROOT / "ui" / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
            (entry.parent / "styles.css").write_text('p:after{content:"</StYle>"}', encoding="utf-8")
            for name in ("motion.js", "app.js"):
                (entry.parent / name).write_text("const value = '</ScRiPt>';", encoding="utf-8")
            document = inline_ui_document(entry)
            self.assertNotIn("</ScRiPt>", document)
            self.assertIn(r"<\/ScRiPt>", document)
            self.assertIn(r"<\/StYle>", document)

    def test_changed_asset_references_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            entry = Path(temporary) / "index.html"
            entry.write_text("<html>unsupported layout</html>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unexpected UI document"):
                inline_ui_document(entry)
