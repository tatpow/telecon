"""Build, test, and archive a standalone Windows distribution."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def windows_version(version: str) -> str:
    """Keep the public beta label while supplying Windows' numeric version."""
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        parts = version.split(".")
    else:
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:(?:a|b|rc)(\d*))?", version)
        if match is None:
            raise ValueError("Version must be numeric (1.2.3.4) or a release label such as v0.2.0b.")
        parts = [*match.groups()[:3], match.group(4) or "0"]
    if any(int(part) > 65535 for part in parts):
        raise ValueError("Windows version components must be between 0 and 65535.")
    return ".".join(str(int(part)) for part in parts)


def stamp_version(executable: Path, label: str, numeric: str) -> None:
    # Nuitka's CLI accepts numbers only. Its resource writer supports a separate
    # display string, so Explorer can show the actual v0.2.0b release label.
    import ctypes.wintypes  # Nuitka's resource writer expects it to be loaded.
    from nuitka.Tracing import general
    from nuitka.utils.WindowsResources import addVersionInfoResource
    values = {"ProductName": "Telecon", "FileDescription": f"Telecon {label}",
              "ProductVersion": label, "FileVersion": label}
    parts = tuple(int(part) for part in numeric.split("."))
    addVersionInfoResource(values, parts, parts, (0, 0), True, str(executable), general)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="2026.10.16.3")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build" / "windows-ffmpeg")
    parser.add_argument("--skip-compile", action="store_true")
    parser.add_argument("--no-hashsum", action="store_true", help="Omit checksum files and source hashes.")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Build on Windows x64.")
    try:
        numeric_version = windows_version(args.version)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    release_name = f"Telecon-Windows-x64-{args.version}"
    release = ROOT / "dist" / release_name
    archive = ROOT / "dist" / f"{release_name}.zip"
    if release.exists() or archive.exists():
        raise SystemExit(f"Release already exists; choose a new version: {release}")

    if not args.skip_compile:
        command = [
            sys.executable, "-m", "nuitka", "--standalone", "--mingw64", "--lto=no", "--jobs=8",
            "--assume-yes-for-downloads", "--windows-console-mode=disable",
            # Nuitka 2.8's pywebview platform allowlist drops win32.py added
            # in pywebview 6.2.1. Follow Windows imports explicitly instead.
            "--disable-plugin=pywebview", "--include-module=webview.platforms.win32",
            "--playwright-include-browser=none",
            "--include-package=proctap", "--include-module=windows_capture.windows_capture",
            "--include-package=imageio_ffmpeg", "--include-distribution-metadata=proc-tap",
            "--nofollow-import-to=proctap.backends.linux,proctap.backends.pipewire_native,proctap.backends.macos_*,webview.platforms.android,webview.platforms.cocoa,webview.platforms.gtk,webview.platforms.qt,pytest,IPython",
            "--include-data-dir=ui=ui", "--output-filename=Telecon.exe",
            f"--output-dir={output}", f"--report={output / 'compilation-report.xml'}",
            f"--product-version={numeric_version}", f"--file-version={numeric_version}",
            "--product-name=Telecon", "--file-description=Telecon lecture recorder", "app.py",
        ]
        (output / "build-command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
        print("Compiling standalone application…", flush=True)
        subprocess.run(command, cwd=ROOT, check=True)

    compiled = output / "app.dist"
    if not (compiled / "Telecon.exe").is_file():
        raise SystemExit(f"Compiled executable not found: {compiled}")
    if args.version != numeric_version and not args.skip_compile:
        stamp_version(compiled / "Telecon.exe", args.version, numeric_version)
    print("Staging FFmpeg and Chromium…", flush=True)
    shutil.copytree(compiled, release)
    # App-local copies of the Microsoft C++ redistributable runtime used by
    # native capture/NumPy/OpenCV modules. Do not copy arbitrary OS libraries.
    runtime_directory = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for filename in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        runtime = runtime_directory / filename
        if not runtime.is_file():
            raise SystemExit(f"Install the Microsoft Visual C++ x64 Redistributable before building: {runtime}")
        shutil.copy2(runtime, release / filename)
    import imageio_ffmpeg
    shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), release / "ffmpeg.exe")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        chromium = Path(playwright.chromium.executable_path)
    if not chromium.is_file():
        raise SystemExit("Install Chromium with: python -m playwright install chromium")
    # Preserve the revision directory expected by the packaged Playwright driver.
    browser_directory = chromium.parent.parent
    shutil.copytree(browser_directory, release / "ms-playwright" / browser_directory.name)
    for name in ("README.md", "AGENTS.md", "BUILDING.md", "CHECKS.md", "LICENSE"):
        shutil.copy2(ROOT / name, release / name)
    (release / "START-HERE.txt").write_text(
        f"Телекон {args.version} для Windows x64\n\n"
        "Распакуйте весь архив в отдельную папку и запустите Telecon.exe.\n"
        "Не переносите EXE отдельно: рядом нужны DLL, ui, ffmpeg.exe и ms-playwright.\n"
        "Python и OBS устанавливать не нужно. Требуется Microsoft Edge WebView2 Runtime.\n"
        "Настройки: «Запись и аккаунт». Войдите корпоративным аккаунтом Яндекса.\n"
        "Вход открывает главную https://telemost.yandex.ru/; ссылку на созвон добавляйте в карточку лекции.\n"
        "По умолчанию видео сохраняется в Видео/Telecon.\n"
        "Опция предотвращения сна действует при расписании/записи; блокировку Windows не отключает.\n",
        encoding="utf-8",
    )

    licenses = release / "licenses"
    packages = ["pywebview", "playwright", "imageio-ffmpeg", "proc-tap", "windows-capture",
                "psutil", "numpy", "scipy", "opencv-python", "pythonnet", "clr-loader"]
    for name in packages:
        distribution = importlib.metadata.distribution(name)
        for file in distribution.files or ():
            if "license" in file.name.lower() or "copying" in file.name.lower():
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    destination = licenses / name / file.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    (licenses / "FFmpeg.txt").write_text(
        "FFmpeg binary distributed by imageio-ffmpeg, built by Gyan.\n"
        "FFmpeg: https://ffmpeg.org/  Source: https://ffmpeg.org/download.html\n"
        "Windows builds: https://www.gyan.dev/ffmpeg/builds/\n"
        "See ffmpeg.exe -L and ffmpeg.exe -version for licensing and build configuration.\n",
        encoding="utf-8",
    )
    report = output / "packaged-self-check.json"
    print("Testing the packaged EXE (temporary UI, browser, video, audio and power request)…", flush=True)
    result = subprocess.run([str(release / "Telecon.exe"), "--self-check", str(report)],
                            cwd=release, timeout=180)
    checks = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    shutil.copy2(report, release / "SELF-CHECK.json") if report.exists() else None
    if result.returncode or checks.get("ok") is not True:
        raise SystemExit(f"Packaged self-check failed: {report}")

    source_files = ["app.py", "diagnostics.py", "integrations.py", "storage.py", "scheduler.py",
                    "recording.py", "power.py", "notifications.py", "ui/index.html", "ui/app.js", "ui/styles.css"]
    manifest = {
        "version": args.version, "windows_version": numeric_version,
        "python": sys.version, "platform": platform.platform(),
        "dependencies": {name: importlib.metadata.version(name) for name in packages + ["Nuitka"]},
        "self_check": checks["checks"],
    }
    if not args.no_hashsum:
        manifest["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files}
    (release / "BUILD-INFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Creating ZIP archive…", flush=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for file in sorted(release.rglob("*")):
            if file.is_file():
                package.write(file, file.relative_to(release.parent))
    result = {"exe": str(release / "Telecon.exe"), "zip": str(archive), "zip_bytes": archive.stat().st_size}
    if not args.no_hashsum:
        with archive.open("rb") as archive_file:
            digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
        archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        result["sha256"] = digest
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
