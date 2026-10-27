"""Create native DEB/RPM or DMG/PKG from an already checked distribution."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from build_desktop import bundle_executable, target_name
from build_windows import windows_version

ROOT = Path(__file__).resolve().parent
CORE_CHECKS = {"connection_default", "temporary_calendar_crud", "chromium_launch", "native_ui_js_bridge"}
TRAY_CHECKS = {"native_menu_layout", "restore", "graceful_exit"}
DEB_DEPENDS = (
    "libc6 (>= 2.39)", "libstdc++6", "libgcc-s1", "zlib1g", "libasound2t64",
    "libatk1.0-0t64", "libatk-bridge2.0-0t64", "libatspi2.0-0t64", "libcairo2",
    "libcups2t64", "libdbus-1-3", "libdrm2", "libgbm1", "libglib2.0-0t64",
    "libnspr4", "libnss3", "libpango-1.0-0", "libx11-6", "libx11-xcb1", "libxcb1",
    "libxcomposite1", "libxdamage1", "libxext6", "libxfixes3", "libxrandr2", "libxrender1",
    "libxcursor1", "libxi6", "libxtst6", "libxss1", "libxshmfence1", "libxkbcommon0",
    "libegl1", "libgl1", "libopengl0", "libxcb-cursor0", "libxcb-icccm4",
    "libxcb-keysyms1", "libxcb-shape0", "libxcb-xinerama0", "libxcb-render-util0",
    "libxcb-image0", "libxcb-xkb1", "libxkbcommon-x11-0", "libgtk-3-0t64",
    "libfontconfig1", "libfreetype6", "fonts-liberation")
RPM_DEPENDS = (
    "glibc >= 2.39", "libstdc++", "libgcc", "zlib", "alsa-lib", "atk", "at-spi2-atk",
    "at-spi2-core", "cairo", "cups-libs", "dbus-libs", "libdrm", "mesa-libgbm", "glib2",
    "nspr", "nss", "pango", "libX11", "libxcb", "libXcomposite", "libXdamage", "libXext",
    "libXfixes", "libXrandr", "libXrender", "libXcursor", "libXi", "libXtst", "libXScrnSaver",
    "libxshmfence", "libxkbcommon", "mesa-libEGL", "libglvnd-glx", "libglvnd-opengl",
    "xcb-util-cursor", "xcb-util-wm", "xcb-util-keysyms", "xcb-util-image",
    "xcb-util-renderutil", "libxkbcommon-x11", "gtk3", "fontconfig", "freetype",
    "liberation-fonts-all")


def package_versions(label: str) -> tuple[str, str, str]:
    windows_version(label)
    match = re.fullmatch(r"v?(\d+\.\d+\.\d+(?:\.\d+)?)(?:(a|b|rc)(\d*))?", label)
    if not match:
        raise ValueError("Unsupported native package version")
    base, stage, number = match.groups()
    suffix = f"{stage}{number or '0'}" if stage else ""
    return base + ("~" + suffix if suffix else ""), base, "0." + suffix if suffix else "1"


def validate_payload(directory: Path, system: str, architecture: str) -> dict:
    directory = directory.resolve(strict=True)
    info = json.loads((directory / "BUILD-INFO.json").read_text(encoding="utf-8"))
    package_versions(info["version"])
    platform_label = "macOS" if system == "darwin" else "Linux"
    if not str(info.get("platform", "")).lower().startswith(platform_label.lower()):
        raise ValueError("Payload belongs to a different platform")
    if info.get("architecture") != architecture:
        raise ValueError("Payload belongs to a different architecture")
    report = json.loads((directory / "SELF-CHECK.json").read_text(encoding="utf-8"))
    for checks, required in ((report.get("checks", {}), CORE_CHECKS),
                             (report.get("tray_checks", {}), TRAY_CHECKS)):
        if not all(checks.get(key) is True for key in required):
            raise ValueError("The packaged UI, browser and tray must pass before creating installers")
    if report.get("ok") is not True or report.get("mode") != "portable":
        raise ValueError("A successful packaged portable self-check is required")
    if (directory / "LICENSE").read_bytes() != (ROOT / "LICENSE").read_bytes():
        raise ValueError("Payload LICENSE differs from the checked-out GitHub LICENSE")
    executable = bundle_executable(directory / "Telecon.app") if system == "darwin" else directory / "Telecon"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("Missing executable or executable permissions")
    if system == "darwin":
        resources = directory / "Telecon.app/Contents/Resources"
        if (resources / "LICENSE").read_bytes() != (ROOT / "LICENSE").read_bytes():
            raise ValueError("The installed .app must contain its own LICENSE")
        for name in ("third-party-licenses", "telecon-docs", "ms-playwright"):
            if not (resources / name).is_dir():
                raise ValueError(f"Missing installed app resources: {name}")
    elif not (directory / "ui/tray.png").is_file():
        raise ValueError("Linux application icon is missing")
    for path in directory.rglob("*"):
        if path.is_symlink():
            boundary = directory / "Telecon.app" if system == "darwin" and path.is_relative_to(directory / "Telecon.app") else directory
            if not path.resolve(strict=True).is_relative_to(boundary):
                raise ValueError(f"A bundle link escapes the payload: {path}")
        if path.name.lower() in {"cookies", "login data", "history", ".git"} or path.suffix.lower() in {
                ".sqlite", ".sqlite3", ".db", ".mkv", ".mp4"}:
            raise ValueError(f"Personal data must never be packaged: {path}")
    return info


def write_file(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755 if executable else 0o644)


def stage_linux_payload(directory: Path, root: Path) -> None:
    shutil.copytree(directory, root / "opt/telecon", symlinks=True)
    write_file(root / "usr/bin/telecon", '#!/bin/sh\nexport QT_API=pyside6\nexec /opt/telecon/Telecon "$@"\n', True)
    write_file(root / "usr/share/applications/ru.tatpow.telecon.desktop", """[Desktop Entry]
