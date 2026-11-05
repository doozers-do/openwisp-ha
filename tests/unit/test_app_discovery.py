"""Regressions for filenames that Home Assistant Supervisor treats as apps."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from check_app_discovery import find_configs


class DiscoveryTests(unittest.TestCase):
    def test_example_config_suffix_is_discovered(self):
        with tempfile.TemporaryDirectory(prefix="ha-discovery-") as folder:
            root = Path(folder)
            filenames = (
                "openwisp/config.yaml",
                "examples/redis/config.example.yaml",
                "examples/probe/config.example.yaml",
            )
            for name in filenames:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual(find_configs(root), sorted(filenames),
                             "config.example.yaml must be counted as an app, as Supervisor does")

    def test_non_config_metadata_does_not_create_apps(self):
        with tempfile.TemporaryDirectory(prefix="ha-discovery-") as folder:
            root = Path(folder)
            for name in (
                "openwisp/config.yaml",
                "examples/redis/app-metadata.example.yaml",
                "examples/probe/app-metadata.example.yaml",
                ".hidden/config.yaml",
                "openwisp/rootfs/config.json",
                "notes/config.txt",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual(find_configs(root), ["openwisp/config.yaml"],
                             "Only the app definition should be discoverable")


if __name__ == "__main__":
    unittest.main()
