"""Конфигурация lpkg: ``~/.config/lpkg/config.toml``.

Все параметры имеют безопасные значения по умолчанию, поэтому отсутствие
файла — нормальная ситуация.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .proc import home_path

DEFAULT_BIN_DIRS = [
    "~/.local/bin",
    "~/bin",
    "~/.cargo/bin",
    "~/.npm/bin",
    "~/go/bin",
]

DEFAULT_MANAGERS = {
    "uv": True,
    "cargo": True,
    "npm": True,
    "gem": True,
    "pip": True,
    "go": True,
    "flatpak": True,
}


class ConfigError(Exception):
    """Конфигурация существует, но не может быть прочитана."""


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "lpkg" / "config.toml"


@dataclass
class Config:
    managers: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_MANAGERS))
    bin_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_BIN_DIRS))
    extra_dirs: list[str] = field(default_factory=list)
    timeout: float = 120.0
    update_timeout: float = 3600.0
    network_timeout: float = 300.0
    color: str = "auto"  # auto | always | never
    pip_python: str = "python3"
    include_manual: bool = False
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        target = Path(path) if path is not None else config_path()
        config = cls(path=target)
        try:
            if not target.exists():
                return config
            with target.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"не удалось прочитать {target}: {exc}") from exc

        general = _section(data, "general")
        config.color = _string(general, "color", config.color)
        if config.color not in ("auto", "always", "never"):
            raise ConfigError("general.color должен быть auto, always или never")
        config.timeout = _timeout(general, "timeout", config.timeout)
        config.update_timeout = _timeout(
            general, "update_timeout", config.update_timeout
        )
        config.network_timeout = _timeout(
            general, "network_timeout", config.network_timeout
        )
        config.pip_python = _string(general, "pip_python", config.pip_python)
        if not config.pip_python.strip():
            raise ConfigError("general.pip_python не должен быть пустым")
        config.include_manual = _boolean(
            general, "include_manual", config.include_manual
        )

        managers = _section(data, "managers")
        for key, value in managers.items():
            if key in config.managers:
                if not isinstance(value, bool):
                    raise ConfigError(f"managers.{key} должен быть булевым")
                config.managers[key] = value

        scan = _section(data, "scan")
        config.bin_dirs = _string_list(scan, "bin_dirs", config.bin_dirs)
        config.extra_dirs = _string_list(scan, "extra_dirs", config.extra_dirs)
        return config

    # ------------------------------------------------------------------ пути

    def resolved_bin_dirs(self) -> list[Path]:
        """Существующие каталоги с бинарями, без дублей, в порядке конфига."""
        seen: set[str] = set()
        result: list[Path] = []
        for raw in self.bin_dirs:
            path = home_path(raw)
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen or not path.is_dir():
                continue
            seen.add(key)
            result.append(path)
        return result

    def resolved_extra_dirs(self) -> list[Path]:
        return [p for p in (home_path(raw) for raw in self.extra_dirs) if p.is_dir()]


def _section(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{key} должен быть таблицей TOML")
    return value


def _string(section: dict, key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"general.{key} должен быть строкой")
    return value


def _boolean(section: dict, key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"general.{key} должен быть булевым")
    return value


def _timeout(section: dict, key: str, default: float) -> float:
    from math import isfinite

    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"general.{key} должен быть числом")
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ConfigError(f"general.{key} должен быть положительным конечным числом")
    return result


def _string_list(section: dict, key: str, default: list[str]) -> list[str]:
    value = section.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"scan.{key} должен быть массивом строк")
    return list(value)
