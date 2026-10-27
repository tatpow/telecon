import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_installer import ROOT, create_installer, validate_payload


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "application"
        (self.release / "ui").mkdir(parents=True)
        (self.release / "Telecon.exe").write_bytes(b"MZ fixture")
        (self.release / "ui/tray.ico").write_bytes(b"icon fixture")
        (self.release / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
        self.info = {"version": "v0.4.1", "platform": "Windows-11-x64",
                     "portable_self_check": dict.fromkeys(("connection_default", "temporary_calendar_crud",
                                                          "chromium_launch", "native_ui_js_bridge"), True),
                     "tray_checks": dict.fromkeys(("native_menu_layout", "restore", "graceful_exit"), True)}
        self.write_info()
        (self.release / "SELF-CHECK.json").write_text(json.dumps({"ok": True, "checks": {"native_ui_js_bridge": True}}))

    def write_info(self):
        (self.release / "BUILD-INFO.json").write_text(json.dumps(self.info))

    def test_rejects_failed_or_stale_native_menu_checks(self):
        self.info["tray_checks"]["native_menu_layout"] = False
        self.write_info()
        with self.assertRaisesRegex(ValueError, "меню"):
            validate_payload(self.release)

    def test_license_must_be_exact_copy_of_repository_license(self):
        (self.release / "LICENSE").write_text("different terms")
        with self.assertRaisesRegex(ValueError, "LICENSE"):
            validate_payload(self.release)

    def test_rejects_personal_data_and_external_links(self):
        secret = self.release / "Cookies"
        secret.write_bytes(b"synthetic fixture")
        with self.assertRaisesRegex(ValueError, "пользовательские"):
            validate_payload(self.release)
        secret.unlink()
        link = self.release / "external"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "ссылки"):
            validate_payload(self.release)

    def test_no_output_inside_payload_and_no_existing_installer_overwrite(self):
        with self.assertRaises(ValueError):
            create_installer(self.release, self.release / "installer", Path("ISCC.exe"))
        output = self.root / "dist"
        output.mkdir()
        existing = output / "Telecon-Windows-x64-v0.4.1-Setup.exe"
        existing.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            create_installer(self.release, output, Path("ISCC.exe"))
        self.assertEqual(existing.read_bytes(), b"keep")

    def test_paths_with_spaces_are_passed_as_single_compiler_arguments(self):
        output = self.root / "installer output"
        def compile(command, **kwargs):
            self.assertIn(f"/DPayloadDir={self.release}", command)
            self.assertIn(f"/DInstallerDir={output}", command)
            self.assertIn("/DReleaseVersion=v0.4.1", command)
            (output / "Telecon-Windows-x64-v0.4.1-Setup.exe").write_bytes(b"MZ fixture")
        with patch("build_installer.subprocess.run", side_effect=compile):
            self.assertTrue(create_installer(self.release, output, Path("ISCC.exe")).is_file())
