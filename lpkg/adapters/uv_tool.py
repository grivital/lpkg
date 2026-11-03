"""uv tool — изолированные CLI-инструменты Python в ``$XDG_DATA_HOME/uv/tools``."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..model import PackageRecord
from ..proc import home_path
from .base import Adapter

#: ``debugpy v1.8.21 (/path) [latest: 1.8.22]``
_TOOL_RE = re.compile(r"^(?P<name>\S+)\s+v(?P<version>\S+)(?P<rest>.*)$")
_LATEST_RE = re.compile(r"\[latest:\s*(?P<latest>[^\]]+)\]")
_PATH_RE = re.compile(r"\((?P<path>/[^)]*)\)")
_ENTRY_RE = re.compile(r"^-\s+(?P<bin>\S+)(?:\s+\((?P<path>[^)]*)\))?\s*$")


def parse_uv_tool_list(text: str) -> list[PackageRecord]:
    """Разбирает вывод ``uv tool list [--show-paths] [--outdated]``."""
    records: list[PackageRecord] = []
    current: PackageRecord | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("- "):
            match = _ENTRY_RE.match(line)
            if match and current is not None:
                current.bin_paths.append(match.group("path") or match.group("bin"))
            continue
        match = _TOOL_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        rest = match.group("rest")
        latest_match = _LATEST_RE.search(rest)
        path_match = _PATH_RE.search(rest)
        record = PackageRecord(
            manager="uv",
            name=name,
            version=match.group("version"),
            latest=latest_match.group("latest").strip() if latest_match else None,
            install_path=path_match.group("path") if path_match else None,
            source="https://pypi.org/project/" + name + "/",
            scope="user",
        )
        if record.install_path:
            try:
                record.installed_at = datetime.fromtimestamp(
                    Path(record.install_path).stat().st_mtime
                )
            except OSError:
                pass
        records.append(record)
        current = record
    return records


class UvToolAdapter(Adapter):
    id = "uv"
    label = "uv tool"
    command = "uv"

    def tool_dir(self) -> Path:
        cached = self._memo.get("tool_dir")
        if isinstance(cached, Path):
            return cached
        result = self.run_command(["uv", "tool", "dir"], timeout=30)
        path = Path(result.out.strip()) if result.ok and result.out.strip() else home_path(
            "~/.local/share/uv/tools"
        )
        self._memo["tool_dir"] = path
        return path

    def list(self) -> list[PackageRecord]:
        result = self.run_command(["uv", "tool", "list", "--show-paths"], timeout=60)
        if not result.ok:
            raise RuntimeError(result.tail() or "uv tool list завершился с ошибкой")
        records = parse_uv_tool_list(result.out)
        for record in records:
            if record.install_path and not Path(record.install_path).exists():
                record.add_warning(
                    "uv-missing-env",
                    f"каталог инструмента не найден: {record.install_path}",
                    "error",
                )
            if not record.bin_paths:
                record.add_warning(
                    "uv-no-entrypoints",
                    "uv не сообщил об исполняемых файлах инструмента",
                    "info",
                )
        return records

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        result = self.run_command(
            ["uv", "tool", "list", "--show-paths", "--outdated"],
            timeout=self.config.network_timeout,
        )
        if not result.ok:
            raise RuntimeError(result.tail() or "uv tool list --outdated завершился с ошибкой")
        latest_map = {
            record.name: record.latest
            for record in parse_uv_tool_list(result.out)
            if record.latest is not None
        }
        # Вывод --outdated содержит только устаревшие инструменты. Всё, чего
        # в нём нет, считается актуальным.
        for record in records:
            if record.name in latest_map:
                record.set_latest(latest_map[record.name])
            elif record.version is not None:
                record.set_latest(record.version)
        return records

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        if latest:
            # --force пересоздаёт окружение без старого спецификатора.
            return ["uv", "tool", "install", "--force", f"{record.name}@latest"]
        return ["uv", "tool", "upgrade", record.name]

    def update_many_command(
        self, records, *, latest: bool = False
    ) -> list[str] | None:
        if latest:
            return None
        # Перечисляем имена явно, чтобы команда точно соответствовала плану.
        return ["uv", "tool", "upgrade", *(record.name for record in records)]

    def install_command(self, spec: str) -> list[str] | None:
        return ["uv", "tool", "install", spec]

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        return ["uv", "tool", "uninstall", record.name]
