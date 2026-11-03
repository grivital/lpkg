import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from lpkg.model import PackageRecord
from lpkg.state import (
    apply_cached_updates,
    apply_first_seen,
    diff_snapshot,
    load_snapshot,
    read_history,
    Snapshot,
    save_snapshot,
)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_cache_is_applied_for_same_version(self):
        snapshot = Snapshot(
            checked_at=datetime(2026, 11, 3, 12, 0),
            packages={
                "uv:x": {
                    "manager": "uv",
                    "name": "x",
                    "version": "1.0",
                    "latest": "1.1",
                    "update_available": True,
                }
            },
        )
        record = PackageRecord(manager="uv", name="x", version="1.0")
        self.assertTrue(apply_cached_updates([record], snapshot))
        self.assertEqual(record.latest, "1.1")
        self.assertTrue(record.outdated)

    def test_cache_marks_update_to_known_latest_as_current(self):
        snapshot = Snapshot(
            packages={
                "uv:x": {
                    "manager": "uv",
                    "name": "x",
                    "version": "1.0",
                    "latest": "1.1",
                    "update_available": True,
                }
            }
        )
        record = PackageRecord(manager="uv", name="x", version="1.1")
        self.assertTrue(apply_cached_updates([record], snapshot))
        self.assertEqual(record.latest, "1.1")
        self.assertFalse(record.outdated)

    def test_cache_is_not_applied_to_changed_version(self):
        snapshot = Snapshot(
            packages={
                "uv:x": {
                    "manager": "uv",
                    "name": "x",
                    "version": "1.0",
                    "latest": "1.1",
                    "update_available": True,
                }
            }
        )
        record = PackageRecord(manager="uv", name="x", version="2.0")
        self.assertFalse(apply_cached_updates([record], snapshot))
        self.assertIsNone(record.latest)

    def test_save_preserves_cache_for_same_version(self):
        checked = datetime(2026, 11, 3, 12, 0)
        snapshot = Snapshot(
            checked_at=checked,
            packages={
                "uv:x": {
                    "manager": "uv",
                    "name": "x",
                    "version": "1.0",
                    "latest": "1.1",
                    "update_available": True,
                }
            },
        )
        saved = save_snapshot(
            [PackageRecord(manager="uv", name="x", version="1.0")], snapshot
        )
        self.assertEqual(saved.packages["uv:x"]["latest"], "1.1")
        self.assertEqual(saved.packages["uv:x"]["update_available"], True)
        self.assertEqual(saved.checked_at, checked)

    def test_save_drops_cache_for_unknown_version_change(self):
        snapshot = Snapshot(
            packages={
                "uv:x": {
                    "manager": "uv",
                    "name": "x",
                    "version": "1.0",
                    "latest": "1.1",
                    "update_available": True,
                }
            }
        )
        saved = save_snapshot(
            [PackageRecord(manager="uv", name="x", version="2.0")], snapshot
        )
        self.assertIsNone(saved.packages["uv:x"]["latest"])

    def test_snapshot_roundtrip_and_first_seen(self):
        records = [
            PackageRecord(manager="uv", name="debugpy", version="1.8.21"),
            PackageRecord(manager="cargo", name="b3sum", version="1.8.5"),
        ]
        snapshot = load_snapshot()
        saved = save_snapshot(records, snapshot)
        self.assertIn("uv:debugpy", saved.first_seen)

        loaded = load_snapshot()
        self.assertEqual(set(loaded.first_seen), {"uv:debugpy", "cargo:b3sum"})
        self.assertEqual(loaded.packages["uv:debugpy"]["version"], "1.8.21")

        fresh = PackageRecord(manager="uv", name="debugpy", version="1.8.21")
        apply_first_seen([fresh], loaded)
        self.assertIsNotNone(fresh.installed_at)

    def test_first_seen_is_preserved_between_scans(self):
        first = save_snapshot(
            [PackageRecord(manager="uv", name="x", version="1.0")],
            load_snapshot(),
        )
        original = first.first_seen["uv:x"]
        second = save_snapshot(
            [PackageRecord(manager="uv", name="x", version="1.1")],
            load_snapshot(),
        )
        self.assertEqual(second.first_seen["uv:x"], original)

    def test_failed_manager_is_preserved(self):
        snapshot = Snapshot(
            packages={
                "uv:x": {"manager": "uv", "name": "x", "version": "1.0"},
                "npm:y": {"manager": "npm", "name": "y", "version": "1.0"},
            }
        )
        saved = save_snapshot(
            [PackageRecord(manager="uv", name="x", version="1.1")],
            snapshot,
            preserve_managers={"npm"},
        )
        self.assertEqual(saved.packages["uv:x"]["version"], "1.1")
        self.assertIn("npm:y", saved.packages)

    def test_malformed_snapshot_reports_error(self):
        from lpkg.state import snapshot_path

        snapshot_path().parent.mkdir(parents=True)
        snapshot_path().write_text('{"first_seen": ["bad"]}', encoding="utf-8")
        snapshot = load_snapshot()
        self.assertIsNotNone(snapshot.error)
        self.assertEqual(snapshot.first_seen, {})

    def test_diff(self):
        records = [
            PackageRecord(manager="uv", name="x", version="1.1"),
            PackageRecord(manager="npm", name="new", version="2.0"),
        ]
        snapshot = load_snapshot()
        snapshot.packages = {
            "uv:x": {"manager": "uv", "name": "x", "version": "1.0"},
            "cargo:gone": {"manager": "cargo", "name": "gone", "version": "0.1"},
        }
        snapshot.first_seen = {"uv:x": "2026-01-01T00:00:00"}
        diff = diff_snapshot(records, snapshot)
        self.assertEqual([r.key for r in diff.added], ["npm:new"])
        self.assertEqual([(r.key, old) for r, old in diff.changed], [("uv:x", "1.0")])
        self.assertEqual([item["key"] for item in diff.removed], ["cargo:gone"])
        self.assertFalse(diff.empty)

    def test_empty_diff(self):
        snapshot = load_snapshot()
        snapshot.packages = {
            "uv:x": {"manager": "uv", "name": "x", "version": "1.0"}
        }
        diff = diff_snapshot(
            [PackageRecord(manager="uv", name="x", version="1.0")], snapshot
        )
        self.assertTrue(diff.empty)

    def test_history(self):
        from lpkg.state import append_history

        append_history({"manager": "uv", "action": "update", "names": ["x"], "ok": True})
        append_history({"manager": "cargo", "action": "update", "names": ["y"], "ok": False})
        entries = read_history(10)
        self.assertEqual(len(entries), 2)
        # Новые записи первыми.
        self.assertEqual(entries[0]["manager"], "cargo")
        self.assertIn("time", entries[0])


if __name__ == "__main__":
    unittest.main()
