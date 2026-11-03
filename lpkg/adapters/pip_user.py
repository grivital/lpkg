"""pip --user — пакеты Python в пользовательском ``site-packages``.

Используется *системный* интерпретатор из конфига (по умолчанию ``python3``),
а не тот, которым запущен lpkg: иначе в изолированном окружении lpkg мы
увидели бы его собственные зависимости.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..model import PackageRecord
from ..proc import which
from .base import Adapter


def parse_pip_list(payload: object) -> list[PackageRecord]:
    records: list[PackageRecord] = []
    if not isinstance(payload, list):
        return records
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        records.append(
            PackageRecord(
                manager="pip",
                name=str(name),
                version=(
                    str(item["version"]) if item.get("version") is not None else None
                ),
                source="https://pypi.org/project/" + str(name) + "/",
            )
        )
    return records


def parse_pip_outdated(payload: object) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(payload, list):
        return result
    for item in payload:
        if isinstance(item, dict) and item.get("name") and item.get("latest_version"):
            result[str(item["name"])] = str(item["latest_version"])
    return result


class PipUserAdapter(Adapter):
    id = "pip"
    label = "pip --user"
    command = None  # проверяем интерпретатор из конфига

    def python(self) -> str:
        return self.config.pip_python

    def is_available(self) -> bool:
        return which(self.python()) is not None

    def unavailable_reason(self) -> str | None:
        if which(self.python()) is None:
            return f"не найден интерпретатор {self.python()}"
        return None

    def _pip(self, *args: str) -> list[str]:
        return [self.python(), "-m", "pip", *args]

    def _base_args(self) -> list[str]:
        return [
            "--user",
            "--disable-pip-version-check",
            "--no-color",
            "--no-input",
        ]

    def _site_info(self) -> dict[str, Path]:
        cached = self._memo.get("site_info")
        if isinstance(cached, dict):
            return cached
        info: dict[str, Path] = {}
        site = self.run_command(
            [self.python(), "-m", "site", "--user-site"], timeout=30
        )
        base = self.run_command(
            [self.python(), "-m", "site", "--user-base"], timeout=30
        )
        if site.ok and site.out.strip():
            info["site"] = Path(site.out.strip())
        if base.ok and base.out.strip():
            info["base"] = Path(base.out.strip())
        self._memo["site_info"] = info
        return info

    def list(self) -> list[PackageRecord]:
        result = self.run_command(
            self._pip("list", *self._base_args(), "--format=json"),
            timeout=180,
        )
        if not result.ok:
            raise RuntimeError(result.tail() or "pip list завершился с ошибкой")
        try:
            payload = json.loads(result.out or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"не удалось разобрать JSON от pip: {exc}") from exc
        records = parse_pip_list(payload)
        site = self._site_info().get("site")
        for record in records:
            record.install_path = str(site) if site else None
            if site is not None:
                record.extra["site"] = str(site)
                dist_info = _find_dist_info(site, record.name)
                if dist_info is not None:
                    try:
                        record.installed_at = datetime.fromtimestamp(
                            dist_info.stat().st_mtime
                        )
                    except OSError:
                        pass
        return records

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        result = self.run_command(
            self._pip("list", *self._base_args(), "--outdated", "--format=json"),
            timeout=self.config.network_timeout,
        )
        if not result.ok:
            raise RuntimeError(result.tail() or "pip list --outdated завершился с ошибкой")
        try:
            payload = json.loads(result.out or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"не удалось разобрать JSON от pip: {exc}") from exc
        latest = parse_pip_outdated(payload)
        for record in records:
            if record.name in latest:
                record.set_latest(latest[record.name])
            elif record.version is not None:
                record.set_latest(record.version)
        return records

    # ----------------------------------------------------------------- команды

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        return self._pip("install", *self._base_args(), "--upgrade", record.name)

    def install_command(self, spec: str) -> list[str] | None:
        return self._pip("install", *self._base_args(), spec)

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        return self._pip("uninstall", "-y", record.name)


def _find_dist_info(site: Path, name: str) -> Path | None:
    """Ищет каталог ``*.dist-info`` для пакета (регистр и ``-``/``_`` не важны)."""
    normalized = _normalize(name)
    try:
        candidates = sorted(site.glob("*.dist-info"))
    except OSError:
        return None
    for candidate in candidates:
        stem = candidate.name[: -len(".dist-info")]
        distribution = stem.split("-")[0]
        if _normalize(distribution) == normalized:
            return candidate
    return None


def _normalize(value: str) -> str:
    return value.lower().replace("-", "_").replace(".", "_")
