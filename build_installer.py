"""Package an already checked Windows distribution with Inno Setup."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from build_windows import windows_version

ROOT = Path(__file__).resolve().parent
APP_ID = "{A7F7E82A-1D7B-4F55-8A14-A63F066F746E}"


def validate_payload(directory: Path) -> dict:
    directory = directory.resolve()
    required = ("Telecon.exe", "LICENSE", "ui/tray.ico", "BUILD-INFO.json", "SELF-CHECK.json")
    if not all((directory / name).is_file() for name in required):
        raise ValueError("Нужна полная проверенная папка Windows-приложения, а не отдельный EXE.")
    info = json.loads((directory / "BUILD-INFO.json").read_text(encoding="utf-8"))
    windows_version(info["version"])
    if not str(info.get("platform", "")).startswith("Windows"):
        raise ValueError("Установщик предназначен для Windows-сборки.")
    check = json.loads((directory / "SELF-CHECK.json").read_text(encoding="utf-8"))
    if check.get("ok") is not True or not check.get("checks") or not all(check["checks"].values()):
        raise ValueError("Самопроверка приложения не пройдена.")
    portable = info.get("portable_self_check", {})
    required_ui = {"connection_default", "temporary_calendar_crud", "chromium_launch", "native_ui_js_bridge"}
    if not required_ui <= portable.keys() or not all(portable[key] is True for key in required_ui):
        raise ValueError("Нет успешной проверки интерфейса и браузера готового EXE.")
    tray = info.get("tray_checks", {})
    if not all(tray.get(key) is True for key in ("native_menu_layout", "restore", "graceful_exit")):
        raise ValueError("Нужна сборка с проверенным нативным меню трея.")
    if (directory / "LICENSE").read_bytes() != (ROOT / "LICENSE").read_bytes():
        raise ValueError("LICENSE приложения отличается от LICENSE текущего checkout GitHub.")
    for path in directory.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Не упаковываются ссылки вне поставки: {path}")
        if path.name.lower() in {"cookies", "login data", "history", ".git"} or path.suffix.lower() in {
                ".sqlite", ".sqlite3", ".db", ".mkv", ".mp4"}:
            raise ValueError(f"В поставке обнаружены пользовательские данные: {path}")
    return info


def find_compiler(explicit: Path | None = None) -> Path:
    if explicit is not None and not explicit.is_file():
        raise FileNotFoundError(f"Компилятор не найден: {explicit}")
    candidates = [explicit, shutil.which("ISCC.exe"), ROOT / "build/tools/innosetup/compiler/ISCC.exe"]
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if root:
            candidates.append(Path(root) / "Inno Setup 6/ISCC.exe")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise FileNotFoundError("Установите Inno Setup 6 или укажите --iscc; см. BUILDING.md.")


def create_installer(directory: Path, output: Path, compiler: Path) -> Path:
    directory, output = directory.resolve(), output.resolve()
    info = validate_payload(directory)
    destination = output / f"Telecon-Windows-x64-{info['version']}-Setup.exe"
    if destination.exists():
        raise FileExistsError(f"Установщик уже существует: {destination}")
    if output == directory or output.is_relative_to(directory):
        raise ValueError("Установщик нельзя создавать внутри упаковываемой папки.")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(compiler), f"/DPayloadDir={directory}", f"/DReleaseVersion={info['version']}",
               f"/DNumericVersion={windows_version(info['version'])}", f"/DInstallerDir={output}",
               str(ROOT / "installer/telecon.iss")]
    subprocess.run(command, cwd=ROOT, check=True)
    if not destination.is_file():
        raise RuntimeError("Inno Setup не создал установщик.")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--iscc", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Создавайте Windows-установщик на Windows.")
    result = create_installer(args.release_dir, args.output_dir, find_compiler(args.iscc))
    print(json.dumps({"installer": str(result), "bytes": result.stat().st_size}))


if __name__ == "__main__":
    main()
