"""cargo install — крейты, установленные в ``$CARGO_HOME/bin``."""

from __future__ import annotations

import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path

from ..model import Issue, PackageRecord
from ..proc import home_path, which
from .base import Adapter

#: ``name 1.2.3 (registry+https://...)``
_KEY_RE = re.compile(r"^(?P<name>.+?) (?P<version>\S+) \((?P<source>[^)]+)\)$")
#: ``b3sum  v1.8.5  v1.8.7  Yes``
_ROW_RE = re.compile(
    r"^(?P<name>\S+)\s+v?(?P<installed>\S+)\s+v?(?P<latest>\S+)\s+(?P<needs>Yes|No)\s*$"
)


def cargo_home() -> Path:
    raw = os.environ.get("CARGO_HOME")
    return home_path(raw) if raw else home_path("~/.cargo")


def parse_crates2(payload: dict) -> list[PackageRecord]:
    installs = payload.get("installs") or {}
    if not isinstance(installs, dict):
        return []
    records: list[PackageRecord] = []
    for key, info in installs.items():
        match = _KEY_RE.match(key)
        if not match:
            continue
        bins = info.get("bins") if isinstance(info, dict) else None
        bins = [str(item) for item in bins] if isinstance(bins, list) else []
        record = PackageRecord(
            manager="cargo",
            name=match.group("name"),
            version=match.group("version"),
            source=match.group("source"),
            install_path=str(cargo_home()),
            bin_paths=[str(cargo_home() / "bin" / item) for item in bins],
            extra={"bins": bins},
        )
        if isinstance(info, dict):
            for field_name in ("version_req", "target", "features", "rustc"):
                value = info.get(field_name)
                if value:
                    record.extra[field_name] = value
        records.append(record)
    return records


def parse_crates_toml(payload: dict) -> list[PackageRecord]:
    """Старый формат ``.crates.toml``: ``[v1]`` ключ → список бинарей."""
    section = payload.get("v1")
    if not isinstance(section, dict):
        return []
    records: list[PackageRecord] = []
    for key, bins in section.items():
        match = _KEY_RE.match(key)
        if not match:
            continue
        bin_list = [str(item) for item in bins] if isinstance(bins, list) else []
        records.append(
            PackageRecord(
                manager="cargo",
                name=match.group("name"),
                version=match.group("version"),
                source=match.group("source"),
                install_path=str(cargo_home()),
                bin_paths=[str(cargo_home() / "bin" / item) for item in bin_list],
                extra={"bins": bin_list},
            )
        )
    return records


def parse_install_update(text: str) -> dict[str, str]:
    """Разбирает таблицу ``cargo install-update --list``: имя → последняя версия."""
    latest: dict[str, str] = {}
    for line in text.splitlines():
        match = _ROW_RE.match(line.strip())
        if not match:
            continue
        latest[match.group("name")] = match.group("latest")
    return latest


class CargoAdapter(Adapter):
    id = "cargo"
    label = "cargo"
    command = "cargo"

    def _bin_dir(self) -> Path:
        return cargo_home() / "bin"

    def list(self) -> list[PackageRecord]:
        home = cargo_home()
        crates2 = home / ".crates2.json"
        crates1 = home / ".crates.toml"
        records: list[PackageRecord] = []
        if crates2.exists():
            try:
                with crates2.open("r", encoding="utf-8") as handle:
                    records = parse_crates2(json.load(handle))
            except (OSError, json.JSONDecodeError):
                records = []
        if not records and crates1.exists():
            try:
                with crates1.open("rb") as handle:
                    records = parse_crates_toml(tomllib.load(handle))
            except (OSError, tomllib.TOMLDecodeError):
                records = []

        try:
            installed_at = datetime.fromtimestamp(home.stat().st_mtime)
        except OSError:
            installed_at = None
        for record in records:
            record.installed_at = installed_at
            for path in record.bin_paths:
                if not Path(path).exists():
                    record.add_warning(
                        "cargo-missing-bin",
                        f"бинарный файл заявлен, но отсутствует: {path}",
                        "error",
                    )
        return records

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        if which("cargo-install-update") is None:
            return records
        result = self.run_command(
            ["cargo", "install-update", "-l"],
            timeout=self.config.network_timeout,
        )
        if not result.ok:
            return records
        latest = parse_install_update(result.out)
        for record in records:
            new_version = latest.get(record.name)
            if new_version is not None:
                record.latest = new_version
        return records

    def issues(self, records: list[PackageRecord]) -> list[Issue]:
        if records and which("cargo-install-update") is None:
            return [
                Issue(
                    code="cargo-no-updater",
                    message=(
                        "проверка обновлений крейтов недоступна: установите "
                        "cargo-update командой cargo install cargo-update"
                    ),
                    severity="info",
                    manager="cargo",
                )
            ]
        return []

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        return ["cargo", "install-update", record.name]

    def update_many_command(
        self, records, *, latest: bool = False
    ) -> list[str] | None:
        return ["cargo", "install-update", *(record.name for record in records)]

    def install_command(self, spec: str) -> list[str] | None:
        return ["cargo", "install", spec]

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        return ["cargo", "uninstall", record.name]
