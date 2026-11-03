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
    include_manual: bool = True
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        target = Path(path) if path is not None else config_path()
        config = cls(path=target)
        if not target.exists():
            return config
        try:
            with target.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"не удалось прочитать {target}: {exc}") from exc

        general = data.get("general", {})
        if isinstance(general, dict):
            config.color = str(general.get("color", config.color))
            config.timeout = float(general.get("timeout", config.timeout))
            config.update_timeout = float(
                general.get("update_timeout", config.update_timeout)
            )
            config.network_timeout = float(
                general.get("network_timeout", config.network_timeout)
            )
            config.pip_python = str(general.get("pip_python", config.pip_python))
            config.include_manual = bool(
                general.get("include_manual", config.include_manual)
            )

        managers = data.get("managers", {})
        if isinstance(managers, dict):
            for key, value in managers.items():
                if key in config.managers:
                    config.managers[key] = bool(value)

        scan = data.get("scan", {})
        if isinstance(scan, dict):
            if "bin_dirs" in scan:
                config.bin_dirs = [str(item) for item in scan["bin_dirs"]]
            if "extra_dirs" in scan:
                config.extra_dirs = [str(item) for item in scan["extra_dirs"]]

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
