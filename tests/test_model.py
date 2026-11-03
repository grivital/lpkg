import unittest
from datetime import datetime

from lpkg.model import (
    PackageRecord,
    human_age,
    max_version,
    normalize_version,
    sort_records,
    version_key,
    versions_equal,
)


class VersionTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_version("v1.2.3"), "1.2.3")
        self.assertEqual(normalize_version(" 1.2.3 "), "1.2.3")

    def test_equal_forms(self):
        self.assertTrue(versions_equal("1.2", "1.2.0"))
        self.assertTrue(versions_equal("v1.2.3", "1.2.3"))
        self.assertFalse(versions_equal("1.2.3", "1.2.4"))

    def test_non_numeric_fallback(self):
        self.assertTrue(versions_equal("abc", "abc"))
        self.assertFalse(versions_equal("abc", "abd"))

    def test_key_trims_trailing_zeroes(self):
        self.assertEqual(version_key("1.2.0"), (1, 2))
        self.assertEqual(version_key("0.0.1"), (0, 0, 1))

    def test_max_version(self):
        self.assertEqual(max_version(["1.9.0", "1.10.0", "1.2"]), "1.10.0")
        self.assertIsNone(max_version([]))


class RecordTests(unittest.TestCase):
    def test_outdated(self):
        record = PackageRecord(manager="uv", name="x", version="1.0.0", latest="1.0.0")
        self.assertFalse(record.outdated)
        record.latest = "1.1.0"
        self.assertTrue(record.outdated)
        record.latest = None
        self.assertIsNone(record.outdated)

    def test_key_and_to_dict(self):
        record = PackageRecord(manager="cargo", name="b3sum", version="1.8.5")
        self.assertEqual(record.key, "cargo:b3sum")
        payload = record.to_dict()
        self.assertEqual(payload["key"], "cargo:b3sum")
        self.assertEqual(payload["version"], "1.8.5")
        self.assertIsNone(payload["installed_at"])

    def test_add_warning_deduplicates(self):
        record = PackageRecord(manager="npm", name="x")
        record.add_warning("a", "сообщение")
        record.add_warning("a", "сообщение")
        self.assertEqual(len(record.warnings), 1)

    def test_sort_records(self):
        records = [
            PackageRecord(manager="manual", name="z"),
            PackageRecord(manager="cargo", name="b"),
            PackageRecord(manager="uv", name="a"),
        ]
        ordered = sort_records(records)
        self.assertEqual([r.key for r in ordered], ["uv:a", "cargo:b", "manual:z"])


class AgeTests(unittest.TestCase):
    def test_human_age(self):
        now = datetime(2026, 1, 4)
        self.assertEqual(human_age(datetime(2026, 1, 4), now=now), "только что")
        self.assertEqual(human_age(datetime(2026, 1, 1), now=now), "3 дня")
        self.assertEqual(human_age(datetime(2025, 12, 1), now=now), "1 месяц")
        self.assertEqual(human_age(None), "—")


if __name__ == "__main__":
    unittest.main()
