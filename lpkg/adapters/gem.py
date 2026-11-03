"""gem — пользовательские гемы Ruby (``~/.local/share/gem``)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..model import PackageRecord, max_version
from ..proc import home_path
from .base import Adapter

_GEM_RE = re.compile(r"^(?P<name>\S+) \((?P<versions>.+)\)\s*$")
_OUTDATED_RE = re.compile(
    r"^(?P<name>\S+) \((?P<old>\S+) < (?P<new>\S+)\)\s*$"
)
_USER_DIR_RE = re.compile(r"USER INSTALLATION DIRECTORY:\s*(?P<path>.+)$")
_BINDIR_RE = re.compile(r"--bindir\s+(?P<path>\S+)")


def parse_gem_list(text: str) -> list[PackageRecord]:
    records: list[PackageRecord] = []
    for line in text.splitlines():
        match = _GEM_RE.match(line.strip())
        if not match:
            continue
        versions = [part.strip() for part in match.group("versions").split(",")]
        # ``gem list`` всегда показывает встроенные (default) гемы Ruby, даже
        # при пустом GEM_HOME. Они не установлены пользователем — пропускаем.
        installed = [v for v in versions if not v.startswith("default:")]
        if not installed:
            continue
        version = max_version(installed)
        records.append(
            PackageRecord(
                manager="gem",
                name=match.group("name"),
                version=version,
                source="https://rubygems.org/gems/" + match.group("name"),
                extra={"versions": versions},
            )
        )
    return records


def parse_gem_outdated(text: str) -> dict[str, str]:
    latest: dict[str, str] = {}
    for line in text.splitlines():
        match = _OUTDATED_RE.match(line.strip())
        if match:
            latest[match.group("name")] = match.group("new")
    return latest


class GemAdapter(Adapter):
    id = "gem"
    label = "gem"
    command = "gem"

    # ------------------------------------------------------------- окружение

    def _env_info(self) -> dict[str, str]:
        cached = self._memo.get("env_info")
        if isinstance(cached, dict):
            return cached
        info: dict[str, str] = {}
        result = self.run_command(["gem", "env"], timeout=60)
        if result.ok:
            user_match = _USER_DIR_RE.search(result.out)
            if user_match:
                info["user_dir"] = user_match.group("path").strip()
            bindir_match = _BINDIR_RE.search(result.out)
            if bindir_match:
                info["bindir"] = bindir_match.group("path").strip()
        if "user_dir" not in info:
            info["user_dir"] = str(home_path("~/.local/share/gem"))
        if "bindir" not in info:
            info["bindir"] = str(home_path("~/.local/bin"))
        self._memo["env_info"] = info
        return info

    def user_dir(self) -> Path:
        return Path(self._env_info()["user_dir"])

    def bindir(self) -> Path:
        return Path(self._env_info()["bindir"])

    def _gem_env(self) -> dict[str, str]:
        """Окружение, ограничивающее gem пользовательским каталогом."""
        import os

        user_dir = str(self.user_dir())
        env = dict(os.environ)
        env["GEM_HOME"] = user_dir
        env["GEM_PATH"] = user_dir
        env.setdefault("NO_COLOR", "1")
        return env

    # ----------------------------------------------------------------- данные

    def list(self) -> list[PackageRecord]:
        result = self.run_command(
            ["gem", "list", "--local"], timeout=120, env=self._gem_env()
        )
        if not result.ok and not result.out.strip():
            raise RuntimeError(result.tail() or "gem list завершился с ошибкой")
        records = parse_gem_list(result.out)
        user_dir = self.user_dir()
        bindir = self.bindir()
        for record in records:
            if record.version:
                gem_dir = user_dir / "gems" / f"{record.name}-{record.version}"
                record.install_path = str(gem_dir)
                try:
                    record.installed_at = datetime.fromtimestamp(
                        gem_dir.stat().st_mtime
                    )
                except OSError:
                    pass
            wrapper = bindir / record.name
            if wrapper.exists():
                record.bin_paths.append(str(wrapper))
        return records

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        result = self.run_command(
            ["gem", "outdated"], timeout=self.config.network_timeout, env=self._gem_env()
        )
        if not result.ok:
            return records
        latest = parse_gem_outdated(result.out)
        for record in records:
            if record.name in latest:
                record.latest = latest[record.name]
            elif record.version is not None:
                record.latest = record.version
        return records

    # ----------------------------------------------------------------- команды

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        return ["gem", "update", record.name]

    def update_many_command(
        self, records, *, latest: bool = False
    ) -> list[str] | None:
        return ["gem", "update", *(record.name for record in records)]

    def install_command(self, spec: str) -> list[str] | None:
        return ["gem", "install", "--user-install", spec]

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        return ["gem", "uninstall", "-x", record.name]
