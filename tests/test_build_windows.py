import unittest

from build_windows import windows_version


class BuildVersionTests(unittest.TestCase):
    def test_beta_label_maps_to_windows_numeric_version(self):
        self.assertEqual(windows_version("v0.3.0b"), "0.3.0.0")
        self.assertEqual(windows_version("v0.2.0b"), "0.2.0.0")
        self.assertEqual(windows_version("v0.2.0b3"), "0.2.0.3")
        self.assertEqual(windows_version("v1.0.0rc2"), "1.0.0.2")
        self.assertEqual(windows_version("0.2.0"), "0.2.0.0")

    def test_existing_four_component_version_is_supported(self):
        self.assertEqual(windows_version("2026.10.17.1"), "2026.10.17.1")

    def test_unsafe_labels_and_out_of_range_versions_are_rejected(self):
        for label in ("../v0.2.0b", "v0.2", "v0.2.0beta", "v0.2.0b/1", "1.2.3.65536", "v65536.0.0"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                windows_version(label)


if __name__ == "__main__":
    unittest.main()
