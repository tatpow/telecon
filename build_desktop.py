"""Build a native Linux folder or macOS .app, smoke-test it, then archive it."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from build_windows import windows_version

ROOT = Path(__file__).resolve().parent
DOCUMENTS = ("README.md", "AGENTS.md", "BUILDING.md", "CHECKS.md", "RELEASE_NOTES.md",
             "CONTRIBUTING.md", "DESIGN.md", "LICENSE")


def target_name(system: str, machine: str) -> tuple[str, str]:
    architecture = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine.lower())
    if system == "darwin" and architecture:
        return "macOS", architecture
    if system == "linux" and architecture == "x64":
        return "Linux", architecture
    raise ValueError("Build natively on Linux x64 or macOS arm64/x64. Windows uses build_windows.py.")


def compilation_command(system: str, version: str, output: Path) -> list[str]:
    numeric = windows_version(version)
    backend = "cocoa" if system == "darwin" else "qt"
    excluded = {"android", "win32", "gtk", "qt", "cocoa"} - {backend}
    command = [sys.executable, "-m", "nuitka", "--standalone", "--lto=no", "--jobs=4",
               "--assume-yes-for-downloads", "--disable-plugin=pywebview",
               f"--include-module=webview.platforms.{backend}", "--include-package-data=webview",
               "--playwright-include-browser=none", "--include-data-dir=ui=ui",
               "--output-filename=Telecon", f"--output-dir={output}",
               f"--report={output / 'compilation-report.xml'}",
               "--nofollow-import-to=" + ",".join(sorted(
                   {f"webview.platforms.{name}" for name in excluded} |
                   {"windows_capture", "proctap", "pytest", "IPython"}))]
    if system == "darwin":
        command += ["--macos-create-app-bundle", "--macos-app-name=Telecon",
                    "--macos-signed-app-name=ru.tatpow.telecon",
                    f"--macos-app-version={'.'.join(numeric.split('.')[:3])}"]
    else:
        command += ["--enable-plugin=pyside6"]
    return command + ["app.py"]


def bundle_executable(app: Path) -> Path:
    with (app / "Contents" / "Info.plist").open("rb") as source:
        name = plistlib.load(source)["CFBundleExecutable"]
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Invalid macOS bundle executable name")
    executable = app / "Contents" / "MacOS" / name
    if not executable.is_file():
        raise FileNotFoundError(executable)
    return executable


def _is_macho(path: Path) -> bool:
    with path.open("rb") as source:
        return source.read(4) in {
            b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def relocate_macos_data(app: Path) -> list[str]:
    """Keep Mach-O code in place; relocate data before sealing the outer app.

    Relative compatibility links preserve package resource lookups. Apple TN2206
    explicitly supports these links when data originally occupied code locations.
    Never follow or overwrite existing links, or move anything outside this app.
    """
    bundle = app.resolve(strict=True)
    code = bundle / "Contents" / "MacOS"
    resources = bundle / "Contents" / "Resources"
    resources.mkdir(exist_ok=True)
    if not code.resolve().is_relative_to(bundle) or not resources.resolve().is_relative_to(bundle):
        raise ValueError("Bundle code/resources must stay inside the application")
    moved = []

    def visit(source: Path):
        if source.is_symlink():
            return
        if source.is_dir():
            # Move data-only trees together, preserving package-relative paths.
            contains_code = any(p.is_file() and _is_macho(p)
                                for p in source.rglob("*"))
            if contains_code:
                for child in sorted(source.iterdir()):
                    visit(child)
                return
        elif not source.is_file() or _is_macho(source):
            return
        relative = source.relative_to(code)
        destination = resources / relative
        if not source.resolve().is_relative_to(code.resolve()) or not destination.parent.resolve().is_relative_to(bundle):
            raise ValueError("Resource relocation escaped the application")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        source.symlink_to(os.path.relpath(destination, source.parent), target_is_directory=destination.is_dir())
        moved.append(relative.as_posix())

    for child in sorted(code.iterdir()):
        visit(child)
    return moved


def configure_linux_qt(release: Path) -> None:
    plugins = release / "PySide6" / "qt-plugins"
    if not (plugins / "platforms" / "libqxcb.so").is_file():
        raise FileNotFoundError(f"Packaged Qt xcb platform plugin missing: {plugins}")
    # Nuitka's generated file contains Prefix only. Supply an explicit fallback
    # plugin location as well, rather than searching the builder's system Qt.
    configuration = "[Paths]\nPrefix = .\nPlugins = PySide6/qt-plugins\n"
    for name in ("qt.conf", "qt6.conf"):
        (release / name).write_text(configuration, encoding="utf-8")


def copy_documentation(release: Path) -> None:
    for name in DOCUMENTS:
        shutil.copy2(ROOT / name, release / name)
    shutil.copytree(ROOT / "docs" / "images", release / "docs" / "images")
    shutil.copy2(ROOT / "docs" / "USER_GUIDE.md", release / "docs" / "USER_GUIDE.md")


def copy_licenses(release: Path) -> dict[str, str]:
    dependencies = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        dependencies[name] = distribution.version
        for file in distribution.files or ():
            if "license" not in file.name.lower() and "copying" not in file.name.lower():
                continue
            source = Path(distribution.locate_file(file))
            if source.is_file():
                destination = release / "licenses" / name / file.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    return dependencies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v0.4.1")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-hashsum", action="store_true", help="Accepted for parity; POSIX builds never generate hashes.")
    args = parser.parse_args()
    try:
        label, architecture = target_name(sys.platform, platform.machine())
        windows_version(args.version)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("Use native CPython 3.12 for release builds.")
    output = (args.output_dir or ROOT / "build" / f"{label.lower()}-{architecture}").resolve()
    release_name = f"Telecon-{label}-{architecture}-{args.version}"
    release = ROOT / "dist" / release_name
    archive = Path(str(release) + (".zip" if sys.platform == "darwin" else ".tar.gz"))
    if release.exists() or archive.exists():
        raise SystemExit(f"Release already exists: {release}")
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    if not browsers.is_absolute() or not any(browsers.glob("chromium-*")):
        raise SystemExit("Set PLAYWRIGHT_BROWSERS_PATH to an absolute Chromium cache; run python -m playwright install chromium.")
    output.mkdir(parents=True, exist_ok=True)
    (output / "build-dependencies.json").write_text(json.dumps({
        "python": sys.version, "platform": platform.platform(),
        "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}}, indent=2), encoding="utf-8")
    command = compilation_command(sys.platform, args.version, output)
    (output / "build-command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    subprocess.run(command, cwd=ROOT, check=True)
    release.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        apps = list(output.glob("*.app"))
        if len(apps) != 1:
            raise SystemExit(f"Expected one Nuitka app bundle, found {len(apps)} in {output}")
        release.mkdir()
        app = release / "Telecon.app"
        shutil.copytree(apps[0], app, symlinks=True)
        browser_destination = app / "Contents" / "Resources" / "ms-playwright"
        executable = bundle_executable(app)
        moved = relocate_macos_data(app)
        (output / "resource-relocations.json").write_text(json.dumps(moved, indent=2), encoding="utf-8")
    else:
        shutil.copytree(output / "app.dist", release, symlinks=True)
        browser_destination = release / "ms-playwright"
        executable = release / "Telecon"
        if not executable.is_file():
            raise SystemExit(f"Compiled executable missing: {executable}")
        configure_linux_qt(release)
    shutil.copytree(browsers, browser_destination, symlinks=True,
                    ignore=shutil.ignore_patterns(".links", "__pycache__"))
    copy_documentation(release)
    dependencies = copy_licenses(release)
    if sys.platform == "darwin":
        # Re-seal the outer bundle after adding Chromium; retain nested vendor signatures.
        subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none",
                        "--preserve-metadata=entitlements,flags", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    report_path = release / "SELF-CHECK.json"
    environment = dict(os.environ)
    # Require the staged browser, rather than accidentally testing the builder's cache.
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_destination)
    smoke_log = output / "packaged-smoke.log"
    try:
        with smoke_log.open("w", encoding="utf-8") as console:
            result = subprocess.run([str(executable), "--portable-self-check", str(report_path)], cwd=release,
                                    env=environment, timeout=150, stdout=console, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        print(smoke_log.read_text(encoding="utf-8", errors="replace"), flush=True)
        raise
    if result.returncode:
        print(smoke_log.read_text(encoding="utf-8", errors="replace"), flush=True)
        if report_path.is_file():
            print(report_path.read_text(encoding="utf-8"), flush=True)
        raise SystemExit(f"Packaged smoke check failed ({result.returncode}); no archive created: {smoke_log}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("ok") is not True or report.get("mode") != "portable":
        raise SystemExit("Packaged native UI/browser smoke check failed; no archive created.")
    (release / "BUILD-INFO.json").write_text(json.dumps({
        "version": args.version, "platform": platform.platform(), "architecture": architecture,
        "python": sys.version, "validation": "portable", "recording_supported": False,
        "recording_checked": False, "developer_id_signed": False,
        "dependencies": dependencies, "self_check": report["checks"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (release / "START-HERE.txt").write_text(
        f"Телекон {args.version} · {label} {architecture}\n\n"
        + ("Откройте Telecon.app. Chromium уже находится внутри приложения.\n" if sys.platform == "darwin" else
           "Сохраните всю папку и запустите ./Telecon. Не переносите бинарник отдельно.\n")
        + "Это экспериментальная нативная сборка для календаря, входа в Яндекс и подключения.\n"
          "Запись видео, защита от сна и предупреждения о скрытом окне доступны в Windows.\n"
          "Для начала: Настройки → Основные → Войти в Яндекс, затем добавьте лекцию.\n"
          "Подробности и системные зависимости: README.md и BUILDING.md.\n", encoding="utf-8")
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(release), str(archive)], check=True)
    else:
        with tarfile.open(archive, "w:gz", dereference=False) as package:
            package.add(release, arcname=release.name)
    print(json.dumps({"release": str(release), "archive": str(archive), "bytes": archive.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
