import json
import os
import plistlib
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import build_posix_installers as packaging
from build_desktop import DOCUMENTS, bundle_macos_documentation


class PosixInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "application with spaces"
        (self.release / "ui").mkdir(parents=True)
        (self.release / "Telecon").write_bytes(b"executable fixture")
        (self.release / "Telecon").chmod(0o755)
        (self.release / "ui/tray.png").write_bytes(b"icon fixture")
        (self.release / "LICENSE").write_bytes((packaging.ROOT / "LICENSE").read_bytes())
        self.info = {"version": "v0.5.0", "platform": "Linux-x64", "architecture": "x64"}
        self.report = {"ok": True, "mode": "portable", "checks": dict.fromkeys(packaging.CORE_CHECKS, True),
                       "tray_checks": dict.fromkeys(packaging.TRAY_CHECKS, True)}
        self.save_reports()

    def save_reports(self):
        for name, data in (("BUILD-INFO.json", self.info), ("SELF-CHECK.json", self.report)):
            (self.release / name).write_text(json.dumps(data), encoding="utf-8")

    def test_release_versions_sort_betas_before_stable_and_reject_injection(self):
        self.assertEqual(packaging.package_versions("v0.5.0"), ("0.5.0", "0.5.0", "1"))
        self.assertEqual(packaging.package_versions("v0.5.0b2"), ("0.5.0~b2", "0.5.0", "0.b2"))
        with self.assertRaises(ValueError):
            packaging.package_versions("0.5.0\n%post\nrm -rf /not-real")

    def test_failed_bridge_or_tray_must_block_installer_even_if_top_level_ok(self):
        for group, key in (("checks", "native_ui_js_bridge"), ("tray_checks", "restore")):
            self.report[group][key] = False
            self.save_reports()
            with self.assertRaisesRegex(ValueError, "must pass"):
                packaging.validate_payload(self.release, "linux", "x64")
            self.report[group][key] = True

    def test_architecture_and_platform_must_match_native_target(self):
        with self.assertRaisesRegex(ValueError, "architecture"):
            packaging.validate_payload(self.release, "linux", "arm64")
        with self.assertRaisesRegex(ValueError, "platform"):
            packaging.validate_payload(self.release, "darwin", "x64")

    def test_exact_license_and_absence_of_personal_data_are_required(self):
        for name in ("Cookies", "private.sqlite3", "lecture.mp4"):
            path = self.release / name
            path.write_bytes(b"synthetic personal data")
            with self.assertRaisesRegex(ValueError, "Personal data"):
                packaging.validate_payload(self.release, "linux", "x64")
            path.unlink()
        (self.release / "LICENSE").write_text("replacement terms", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "LICENSE"):
            packaging.validate_payload(self.release, "linux", "x64")

    def test_internal_relative_resource_links_survive_but_external_links_are_rejected(self):
        link = self.release / "compatibility-link"
        link.symlink_to("ui", target_is_directory=True)
        packaging.validate_payload(self.release, "linux", "x64")
        root = self.root / "installed-root"
        packaging.stage_linux_payload(self.release, root)
        copied = root / "opt/telecon/compatibility-link"
        self.assertTrue(copied.is_symlink())
        self.assertEqual(os.readlink(copied), "ui")
        link.unlink()
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "escapes"):
            packaging.validate_payload(self.release, "linux", "x64")

    def test_linux_menu_launcher_and_license_are_owned_by_package_not_user_home(self):
        root = self.root / "installed-root"
        packaging.stage_linux_payload(self.release, root)
        launcher = root / "usr/bin/telecon"
        self.assertIn('exec /opt/telecon/Telecon "$@"', launcher.read_text())
        self.assertNotIn("DISABLE_SANDBOX", launcher.read_text())
        self.assertNotIn("--no-sandbox", launcher.read_text())
        if os.name == "posix":
            self.assertTrue(launcher.stat().st_mode & 0o111)
        desktop = (root / "usr/share/applications/ru.tatpow.telecon.desktop").read_text(encoding="utf-8")
        self.assertIn("Exec=telecon\n", desktop)
        self.assertNotIn("%U", desktop)
        self.assertEqual((root / "usr/share/doc/telecon/copyright").read_bytes(),
                         (packaging.ROOT / "LICENSE").read_bytes())
        self.assertFalse((root / "home").exists())

    def test_package_metadata_declares_glibc_baseline_and_no_uninstall_scripts(self):
        control = packaging.debian_control("0.5.0")
        self.assertIn("Architecture: amd64", control)
        self.assertIn("libc6 (>= 2.39)", control)
        depends = next(line.removeprefix("Depends: ") for line in control.splitlines() if line.startswith("Depends: "))
        self.assertIn("libatomic1", depends.split(", "))
        spec = packaging.rpm_spec(PurePosixPath("/tmp/with spaces/root"), "0.5.0", "1")
        self.assertIn("License: AGPL-3.0-only", spec)
        self.assertIn("glibc >= 2.39", spec)
        requires = next(line.removeprefix("Requires: ") for line in spec.splitlines() if line.startswith("Requires: "))
        self.assertIn("libatomic", requires.split(", "))
        self.assertIn("'/tmp/with spaces/root/.'", spec)
        self.assertIn("__os_install_post %{nil}", spec)
        self.assertNotIn("%postun", spec)
        self.assertNotIn("/home/", spec)

    def test_installer_never_overwrites_or_writes_into_the_payload(self):
        with patch("build_posix_installers.sys.platform", "linux"), patch("platform.machine", return_value="x86_64"):
            with self.assertRaises(ValueError):
                packaging.create_installers(self.release, self.release / "output")
            output = self.root / "dist"
            output.mkdir()
            existing = output / "Telecon-Linux-x64-v0.5.0.deb"
            existing.write_bytes(b"preserve this release")
            with self.assertRaises(FileExistsError):
                packaging.create_installers(self.release, output)
            self.assertEqual(existing.read_bytes(), b"preserve this release")

    def test_linux_package_staging_does_not_put_debian_metadata_in_rpm(self):
        work = self.root / "work"
        work.mkdir()
        def sdk(command, **kwargs):
            if command[0] == "dpkg-deb":
                self.assertIn("--root-owner-group", command)
                self.assertTrue((work / "root/DEBIAN/control").is_file())
                Path(command[-1]).write_bytes(b"deb fixture")
            elif command[0] == "rpmbuild":
                self.assertFalse((work / "root/DEBIAN").exists())
                rpm = work / "rpmbuild/RPMS/x86_64/telecon-0.5.0-1.x86_64.rpm"
                rpm.parent.mkdir(parents=True, exist_ok=True)
                rpm.write_bytes(b"rpm fixture")
        with patch("build_posix_installers.subprocess.run", side_effect=sdk):
            result = packaging.build_linux(self.release, work, "v0.5.0")
        self.assertEqual([path.name for path in result], ["Telecon-Linux-x64-v0.5.0.deb", "Telecon-Linux-x64-v0.5.0.rpm"])
        self.assertTrue(all(path.is_file() for path in result))

    def test_mac_distribution_keeps_sdk_architecture_and_adds_exact_license_reference(self):
        xml = self.root / "Distribution.xml"
        xml.write_text('<installer-gui-script><options hostArchitectures="arm64"/></installer-gui-script>')
        packaging.configure_distribution(xml)
        tree = ET.parse(xml).getroot()
        self.assertEqual(tree.find("options").get("hostArchitectures"), "arm64")
        self.assertEqual(tree.find("license").attrib, {"file": "LICENSE.txt", "mime-type": "text/plain"})
        self.assertEqual(tree.find("domains").get("enable_localSystem"), "true")

    def test_mac_app_links_cannot_depend_on_files_outside_the_installed_bundle(self):
        self.info["platform"] = "macOS-15-arm64"
        self.info["architecture"] = "arm64"
        self.save_reports()
        contents = self.release / "Telecon.app/Contents"
        (contents / "MacOS").mkdir(parents=True)
        executable = contents / "MacOS/Telecon"
        executable.write_bytes(b"native executable fixture")
        executable.chmod(0o755)
        with (contents / "Info.plist").open("wb") as destination:
            plistlib.dump({"CFBundleExecutable": "Telecon"}, destination)
        resources = contents / "Resources"
        for name in ("third-party-licenses", "telecon-docs", "ms-playwright"):
            (resources / name).mkdir(parents=True)
        (resources / "LICENSE").write_bytes((packaging.ROOT / "LICENSE").read_bytes())
        packaging.validate_payload(self.release, "darwin", "arm64")
        # This is inside the portable release but would break when only .app is installed.
        (resources / "ui").symlink_to(self.release / "ui", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "escapes"):
            packaging.validate_payload(self.release, "darwin", "arm64")

    def test_mac_app_alone_contains_project_and_vendor_licenses_and_user_guide(self):
        for name in DOCUMENTS:
            if name != "LICENSE":
                (self.release / name).write_text("documentation", encoding="utf-8")
        (self.release / "licenses/vendor").mkdir(parents=True)
        (self.release / "licenses/vendor/COPYING").write_text("vendor terms")
        (self.release / "docs").mkdir()
        (self.release / "docs/USER_GUIDE.md").write_text("guide")
        app = self.release / "Telecon.app"
        resources = app / "Contents/Resources"
        resources.mkdir(parents=True)
        bundle_macos_documentation(self.release, app)
        self.assertEqual((resources / "LICENSE").read_bytes(), (packaging.ROOT / "LICENSE").read_bytes())
        self.assertEqual((resources / "third-party-licenses/vendor/COPYING").read_text(), "vendor terms")
        self.assertEqual((resources / "telecon-docs/docs/USER_GUIDE.md").read_text(), "guide")


if __name__ == "__main__":
    unittest.main()
