"""Check a mounted DMG, install PKG on an ephemeral CI Mac, and check removal."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_desktop import bundle_executable
from build_posix_installers import CORE_CHECKS, TRAY_CHECKS


def check_app(app: Path, report: Path) -> None:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    resources = app / "Contents/Resources"
    assert (resources / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    environment = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(resources / "ms-playwright"))
    with report.with_suffix(".log").open("w", encoding="utf-8") as console:
        result = subprocess.run([str(bundle_executable(app)), "--portable-self-check", str(report)],
                                cwd=app.parent, env=environment, timeout=180,
                                stdout=console, stderr=subprocess.STDOUT)
    result.check_returncode()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data.get("ok") is True
    assert all(data.get("checks", {}).get(key) is True for key in CORE_CHECKS)
    assert all(data.get("tray_checks", {}).get(key) is True for key in TRAY_CHECKS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", choices=("x64", "arm64"), required=True)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--reports", type=Path, default=ROOT / "build/installer-smoke")
    parser.add_argument("--allow-system-install", action="store_true")
    args = parser.parse_args()
    if (sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted" or not args.allow_system_install):
        parser.error("Installation/removal is restricted to an explicitly opted-in ephemeral GitHub macOS runner")
    installed = Path("/Applications/Telecon.app")
    if installed.exists() or installed.is_symlink():
        raise FileExistsError("Refusing to replace an existing installed application")
    reports = args.reports.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    stem = args.dist_dir.resolve() / f"Telecon-macOS-{args.arch}-{args.version}"
    dmg, pkg = Path(str(stem) + ".dmg"), Path(str(stem) + ".pkg")
    for path in (dmg, pkg):
        if not path.is_file():
            raise FileNotFoundError(path)
    with tempfile.TemporaryDirectory(prefix="telecon-dmg-check-") as work:
        mount = Path(work) / "mounted"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)], check=True)
        try:
            assert (mount / "Applications").is_symlink()
            assert (mount / "LICENSE.txt").read_bytes() == (ROOT / "LICENSE").read_bytes()
            check_app(mount / "Telecon.app", reports / "mounted-dmg.json")
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True)
    with (reports / "install-pkg.log").open("w", encoding="utf-8") as console:
        subprocess.run(["sudo", "installer", "-pkg", str(pkg), "-target", "/"],
                       stdout=console, stderr=subprocess.STDOUT, check=True, timeout=180)
    check_app(installed, reports / "installed-pkg.json")
    # Only the newly installed app is removed, on this disposable runner. No broad
    # cleanup of Application Support, profiles, package receipts or recordings.
    assert installed.resolve(strict=True) == Path("/Applications/Telecon.app")
    data = Path.home() / "Library/Application Support/LectureAssistant"
    videos = Path.home() / "Videos/Telecon"
    data.mkdir(parents=True, exist_ok=True)
    videos.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".installer-smoke-", dir=data) as calendar, \
            tempfile.TemporaryDirectory(prefix=".installer-smoke-", dir=videos) as recording:
        markers = [Path(calendar) / "synthetic-profile.txt", Path(recording) / "synthetic-video.txt"]
        for marker in markers:
            marker.write_text("keep", encoding="utf-8")
        subprocess.run(["sudo", "rm", "-rf", "--", str(installed)], check=True)
        assert not installed.exists()
        assert all(marker.read_text(encoding="utf-8") == "keep" for marker in markers)
    (reports / "uninstall-macos.json").write_text(json.dumps({
        "ok": True, "app_removed": True, "external_user_data_preserved": True}), encoding="utf-8")


if __name__ == "__main__":
    main()
