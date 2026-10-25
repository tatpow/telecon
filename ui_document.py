"""Load the same local interface without an HTTP dependency on Cocoa."""
from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path


def _replace_once(document: str, expected: str, replacement: str) -> str:
    if document.count(expected) != 1:
        raise ValueError(f"Unexpected UI document layout: {expected}")
    return document.replace(expected, replacement, 1)


def inline_ui_document(entry: Path) -> str:
    """Embed only bundled assets, authorized by a fresh per-document CSP nonce.

    The scripts execute at the end of body, after their DOM exists, in the same
    motion/app order as the original deferred external scripts. Never interpolate
    lecture data or permit arbitrary inline content.
    """
    document = entry.read_text(encoding="utf-8")
    nonce = secrets.token_urlsafe(24)
    document = _replace_once(document, "style-src 'self'", f"style-src 'self' 'nonce-{nonce}'")
    document = _replace_once(document, "script-src 'self' 'unsafe-eval'",
                             f"script-src 'self' 'unsafe-eval' 'nonce-{nonce}'")
    css = (entry.parent / "styles.css").read_text(encoding="utf-8")
    css = re.sub(r"</(?=style)", r"<\\/", css, flags=re.IGNORECASE)
    document = _replace_once(document, '<link rel="stylesheet" href="styles.css">',
                             f'<style nonce="{nonce}">\n{css}\n</style>')
    scripts = []
    for name in ("motion.js", "app.js"):
        document = _replace_once(document, f'<script src="{name}" defer></script>', "")
        script = (entry.parent / name).read_text(encoding="utf-8")
        script = re.sub(r"</(?=script)", r"<\\/", script, flags=re.IGNORECASE)
        scripts.append(f'<script nonce="{nonce}" data-bundled-source="{name}">\n{script}\n</script>')
    return _replace_once(document, "</body>", "\n".join(scripts) + "\n</body>")


def ui_window_content(entry: Path, platform: str | None = None) -> tuple[dict, bool]:
    """Return create_window content and whether pywebview should start HTTP."""
    if (platform or sys.platform) == "darwin":
        return {"html": inline_ui_document(entry)}, False
    return {"url": str(entry)}, True
