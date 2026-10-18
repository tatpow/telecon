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
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta
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
        from recording import FFmpegRecorder, browser_target, check_recorder, target_minimized
        from storage import Lecture, LectureStore

        with tempfile.TemporaryDirectory(prefix="telecon-build-check-", ignore_cleanup_errors=True) as temporary:
            directory = Path(temporary)
            config = {"recordings_dir": str(directory / "recordings"),
                      "recording_name_template": "self-check-{date_iso}-{duration}"}
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
                target = browser_target(directory / "profile")
                recorder = FFmpegRecorder(target, config, 0)
                recorder.start()
                for _ in range(6):
                    page.wait_for_timeout(500)
                    recorder.check()
                user = ctypes.WinDLL("user32", use_last_error=True)
                user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                user.ShowWindow(target.hwnd, 6)  # SW_MINIMIZE, only our test window.
                assert target_minimized(target), "Synthetic browser did not minimize"
                for _ in range(8):
                    page.wait_for_timeout(500)
                    recorder.check()
                user.ShowWindow(target.hwnd, 9)  # SW_RESTORE.
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
                pixels = subprocess.run(
                    [executable, "-v", "error", "-i", output, "-an", "-vf", "fps=2,scale=1:1",
                     "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                    capture_output=True, check=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
                ).stdout
                colors = [pixels[index:index+3] for index in range(0, len(pixels), 3)]
                black = [index for index, color in enumerate(colors) if sum(color) < 9]
                assert black, "Minimized video did not become black"
                assert any(sum(color) > 30 for color in colors[:min(black)]), "Initial picture missing"
                assert any(sum(color) > 30 for color in colors[max(black)+1:]), "Picture did not resume"
                offset = int(black[len(black)//2] / 2 * 8000)
                segment = samples[offset:offset+4000]
                minimized_rms = math.sqrt(sum(sample*sample for sample in segment)/len(segment))
                assert minimized_rms > .001, "Process audio stopped while minimized"
                report["media"]["minimized_audio_rms"] = minimized_rms
                report["checks"]["minimized_recording_and_restore"] = True
                assert Path(output).name.startswith("self-check-") and ".partial" not in Path(output).name
                report["checks"]["recording_name_template"] = True

                recorder = FFmpegRecorder(target, dict(config, minimum_recording_minutes=1), 0)
                recorder.start()
                for _ in range(4):
                    page.wait_for_timeout(500)
                    recorder.check()
                assert recorder.stop() is None, "Short recording was saved"
                short_result = recorder.result()
                assert short_result["status"] == "discarded" and short_result["path"] is None
                assert short_result["duration_seconds"] < 60
                recorder = None
                report["checks"]["minimum_recording_duration"] = True

                media_store = LectureStore(directory / "media-check.sqlite3")
                now = datetime.now()
                def media_lecture():
                    return media_store.add(Lecture(None, "Synthetic media check", "telemost.yandex.ru/j/test",
                                                   now, now + timedelta(minutes=1)))
                first = media_lecture()
                media_store.save_recording(first, {"status": "saved", "path": output})
                assert media_store.delete(first) and Path(output).is_file(), "Calendar deletion removed video"
                second = media_lecture()
                media_store.save_recording(second, {"status": "saved", "path": output})
                assert media_store.delete(second, delete_recording=True) and not Path(output).exists()
                third = media_lecture()
                media_store.save_recording(third, short_result)
                assert media_store.recording_for(third)["note"], "Short recording explanation missing"
                report["checks"]["calendar_and_video_deletion"] = True
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
            minimized = threading.Event()
            worker_started = threading.Event()

            def synthetic_runner(lecture, settings, *, cancel_event, status_callback):
                worker_started.set()
                cancel_event.wait(10)

            class DiagnosticApi(DesktopApi):
                def get_state(self):
                    result = super().get_state()
                    if result["ok"]:
                        called.set()
                    return result

            api = DiagnosticApi(LectureStore(directory / "lectures.sqlite3"), event_runner=synthetic_runner)
            ui = Path(__file__).resolve().parent / "ui" / "index.html"
            assert ui.is_file(), "Packaged UI files are missing"
            window = webview.create_window("Телекон — проверка билда", str(ui), js_api=api,
                                           width=1000, height=760, background_color="#171819")
            api._alerts.backend.window = window
            window.events.minimized += lambda: minimized.set()
            def wait_for_bridge():
                try:
                    report["checks"]["native_ui_js_bridge"] = called.wait(20)
                    window.minimize()
                    assert minimized.wait(5), "Native app did not minimize"
                    result = api.quick_join({"url": "telemost.yandex.ru/j/synthetic-check",
                                             "ends_at": (datetime.now() + timedelta(minutes=1)).isoformat()})
                    assert result["ok"], result
                    assert worker_started.wait(5), "Background worker did not start while minimized"
                    api.save_settings({"alert_volume": 0})
                    api._on_visibility(True)
                    alert_state = api.get_state()["data"]["alerts"]
                    assert alert_state["meeting_hidden"] and not alert_state["error"], alert_state
                    assert api._alerts.backend._tray_hwnd is not None, "Native notification icon missing"
                    api._on_visibility(False)
                    assert api._alerts.backend._tray_hwnd is None, "Native notification was not cleared"
                    assert not api.get_state()["data"]["alerts"]["error"]
                    report["checks"]["native_hidden_window_alerts"] = True
                    # UI timers may be suspended; the Python worker must remain active.
                    threading.Event().wait(1)
                    assert api._scheduler.active, "Background worker stopped while minimized"
                    report["checks"]["native_minimized_worker"] = True
                except Exception:
                    report["checks"]["native_minimized_worker"] = False
                    report["native_ui_error"] = traceback.format_exc()
                finally:
                    api._alerts.close()
                    api.stop_active()
                    if api._scheduler:
                        api._scheduler.wait_idle(10)
                    api._sleep.close()
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
