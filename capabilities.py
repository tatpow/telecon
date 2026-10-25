"""Explicit platform support; unsupported Windows features never block a join."""
import sys


def desktop_capabilities(platform: str | None = None) -> dict:
    platform = platform or sys.platform
    windows = platform == "win32"
    return {"platform": platform, "recording": windows,
            "prevent_sleep": windows, "hidden_window_alerts": windows}
