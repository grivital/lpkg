import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from lpkg.cli import (
    Context,
    cmd_info,
    cmd_list,
    cmd_outdated,
    cmd_uninstall,
    cmd_update,
)
from lpkg.config import Config
from lpkg.model import ActionResult, Issue, PackageRecord
from lpkg.registry import ManagerError, ScanResult, collect
from lpkg.state import Snapshot
from lpkg.ui import Style


class UninstallTests(unittest.TestCase):
    def test_dry_run_does_not_write_history(self):
        record = PackageRecord(manager="uv", name="x", version="1.0")
        adapter = Mock()
        adapter.uninstall.return_value = [
            ActionResult(manager="uv", action="uninstall", names=["x"], message="dry-run")
        ]
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(targets=["uv:x"], dry_run=True, yes=True, json=False),
            adapters={"uv": adapter},
            style=Style("never"),
            json=False,
            collect=Mock(return_value=SimpleNamespace(records=[record])),
            record_history=Mock(),
            save=Mock(),
        )
        with redirect_stdout(io.StringIO()):
            cmd_uninstall(ctx)
        ctx.record_history.assert_not_called()
        ctx.save.assert_not_called()


class OutdatedCommandTests(unittest.TestCase):
    def test_check_failure_returns_error_and_not_current_message(self):
        record = PackageRecord(manager="npm", name="x", version="1.0")
        result = ScanResult(
            records=[record],
            errors=[ManagerError("npm", "network failed")],
        )
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(manager=None, json=False),
            style=Style("never"),
            json=False,
            collect=Mock(return_value=result),
            save=Mock(),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            status = cmd_outdated(ctx)
        self.assertEqual(status, 1)
        self.assertIn("Не удалось проверить", output.getvalue())
        self.assertNotIn("Всё актуально", output.getvalue())


class UpdateCommandTests(unittest.TestCase):
    def test_failed_check_is_not_successful_noop(self):
        record = PackageRecord(manager="npm", name="x", version="1.0")
        result = ScanResult(
            records=[record], errors=[ManagerError("npm", "network failed")]
        )
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(
                all=True,
                targets=[],
                latest=False,
                dry_run=True,
                yes=False,
                json=False,
            ),
            adapters={},
            style=Style("never"),
            json=False,
            collect=Mock(return_value=result),
        )
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            status = cmd_update(ctx)
        self.assertEqual(status, 1)


class ListCommandTests(unittest.TestCase):
    def run_list(self, *, configured: bool, all_flag: bool = False):
        records = [
            PackageRecord(manager="uv", name="x"),
            PackageRecord(manager="manual", name="y"),
        ]
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(
                check=False,
                outdated=False,
                manual=False,
                all=all_flag,
                manager=None,
                paths=False,
                json=True,
            ),
            config=Config(include_manual=configured),
            style=Style("never"),
            json=True,
            collect=Mock(return_value=ScanResult(records=records)),
            save=Mock(),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            cmd_list(ctx)
        return ctx, json.loads(output.getvalue())

    def test_config_enables_manual_records(self):
        ctx, payload = self.run_list(configured=True)
        self.assertEqual([item["name"] for item in payload], ["x", "y"])
        ctx.collect.assert_called_once_with(check_updates=False, include_manual=True)

    def test_all_overrides_disabled_config(self):
        ctx, payload = self.run_list(configured=False, all_flag=True)
        self.assertEqual([item["name"] for item in payload], ["x", "y"])
        ctx.collect.assert_called_once_with(check_updates=False, include_manual=True)

    def test_manual_is_hidden_by_default(self):
        ctx, payload = self.run_list(configured=False)
        self.assertEqual([item["name"] for item in payload], ["x"])
        ctx.collect.assert_called_once_with(check_updates=False, include_manual=False)


class ManualRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = PackageRecord(
            manager="manual",
            name="gh",
            version="2.0.0",
            install_path="/home/user/.local/bin/gh",
        )

    def test_info_finds_manual_record(self):
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(targets=["gh"], check=False, json=True),
            collect=Mock(return_value=ScanResult(records=[self.record])),
            json=True,
            style=Style("never"),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            status = cmd_info(ctx)
        self.assertEqual(status, 0)
        ctx.collect.assert_called_once_with(check_updates=False, include_manual=True)
        self.assertEqual(json.loads(output.getvalue())[0]["key"], "manual:gh")

    def test_uninstall_explains_manual_record(self):
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(targets=["gh"], dry_run=True, yes=True, json=False),
            adapters={},
            style=Style("never"),
            json=False,
            collect=Mock(return_value=ScanResult(records=[self.record])),
            record_history=Mock(),
            save=Mock(),
        )
        errors = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(errors):
            status = cmd_uninstall(ctx)
        self.assertEqual(status, 2)
        ctx.collect.assert_called_once_with(check_updates=False, include_manual=True)
        self.assertIn("установлен вручную", errors.getvalue())

    def test_update_explains_manual_record(self):
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(
                all=False,
                targets=["gh"],
                latest=False,
                dry_run=True,
                yes=False,
                json=False,
            ),
            adapters={},
            style=Style("never"),
            json=False,
            collect=Mock(return_value=ScanResult(records=[self.record])),
        )
        errors = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(errors):
            status = cmd_update(ctx)
        self.assertEqual(status, 2)
        ctx.collect.assert_called_once_with(check_updates=True, include_manual=True)
        self.assertIn("установлен вручную", errors.getvalue())


