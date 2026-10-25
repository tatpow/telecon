import plistlib
import tempfile
import unittest
from pathlib import Path

from build_desktop import bundle_executable, compilation_command, target_name


class NativeBuildTests(unittest.TestCase):
    def test_supported_native_architectures_and_rejection_of_cross_builds(self):
        self.assertEqual(target_name("darwin", "arm64"), ("macOS", "arm64"))
        self.assertEqual(target_name("darwin", "x86_64"), ("macOS", "x64"))
        self.assertEqual(target_name("linux", "AMD64"), ("Linux", "x64"))
        for system, architecture in (("win32", "AMD64"), ("linux", "arm64"), ("darwin", "i386")):
            with self.subTest(system=system, architecture=architecture), self.assertRaises(ValueError):
                target_name(system, architecture)

    def test_mac_build_includes_cocoa_and_actual_app_bundle(self):
        command = compilation_command("darwin", "v0.4.0b", Path("build/native"))
        self.assertIn("--macos-create-app-bundle", command)
        self.assertIn("--include-module=webview.platforms.cocoa", command)
        self.assertIn("--macos-app-version=0.4.0", command)
        self.assertNotIn("--enable-plugin=pyside6", command)

    def test_linux_build_uses_qt_plugin_and_local_ui(self):
        command = compilation_command("linux", "v0.4.0b", Path("build/native"))
        self.assertIn("--enable-plugin=pyside6", command)
        self.assertIn("--include-module=webview.platforms.qt", command)
        self.assertIn("--include-data-dir=ui=ui", command)
        self.assertNotIn("--macos-create-app-bundle", command)

    def test_bundle_executable_comes_from_plist_and_must_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Telecon.app"
            binary = app / "Contents" / "MacOS" / "Telecon"
            binary.parent.mkdir(parents=True)
            info = app / "Contents" / "Info.plist"
            info.write_bytes(plistlib.dumps({"CFBundleExecutable": "Telecon"}))
            with self.assertRaises(FileNotFoundError):
                bundle_executable(app)
            binary.write_bytes(b"fixture")
            self.assertEqual(bundle_executable(app), binary)
            info.write_bytes(plistlib.dumps({"CFBundleExecutable": "../outside"}))
            with self.assertRaises(ValueError):
                bundle_executable(app)
