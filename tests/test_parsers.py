import json
import unittest

from lpkg.adapters.cargo import parse_crates2, parse_install_update
from lpkg.adapters.flatpak_user import parse_flatpak_list
from lpkg.adapters.gem import parse_gem_list, parse_gem_outdated
from lpkg.adapters.go import parse_go_version_m
from lpkg.adapters.npm import iter_packages
from lpkg.adapters.pip_user import parse_pip_list, parse_pip_outdated
from lpkg.adapters.uv_tool import parse_uv_tool_list

import tempfile
from pathlib import Path


class UvParserTests(unittest.TestCase):
    def test_list_with_paths(self):
        text = (
            "debugpy v1.8.21 (/home/u/.local/share/uv/tools/debugpy)\n"
            "- debugpy (/home/u/.local/bin/debugpy)\n"
            "- debugpy-adapter (/home/u/.local/bin/debugpy-adapter)\n"
            "graphifyy v0.9.43 (/home/u/.local/share/uv/tools/graphifyy)\n"
            "- graphify (/home/u/.local/bin/graphify)\n"
        )
        records = parse_uv_tool_list(text)
        self.assertEqual([r.name for r in records], ["debugpy", "graphifyy"])
        self.assertEqual(records[0].version, "1.8.21")
        self.assertEqual(len(records[0].bin_paths), 2)
        self.assertEqual(records[0].install_path, "/home/u/.local/share/uv/tools/debugpy")

    def test_outdated_marker(self):
        text = "debugpy v1.8.21 [latest: 1.8.22]\n- debugpy\n"
        records = parse_uv_tool_list(text)
        self.assertEqual(records[0].latest, "1.8.22")
        self.assertTrue(records[0].outdated)


class CargoParserTests(unittest.TestCase):
    KEY = "b3sum 1.8.5 (registry+https://github.com/rust-lang/crates.io-index)"

    def test_crates2(self):
        payload = {
            "installs": {
                self.KEY: {
                    "version_req": "=1.8.5",
                    "bins": ["b3sum"],
                    "target": "x86_64-unknown-linux-gnu",
                }
            }
        }
        records = parse_crates2(payload)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.name, "b3sum")
        self.assertEqual(record.version, "1.8.5")
        self.assertEqual(record.bin_paths[0].endswith("/bin/b3sum"), True)
        self.assertEqual(record.extra["version_req"], "=1.8.5")

    def test_install_update_table(self):
        text = (
            "    Polling registry 'https://index.crates.io/'.....\n"
            "\n"
            "Package         Installed  Latest   Needs update\n"
            "b3sum           v1.8.5     v1.8.7   Yes\n"
            "cargo-outdated  v0.19.0    v0.19.0  No\n"
        )
        latest = parse_install_update(text)
        self.assertEqual(latest, {"b3sum": "1.8.7", "cargo-outdated": "0.19.0"})


class GemParserTests(unittest.TestCase):
    def test_default_gems_are_skipped(self):
        text = (
            "abbrev (default: 0.1.2)\n"
            "json (2.19.4, default: 2.7.2)\n"
            "rake (13.0.6)\n"
        )
        records = parse_gem_list(text)
        self.assertEqual([r.name for r in records], ["json", "rake"])
        self.assertEqual(records[0].version, "2.19.4")

    def test_outdated(self):
        text = "rake (13.0.6 < 13.2.1)\njson (2.19.4 < 2.19.4)\n"
        self.assertEqual(
            parse_gem_outdated(text), {"rake": "13.2.1", "json": "2.19.4"}
        )


class PipParserTests(unittest.TestCase):
    def test_list(self):
        payload = [
            {"name": "requests", "version": "2.32.0"},
            {"name": "urllib3", "version": "2.2.0"},
        ]
        records = parse_pip_list(payload)
        self.assertEqual([r.version for r in records], ["2.32.0", "2.2.0"])
        self.assertEqual(records[0].manager, "pip")

    def test_outdated(self):
        payload = [
            {"name": "requests", "version": "2.31.0", "latest_version": "2.32.0"}
        ]
        self.assertEqual(parse_pip_outdated(payload), {"requests": "2.32.0"})


class GoParserTests(unittest.TestCase):
    def test_buildinfo(self):
        text = (
            "/home/u/bin/gh: go1.27.3\n"
            "\tpath\tgithub.com/cli/cli/v2/cmd/gh\n"
            "\tmod\tgithub.com/cli/cli/v2\tv2.103.2\t\n"
            "\tdep\tgithub.com/x/y\tv1.0.0\th1:abc=\n"
        )
        self.assertEqual(
            parse_go_version_m(text), ("github.com/cli/cli/v2", "v2.103.2")
        )

    def test_devel_version(self):
        text = "\tmod\texample.com/x\t(devel)\t\n"
        self.assertEqual(parse_go_version_m(text), ("example.com/x", None))

    def test_no_buildinfo(self):
        self.assertIsNone(parse_go_version_m("not a go binary"))


class FlatpakParserTests(unittest.TestCase):
    def test_list(self):
        payload = [
            {
                "application": "org.telegram.desktop",
                "name": "Telegram",
                "version": "7.3.3",
                "origin": "flathub",
                "branch": "stable",
            }
        ]
        records = parse_flatpak_list(payload)
        self.assertEqual(records[0].name, "org.telegram.desktop")
        self.assertEqual(records[0].version, "7.3.3")
        self.assertEqual(records[0].source, "flathub")
        self.assertEqual(records[0].extra["display_name"], "Telegram")

    def test_empty(self):
        self.assertEqual(parse_flatpak_list([]), [])
        self.assertEqual(parse_flatpak_list(None), [])


class NpmIteratorTests(unittest.TestCase):
    def test_scoped_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "@scope" / "pkg").mkdir(parents=True)
            (root / "@scope" / "pkg" / "package.json").write_text(
                json.dumps({"name": "@scope/pkg", "version": "1.0.0"})
            )
            (root / "plain").mkdir()
            names = sorted(p.name for p in iter_packages(root))
            self.assertEqual(names, ["pkg", "plain"])


if __name__ == "__main__":
    unittest.main()