class JsonOutputTests(unittest.TestCase):
    def _adapter(self):
        adapter = Mock()
        adapter.is_available.return_value = True
        adapter.update.return_value = [
            ActionResult(manager="uv", action="update", names=["x"], ok=True, message="ok")
        ]
        adapter.uninstall.return_value = [
            ActionResult(
                manager="uv", action="uninstall", names=["x"], ok=True, message="ok"
            )
        ]
        return adapter

    def test_update_json_is_valid_without_plan_text(self):
        record = PackageRecord(manager="uv", name="x", version="1.0", latest="1.1")
        adapter = self._adapter()
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(
                all=False,
                targets=["uv:x"],
                latest=False,
                dry_run=False,
                yes=True,
                json=True,
            ),
            adapters={"uv": adapter},
            style=Style("never"),
            json=True,
            collect=Mock(
                side_effect=[ScanResult(records=[record]), ScanResult(records=[record])]
            ),
            record_history=Mock(),
            save=Mock(),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            status = cmd_update(ctx)
        self.assertEqual(status, 0)
        self.assertNotIn("Планируется", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())[0]["action"], "update")

    def test_uninstall_json_is_valid_without_target_list(self):
        record = PackageRecord(manager="uv", name="x", version="1.0")
        adapter = self._adapter()
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(
                targets=["uv:x"], dry_run=False, yes=True, json=True
            ),
            adapters={"uv": adapter},
            style=Style("never"),
            json=True,
            collect=Mock(
                side_effect=[ScanResult(records=[record]), ScanResult(records=[])]
            ),
            record_history=Mock(),
            save=Mock(),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            status = cmd_uninstall(ctx)
        self.assertEqual(status, 0)
        self.assertNotIn("uv:x 1", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())[0]["action"], "uninstall")


class EmptyOutdatedTests(unittest.TestCase):
    def run_outdated(self, result: ScanResult) -> tuple[int, str]:
        ctx: Any = SimpleNamespace(
            args=SimpleNamespace(manager=None, json=False),
            style=Style("never"),
            json=False,
            collect=Mock(return_value=result),
            save=Mock(),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            status = cmd_outdated(ctx)
        return status, output.getvalue()

    def test_all_managers_unavailable_is_not_all_current(self):
        result = ScanResult(unavailable=[ManagerError("npm", "не найдена команда npm")])
        status, output = self.run_outdated(result)
        self.assertEqual(status, 0)
        self.assertIn("Нет пакетов для проверки", output)
        self.assertIn("Недоступны менеджеры", output)
        self.assertNotIn("Всё актуально", output)

    def test_no_managers_enabled_is_not_all_current(self):
        status, output = self.run_outdated(ScanResult())
        self.assertEqual(status, 0)
        self.assertIn("Нет пакетов для проверки", output)
        self.assertNotIn("Всё актуально", output)


class StateErrorVisibilityTests(unittest.TestCase):
    def test_context_prints_state_error(self):
        ctx: Any = Context.__new__(Context)
        ctx.args = SimpleNamespace(verbose=False, json=False)
        ctx.config = Config()
        ctx.adapters = {}
        ctx.style = Style("never")
        errors = io.StringIO()
        with (
            patch(
                "lpkg.cli.collect",
                return_value=ScanResult(
                    issues=[Issue("state-error", "snapshot broken", severity="error")]
                ),
            ),
            redirect_stderr(errors),
        ):
            ctx.collect()
        self.assertIn("snapshot broken", errors.getvalue())


class TuiManualTests(unittest.TestCase):
    def test_package_list_includes_manual(self):
        from lpkg.tui import _show_packages

        ctx: Any = SimpleNamespace(collect=Mock(return_value=ScanResult(records=[])))
        with (
            patch("lpkg.tui._infobox"),
            patch("lpkg.tui._menu", return_value="back"),
        ):
            _show_packages(ctx)
        ctx.collect.assert_called_once_with(check_updates=False, include_manual=True)


class RegistryTests(unittest.TestCase):
    def test_unavailable_manager_is_not_scan_error(self):
        adapter = Mock()
        adapter.id = "npm"
        adapter.is_available.return_value = False
        adapter.unavailable_reason.return_value = "не найдена команда npm"
        result = collect(
            Config(),
            adapters=[adapter],
            include_manual=False,
            snapshot=Snapshot.empty(),
        )
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.unavailable), 1)


class TuiTests(unittest.TestCase):
    def test_single_update_requires_confirmation(self):
        record = PackageRecord(manager="uv", name="x", version="1.0")
        ctx: Any = SimpleNamespace(adapters={"uv": Mock()})
        ctx.adapters["uv"].update_command.return_value = ["uv", "tool", "upgrade", "x"]
        with (
            patch("lpkg.tui._menu", side_effect=["update", "back"]),
            patch("lpkg.tui._yesno", return_value=False),
            patch("lpkg.tui._run_update") as run_update,
        ):
            from lpkg.tui import _package_menu

            _package_menu(ctx, record)
        run_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
