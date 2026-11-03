import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lpkg.config import Config
from lpkg.model import PackageRecord
from lpkg.registry import _bin_dirs
from lpkg.scan import scan_bin_dirs


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        def fake_home(value: str) -> Path:
            if value.startswith("~/"):
                return self.root / value[2:]
            return Path(value)

        self.home_patch = patch("lpkg.scan.home_path", side_effect=fake_home)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.tmp.cleanup()

    def _script(self, directory: Path, name: str, text: str = "#!/bin/sh\nexit 0\n") -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_owned_symlink_is_not_manual(self):
        tools = self.root / "tools" / "debugpy" / "bin"
        tools.mkdir(parents=True)
        target = self._script(tools, "debugpy")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        link = bin_dir / "debugpy"
        link.symlink_to(target)

        record = PackageRecord(
            manager="uv", name="debugpy", bin_paths=[str(link)]
        )
        outcome = scan_bin_dirs([record], [bin_dir], path_env="")
        self.assertEqual(outcome.manual, [])

    def test_broken_symlink_reported(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "broken").symlink_to(self.root / "nowhere")

        outcome = scan_bin_dirs([], [bin_dir], path_env="")
        codes = [issue.code for issue in outcome.issues]
        self.assertIn("broken-symlink", codes)

    def test_manual_script_detected(self):
        bin_dir = self.root / "bin"
        self._script(bin_dir, "myscript", "#!/bin/sh\necho hi\n")

        outcome = scan_bin_dirs([], [bin_dir], path_env="")
        self.assertEqual(len(outcome.manual), 1)
        record = outcome.manual[0]
        self.assertEqual(record.manager, "manual")
        self.assertEqual(record.name, "myscript")
        self.assertEqual(record.extra["kind"], "script")

    def test_symlink_into_manager_dir_hints_owner(self):
        tools = self.root / "uv" / "tools" / "x" / "bin"
        tools.mkdir(parents=True)
        target = self._script(tools, "x")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        link = bin_dir / "x"
        link.symlink_to(target)

        outcome = scan_bin_dirs([], [bin_dir], path_env="")
        self.assertEqual(len(outcome.manual), 1)
        self.assertEqual(outcome.manual[0].extra.get("hint"), "uv")

    def test_duplicates_reported(self):
        first = self.root / "bin1"
        second = self.root / "bin2"
        self._script(first, "foo")
        self._script(second, "foo")

        outcome = scan_bin_dirs([], [first, second], path_env="")
        codes = [issue.code for issue in outcome.issues]
        self.assertIn("duplicate-bin", codes)

    def test_duplicate_config_dirs_are_scanned_once(self):
        self._script(self.root, "tool")
        config = Config(bin_dirs=[str(self.root)], extra_dirs=[str(self.root)])
        dirs = _bin_dirs(config, [])
        self.assertEqual(dirs, [self.root])
        outcome = scan_bin_dirs([], dirs, path_env="")
        self.assertEqual(len(outcome.manual), 1)
        self.assertNotIn(
            "duplicate-bin", [issue.code for issue in outcome.issues]
        )

    def test_stale_receipt_reported(self):
        receipt_dir = self.root / ".config" / "uv"
        receipt_dir.mkdir(parents=True)
        (receipt_dir / "uv-receipt.json").write_text(
            json.dumps(
                {
                    "binaries": ["uv", "uvx"],
                    "install_prefix": str(self.root / ".local" / "bin"),
                    "version": "0.9.17",
                    "source": {"app_name": "uv"},
                }
            ),
            encoding="utf-8",
        )
        outcome = scan_bin_dirs([], [], path_env="")
        codes = [issue.code for issue in outcome.issues]
        self.assertIn("stale-receipt", codes)

    def test_live_receipt_not_reported(self):
        receipt_dir = self.root / ".config" / "uv"
        receipt_dir.mkdir(parents=True)
        bindir = self.root / ".local" / "bin"
        self._script(bindir, "uv")
        (receipt_dir / "uv-receipt.json").write_text(
            json.dumps(
                {
                    "binaries": ["uv"],
                    "install_prefix": str(bindir),
                    "version": "0.9.17",
                }
            ),
            encoding="utf-8",
        )
        outcome = scan_bin_dirs([], [], path_env="")
        self.assertNotIn("stale-receipt", [i.code for i in outcome.issues])


if __name__ == "__main__":
    unittest.main()
