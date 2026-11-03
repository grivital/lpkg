import tempfile
import unittest
from pathlib import Path

from lpkg.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def load(self, text: str) -> Config:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return Config.load(path)

    def test_boolean_is_not_coerced(self):
        config = self.load('[general]\ninclude_manual = false\n')
        self.assertFalse(config.include_manual)
        with self.assertRaises(ConfigError):
            self.load('[general]\ninclude_manual = "false"\n')

    def test_timeout_must_be_positive_number(self):
        for value in ('"bad"', "0", "-1", "true", "nan"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.load(f"[general]\ntimeout = {value}\n")

    def test_scan_dirs_must_be_string_array(self):
        with self.assertRaises(ConfigError):
            self.load('[scan]\nbin_dirs = "~/bin"\n')
        with self.assertRaises(ConfigError):
            self.load('[scan]\nextra_dirs = [1]\n')

    def test_manager_flag_must_be_boolean(self):
        with self.assertRaises(ConfigError):
            self.load('[managers]\nnpm = "false"\n')


if __name__ == "__main__":
    unittest.main()
