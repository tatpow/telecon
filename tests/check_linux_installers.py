"""Install/remove native packages in fresh Ubuntu and Fedora containers."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--reports", type=Path, default=ROOT / "build/installer-smoke")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run on Linux with Docker; the host system is never modified")
    reports = args.reports.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    for kind, image in (("deb", "ubuntu:24.04"), ("rpm", "registry.fedoraproject.org/fedora:44")):
        package = args.dist_dir.resolve() / f"Telecon-Linux-x64-{args.version}.{kind}"
        if not package.is_file():
            raise FileNotFoundError(package)
        command = ["docker", "run", "--rm", "--volume", f"{package.parent}:/packages:ro",
                   "--volume", f"{ROOT / 'tests/check_linux_installers.sh'}:/check.sh:ro",
                   "--volume", f"{reports}:/reports", image, "bash", "/check.sh", kind, package.name]
        log = reports / f"install-{kind}.log"
        with log.open("w", encoding="utf-8") as console:
            result = subprocess.run(command, stdout=console, stderr=subprocess.STDOUT, timeout=1200)
        if result.returncode:
            print(log.read_text(encoding="utf-8", errors="replace"), flush=True)
            raise SystemExit(f"{image} install/uninstall smoke failed; see {log}")


if __name__ == "__main__":
    main()