Type=Application
Name=Telecon
Name[ru]=Телекон
Comment=Join scheduled Telemost meetings
Comment[ru]=Подключение к встречам Телемоста по расписанию
Exec=telecon
Icon=ru.tatpow.telecon
Terminal=false
Categories=Education;Office;
StartupNotify=true
""")
    icon = root / "usr/share/icons/hicolor/64x64/apps/ru.tatpow.telecon.png"
    icon.parent.mkdir(parents=True)
    shutil.copy2(directory / "ui/tray.png", icon)
    documentation = root / "usr/share/doc/telecon"
    documentation.mkdir(parents=True)
    shutil.copy2(directory / "LICENSE", documentation / "copyright")


def debian_control(version: str) -> str:
    return (f"Package: telecon\nVersion: {version}\nArchitecture: amd64\nSection: education\nPriority: optional\n"
            "Maintainer: tatpow <tatpow@users.noreply.github.com>\nHomepage: https://github.com/tatpow/telecon\n"
            "Depends: " + ", ".join(DEB_DEPENDS) + "\nDescription: Telemost meeting scheduler\n"
            " Local calendar and scheduled connections with bundled Chromium.\n")


def rpm_spec(root: Path, version: str, release: str) -> str:
    # Do not strip/rewrite a payload that has already passed a native smoke test.
    # Private bundled SONAMEs must not become public package Provides/Requires.
    return ("%global debug_package %{nil}\n%global __os_install_post %{nil}\n%global _build_id_links none\n"
            f"Name: telecon\nVersion: {version}\nRelease: {release}\nSummary: Telemost meeting scheduler\n"
            "License: AGPL-3.0-only\nURL: https://github.com/tatpow/telecon\nBuildArch: x86_64\nAutoReqProv: no\n"
            "Requires: " + ", ".join(RPM_DEPENDS) + "\n%description\nLocal calendar and scheduled connections.\n"
            "%prep\n%build\n%install\nmkdir -p \"$RPM_BUILD_ROOT\"\ncp -a "
            + shlex.quote(str(root) + "/.") + ' "$RPM_BUILD_ROOT/"\n'
            "%files\n%defattr(-,root,root,-)\n/opt/telecon\n/usr/bin/telecon\n/usr/share/applications/ru.tatpow.telecon.desktop\n"
            "/usr/share/icons/hicolor/64x64/apps/ru.tatpow.telecon.png\n"
            "%dir /usr/share/doc/telecon\n%license /usr/share/doc/telecon/copyright\n")


def build_linux(directory: Path, temporary: Path, label: str) -> list[Path]:
    deb_version, rpm_version, rpm_release = package_versions(label)
    root = temporary / "root"
    stage_linux_payload(directory, root)
    subprocess.run(["desktop-file-validate", str(root / "usr/share/applications/ru.tatpow.telecon.desktop")], check=True)
    write_file(root / "DEBIAN/control", debian_control(deb_version))
    deb = temporary / f"Telecon-Linux-x64-{label}.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(deb)], check=True)
    # DEBIAN is package metadata, not part of the RPM filesystem.
    (root / "DEBIAN/control").unlink()
    (root / "DEBIAN").rmdir()
    spec = temporary / "telecon.spec"
    spec.write_text(rpm_spec(root, rpm_version, rpm_release), encoding="utf-8")
    top = temporary / "rpmbuild"
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        (top / name).mkdir(parents=True, exist_ok=True)
    subprocess.run(["rpmbuild", "-bb", "--define", f"_topdir {top}", str(spec)], check=True)
    rpms = list((top / "RPMS/x86_64").glob("*.rpm"))
    if len(rpms) != 1:
        raise RuntimeError("Expected exactly one x86_64 RPM")
    rpm = temporary / f"Telecon-Linux-x64-{label}.rpm"
    rpms[0].rename(rpm)
    return [deb, rpm]


def configure_distribution(path: Path) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    ET.SubElement(root, "title").text = "Телекон"
    ET.SubElement(root, "license", {"file": "LICENSE.txt", "mime-type": "text/plain"})
    domains = root.find("domains")
    if domains is None:
        domains = ET.SubElement(root, "domains")
    domains.attrib.update(enable_anywhere="true", enable_currentUserHome="false", enable_localSystem="true")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def build_macos(directory: Path, temporary: Path, label: str, architecture: str) -> list[Path]:
    app = directory / "Telecon.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    disk = temporary / "disk"
    disk.mkdir()
    subprocess.run(["ditto", str(app), str(disk / "Telecon.app")], check=True)
    (disk / "Applications").symlink_to("/Applications", target_is_directory=True)
    shutil.copy2(directory / "LICENSE", disk / "LICENSE.txt")
    write_file(disk / "INSTALL.txt", "Перетащите Telecon.app в Applications или выбранную папку.\n"
               "Удаление: закройте Телекон через меню трея и переместите .app в Корзину.\n"
               "Календарь, профиль Яндекса и видеозаписи при этом сохраняются.\n")
    dmg = temporary / f"Telecon-macOS-{architecture}-{label}.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "Telecon", "-srcfolder", str(disk),
                    "-format", "UDZO", str(dmg)], check=True)
    subprocess.run(["hdiutil", "verify", str(dmg)], check=True)
    package_root = temporary / "package-root"
    package_root.mkdir()
    subprocess.run(["ditto", str(app), str(package_root / "Telecon.app")], check=True)
    components = temporary / "components.plist"
    subprocess.run(["pkgbuild", "--analyze", "--root", str(package_root), str(components)], check=True)
    with components.open("rb") as source:
        entries = plistlib.load(source)
    for entry in entries:
        entry["BundleIsRelocatable"] = False
    with components.open("wb") as destination:
        plistlib.dump(entries, destination)
    component = temporary / "Telecon-component.pkg"
    subprocess.run(["pkgbuild", "--root", str(package_root), "--component-plist", str(components),
                    "--identifier", "ru.tatpow.telecon", "--version", windows_version(label),
                    "--install-location", "/Applications", "--ownership", "recommended", str(component)], check=True)
    distribution = temporary / "Distribution.xml"
    requirements = temporary / "requirements.plist"
    with requirements.open("wb") as destination:
        plistlib.dump({"arch": ["arm64" if architecture == "arm64" else "x86_64"]}, destination)
    subprocess.run(["productbuild", "--synthesize", "--product", str(requirements),
                    "--package", str(component), str(distribution)], check=True)
    configure_distribution(distribution)
    resources = temporary / "resources"
    resources.mkdir()
    shutil.copy2(directory / "LICENSE", resources / "LICENSE.txt")
    pkg = temporary / f"Telecon-macOS-{architecture}-{label}.pkg"
    subprocess.run(["productbuild", "--distribution", str(distribution), "--resources", str(resources),
                    "--package-path", str(temporary), str(pkg)], check=True)
    return [dmg, pkg]


def create_installers(directory: Path, output: Path) -> list[Path]:
    import platform
    label, architecture = target_name(sys.platform, platform.machine())
    directory, output = directory.resolve(strict=True), output.resolve()
    info = validate_payload(directory, sys.platform, architecture)
    if output == directory or output.is_relative_to(directory):
        raise ValueError("Installer output must be outside the payload")
    extensions = ("dmg", "pkg") if label == "macOS" else ("deb", "rpm")
    destinations = [output / f"Telecon-{label}-{architecture}-{info['version']}.{ext}" for ext in extensions]
    if any(path.exists() or path.is_symlink() for path in destinations):
        raise FileExistsError("Installer already exists; choose another output directory")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="telecon-installers-", dir=output) as work:
        temporary = Path(work)
        artifacts = (build_macos(directory, temporary, info["version"], architecture) if label == "macOS"
                     else build_linux(directory, temporary, info["version"]))
        for source in artifacts:
            if not source.is_file() or not source.stat().st_size:
                raise RuntimeError(f"Native SDK did not create {source.name}")
        for source, destination in zip(artifacts, destinations, strict=True):
            # Same filesystem; exclusive creation also protects against a second
            # concurrent packager, unlike POSIX rename's overwrite semantics.
            os.link(source, destination)
    return destinations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(json.dumps({"installers": [str(path) for path in create_installers(args.release_dir, args.output_dir)]}))


if __name__ == "__main__":
    main()
