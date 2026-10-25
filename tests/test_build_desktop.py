import plistlib
import tempfile
import unittest
from pathlib import Path

from build_desktop import (bundle_executable, compilation_command, target_name,
                           relocate_macos_data, configure_linux_qt, configure_macos_transport)


class NativeBuildTests(unittest.TestCase):
    def test_macos_transport_allows_loopback_without_disabling_ats(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Telecon.app"
            info = app / "Contents" / "Info.plist"
            info.parent.mkdir(parents=True)
            original = {"CFBundleExecutable": "Telecon", "CFBundleShortVersionString": "0.4.1",
                        "NSAppTransportSecurity": {"NSExceptionDomains": {
                            "example.org": {"NSIncludesSubdomains": False}}}}
            info.write_bytes(plistlib.dumps(original))
            configure_macos_transport(app)
            configure_macos_transport(app)
            configured = plistlib.loads(info.read_bytes())
            self.assertEqual(configured["CFBundleExecutable"], "Telecon")
            self.assertEqual(configured["CFBundleShortVersionString"], "0.4.1")
            policy = configured["NSAppTransportSecurity"]
            self.assertNotIn("NSAllowsArbitraryLoads", policy)
            self.assertNotIn("NSAllowsArbitraryLoadsInWebContent", policy)
            self.assertNotIn("NSAllowsLocalNetworking", policy)
            self.assertEqual(policy["NSExceptionDomains"]["127.0.0.1"], {
                "NSExceptionAllowsInsecureHTTPLoads": True, "NSIncludesSubdomains": False})
            self.assertEqual(policy["NSExceptionDomains"]["example.org"], {"NSIncludesSubdomains": False})

    def test_linux_qt_configuration_uses_the_packaged_plugins(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            with self.assertRaises(FileNotFoundError):
                configure_linux_qt(release)
            plugin = release / "PySide6" / "qt-plugins" / "platforms" / "libqxcb.so"
            plugin.parent.mkdir(parents=True)
            plugin.write_bytes(b"ELF fixture")
            configure_linux_qt(release)
            self.assertIn("Plugins = PySide6/qt-plugins", (release / "qt6.conf").read_text())
            self.assertEqual((release / "qt.conf").read_bytes(), (release / "qt6.conf").read_bytes())

    def test_macos_data_moves_but_binary_locations_and_resource_lookups_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Telecon.app"
            code = app / "Contents" / "MacOS"
            ui = code / "ui" / "index.html"
            ui.parent.mkdir(parents=True)
            ui.write_text("<html>fixture</html>")
            binary = code / "Telecon"
            binary.write_bytes(b"\xcf\xfa\xed\xfe" + b"native fixture")
            extension = code / "package" / "extension.so"
            extension.parent.mkdir()
            extension.write_bytes(b"\xfe\xed\xfa\xcf" + b"extension fixture")
            data = extension.parent / "settings.json"
            data.write_text('{"fixture":true}')
            # A package whose executable is a link must stay at its code location.
            helper_dir = code / "helper"
            helper_dir.mkdir()
            (helper_dir / "engine").symlink_to(Path("..") / "Telecon")
            moved = relocate_macos_data(app)
            self.assertIn("ui", moved)
            self.assertIn("package/settings.json", moved)
            self.assertFalse(binary.is_symlink())
            self.assertFalse(extension.is_symlink())
            self.assertFalse(helper_dir.is_symlink())
            self.assertTrue(ui.parent.is_symlink())
            self.assertEqual(ui.read_text(), "<html>fixture</html>")
            self.assertEqual(data.read_text(), '{"fixture":true}')
            self.assertTrue((app / "Contents" / "Resources" / "ui" / "index.html").is_file())
            self.assertEqual(relocate_macos_data(app), [])

    def test_macos_relocation_refuses_to_overwrite_existing_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Telecon.app"
            code = app / "Contents" / "MacOS"
            resources = app / "Contents" / "Resources"
            code.mkdir(parents=True)
            resources.mkdir()
            (code / "file.txt").write_text("new")
            (resources / "file.txt").write_text("original")
            with self.assertRaises(FileExistsError):
                relocate_macos_data(app)
            self.assertEqual((resources / "file.txt").read_text(), "original")

    def test_macos_relocation_rejects_resources_outside_the_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Telecon.app"
            code = app / "Contents" / "MacOS"
            code.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (app / "Contents" / "Resources").symlink_to(outside, target_is_directory=True)
            (code / "file.txt").write_text("original")
            with self.assertRaises(ValueError):
                relocate_macos_data(app)
            self.assertEqual((code / "file.txt").read_text(), "original")
            self.assertEqual(list(outside.iterdir()), [])

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
