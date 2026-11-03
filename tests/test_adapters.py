import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lpkg.adapters.cargo import CargoAdapter
from lpkg.adapters.gem import GemAdapter
from lpkg.adapters.go import GoAdapter
from lpkg.adapters.npm import NpmAdapter
from lpkg.model import PackageRecord
from lpkg.proc import RunResult


class OutdatedCheckTests(unittest.TestCase):
    def test_npm_failure_is_not_current(self):
        adapter = NpmAdapter()
        adapter.run_command = Mock(
            return_value=RunResult(cmd=["npm"], ok=False, code=2, err="network failed")
        )
        record = PackageRecord(manager="npm", name="x", version="1.0.0")
        with self.assertRaises(RuntimeError):
            adapter.check_outdated([record])
        self.assertIsNone(record.latest)
        self.assertIsNone(record.outdated)

    def test_npm_outdated_status_comes_from_npm(self):
        adapter = NpmAdapter()
        adapter.run_command = Mock(
            return_value=RunResult(
                cmd=["npm"],
                ok=False,
                code=1,
                out='{"x": {"latest": "2.0.0"}}',
            )
        )
        outdated = PackageRecord(manager="npm", name="x", version="1.0.0")
        current = PackageRecord(manager="npm", name="y", version="1.0.0")
        adapter.check_outdated([outdated, current])
        self.assertTrue(outdated.outdated)
        self.assertFalse(current.outdated)

    def test_go_failure_is_reported(self):
        adapter = GoAdapter()
        adapter.run_command = Mock(
            return_value=RunResult(cmd=["go"], ok=False, code=1, err="network failed")
        )
        record = PackageRecord(
            manager="go", name="x", version="1.0.0", extra={"module": "example.com/x"}
        )
        with self.assertRaises(RuntimeError):
            adapter.check_outdated([record])

    def test_go_lower_latest_is_not_update(self):
        adapter = GoAdapter()
        adapter.run_command = Mock(
            return_value=RunResult(cmd=["go"], ok=True, code=0, out='{"Version": "v1.9.0"}')
        )
        record = PackageRecord(
            manager="go", name="x", version="v2.0.0", extra={"module": "example.com/x"}
        )
        adapter.check_outdated([record])
        self.assertFalse(record.outdated)

    def test_npm_lower_latest_is_not_update(self):
        adapter = NpmAdapter()
        adapter.run_command = Mock(
            return_value=RunResult(
                cmd=["npm"],
                ok=False,
                code=1,
                out='{"x": {"latest": "0.9.0"}}',
            )
        )
        record = PackageRecord(manager="npm", name="x", version="1.0.0")
        adapter.check_outdated([record])
        self.assertFalse(record.outdated)


class CargoCheckTests(unittest.TestCase):
    TABLE = (
        "Package  Installed  Latest  Needs update\n"
        "x        v1.0.0     v1.1.0  Yes\n"
    )

    def test_nonzero_with_table_is_accepted(self):
        adapter = CargoAdapter()
        adapter.run_command = Mock(
            return_value=RunResult(
                cmd=["cargo"], ok=False, code=1, out=self.TABLE
            )
        )
        record = PackageRecord(manager="cargo", name="x", version="1.0.0")
        with patch("lpkg.adapters.cargo.which", return_value="/usr/bin/cargo-install-update"):
            adapter.check_outdated([record])
        self.assertTrue(record.outdated)

    def test_nonzero_without_table_is_error(self):
        adapter = CargoAdapter()
        adapter.run_command = Mock(
            return_value=RunResult(
                cmd=["cargo"], ok=False, code=2, err="registry unavailable"
            )
        )
        record = PackageRecord(manager="cargo", name="x", version="1.0.0")
        with patch("lpkg.adapters.cargo.which", return_value="/usr/bin/cargo-install-update"):
            with self.assertRaises(RuntimeError):
                adapter.check_outdated([record])


class NpmPrefixTests(unittest.TestCase):
    def test_system_prefix_is_reported(self):
        adapter = NpmAdapter()
        adapter._memo["prefix"] = Path("/usr")
        with patch("lpkg.adapters.npm.Path.home", return_value=Path("/home/user")):
            issues = adapter.issues([])
        self.assertEqual([issue.code for issue in issues], ["npm-system-prefix"])


class ExecutionEnvironmentTests(unittest.TestCase):
    def test_gem_update_uses_user_environment(self):
        adapter = GemAdapter()
        adapter._memo["env_info"] = {
            "user_dir": "/home/user/.local/share/gem",
            "bindir": "/home/user/.local/bin",
        }
        adapter.run_command = Mock(
            return_value=RunResult(cmd=["gem"], ok=True, code=0)
        )
        record = PackageRecord(manager="gem", name="rake", version="1.0")
        result = adapter.update([record])
        self.assertTrue(result[0].ok)
        env = adapter.run_command.call_args.kwargs["env"]
        self.assertEqual(env["GEM_HOME"], "/home/user/.local/share/gem")
        self.assertEqual(env["GEM_PATH"], "/home/user/.local/share/gem")

    def test_npm_system_prefix_is_not_managed(self):
        adapter = NpmAdapter()
        adapter._memo["prefix"] = Path("/usr")
        with patch("lpkg.adapters.npm.Path.home", return_value=Path("/home/user")):
            self.assertIsNone(adapter.install_command("x"))
            record = PackageRecord(manager="npm", name="x")
            self.assertIsNone(adapter.update_command(record))
            self.assertIsNone(adapter.uninstall_command(record))


if __name__ == "__main__":
    unittest.main()
