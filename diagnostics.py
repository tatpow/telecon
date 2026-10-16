"""Opt-in packaged-runtime check using disposable data and synthetic media."""
from __future__ import annotations

import json
import logging
import math
import struct
import subprocess
import tempfile
import threading
import traceback
from pathlib import Path


def run_self_check(report_path: Path) -> int:
    report = {"ok": False, "checks": {}}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    log_handler = logging.FileHandler(report_path.with_suffix(".log"), encoding="utf-8")
    logging.getLogger("pywebview").addHandler(log_handler)
    try:
        from app import DesktopApi
        from integrations import _start_playwright, _mute_devices_stably
        from power import SleepInhibitor
        from recording import FFmpegRecorder, browser_target, check_recorder
        from storage import LectureStore

        with tempfile.TemporaryDirectory(prefix="telecon-build-check-", ignore_cleanup_errors=True) as temporary:
            directory = Path(temporary)
            config = {"recordings_dir": str(directory / "recordings")}
            report["recorder"] = check_recorder(config)
            report["checks"]["ffmpeg_and_native_modules"] = True
            guard = SleepInhibitor()
            try:
                guard.set_active(True)
                assert guard.active
            finally:
                guard.close()
            report["checks"]["power_request_released"] = not guard.active

            playwright = _start_playwright()
            context = None
            recorder = None
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(directory / "profile"), headless=False,
                    viewport={"width": 960, "height": 640},
                    args=["--autoplay-policy=no-user-gesture-required"],
                )
                page = context.pages[0]
                page.set_content("""<button data-testid="turn-off-mic-button"
                    onclick="this.dataset.testid='turn-on-mic-button'">Mic</button>
                    <button data-testid="turn-off-camera-button"
                    onclick="this.dataset.testid='turn-on-camera-button'">Camera</button>
                    <div role="dialog" style="position:fixed;inset:0;background:#222;color:white">
                    <h2>Включить видео не удалось</h2><button onclick="this.parentElement.remove()">Понятно</button></div>""")
                _mute_devices_stably(page)
                assert page.locator("[data-testid='turn-on-mic-button']").is_visible()
                assert page.locator("[data-testid='turn-on-camera-button']").is_visible()
                assert not page.get_by_role("dialog").is_visible()
                report["checks"]["media_error_recovery"] = True
                page.set_content("""<title>Telecon build check</title>
                    <body style="background:#235185;color:white;font:32px Arial">
                    <h1>Telecon build check</h1><p>Synthetic picture and test sound</p><div id="counter"></div>
                    <script>let t=0;setInterval(()=>document.getElementById('counter').textContent=++t,100);
                    let a=new AudioContext(),o=a.createOscillator(),g=a.createGain();
                    o.frequency.value=440;g.gain.value=.02;o.connect(g).connect(a.destination);o.start();</script>""")
                page.wait_for_timeout(500)
                report["checks"]["bundled_chromium_launch"] = True
                recorder = FFmpegRecorder(browser_target(directory / "profile"), config, 0)
                recorder.start()
                for _ in range(6):
                    page.wait_for_timeout(500)
                    recorder.check()
                output = recorder.stop()
                executable = recorder.executable
                recorder = None
                audio = subprocess.run(
                    [executable, "-v", "error", "-i", output, "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"],
                    capture_output=True, check=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
                ).stdout
                samples = struct.unpack(f"<{len(audio)//4}f", audio)
                rms = math.sqrt(sum(sample*sample for sample in samples)/len(samples))
                assert rms > .001, "Recorded audio is silent"
                pixel = subprocess.run(
                    [executable, "-v", "error", "-i", output, "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                    capture_output=True, check=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
                ).stdout
                assert len(pixel) == 3 and sum(pixel) > 30 and pixel[2] > pixel[0]*1.5, "Recorded picture is missing or incorrect"
                report["media"] = {"bytes": Path(output).stat().st_size, "audio_rms": rms, "mean_rgb": list(pixel)}
                report["checks"]["window_video_and_process_audio"] = True
            finally:
                try:
                    if recorder is not None:
                        recorder.stop(validate=False)
                finally:
                    try:
                        if context is not None:
                            context.close()
                    finally:
                        playwright.stop()

            import webview
            called = threading.Event()

            class DiagnosticApi(DesktopApi):
                def get_state(self):
                    result = super().get_state()
                    if result["ok"]:
                        called.set()
                    return result

            api = DiagnosticApi(LectureStore(directory / "lectures.sqlite3"))
            ui = Path(__file__).resolve().parent / "ui" / "index.html"
            assert ui.is_file(), "Packaged UI files are missing"
            window = webview.create_window("Телекон — проверка билда", str(ui), js_api=api,
                                           width=1000, height=760, background_color="#171819")
            def wait_for_bridge():
                report["checks"]["native_ui_js_bridge"] = called.wait(20)
                window.destroy()
            webview.start(wait_for_bridge, http_server=True, private_mode=True)
            assert report["checks"]["native_ui_js_bridge"], "UI did not call the Python bridge"
            report["ok"] = all(report["checks"].values())
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.getLogger("pywebview").removeHandler(log_handler)
    log_handler.close()
    return 0 if report["ok"] else 1
