"""flatpak --user — приложения пользовательской установки Flatpak."""

from __future__ import annotations

import json

from ..model import PackageRecord
from .base import Adapter


def parse_flatpak_list(payload: object) -> list[PackageRecord]:
    records: list[PackageRecord] = []
    if not isinstance(payload, list):
        return records
    for item in payload:
        if not isinstance(item, dict):
            continue
        app_id = item.get("application") or item.get("name")
        if not app_id:
            continue
        record = PackageRecord(
            manager="flatpak",
            name=str(app_id),
            version=(
                str(item["version"]) if item.get("version") is not None else None
            ),
            source=str(item["origin"]) if item.get("origin") else "flathub",
            extra={
                key: item[key]
                for key in ("branch", "runtime", "installation", "arch")
                if item.get(key) is not None
            },
        )
        if item.get("name") and item.get("name") != app_id:
            record.extra["display_name"] = str(item["name"])
        records.append(record)
    return records


class FlatpakUserAdapter(Adapter):
    id = "flatpak"
    label = "flatpak --user"
    command = "flatpak"

    def _ensure_user_remote(self) -> None:
        """Flatpak без user-remote не умеет список — это не ошибка."""
        return None

    def list(self) -> list[PackageRecord]:
        result = self.run_command(
            ["flatpak", "list", "--user", "--json"], timeout=120
        )
        if not result.ok:
            raise RuntimeError(result.tail() or "flatpak list завершился с ошибкой")
        text = result.out.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"не удалось разобрать JSON от flatpak: {exc}") from exc
        records = parse_flatpak_list(payload)
        for record in records:
            if record.version is None:
                record.add_warning(
                    "flatpak-no-version",
                    "приложение не сообщает версию",
                    "info",
                )
        return records

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        result = self.run_command(
            ["flatpak", "remote-ls", "--user", "--updates", "--json"],
            timeout=self.config.network_timeout,
        )
        if not result.ok:
            raise RuntimeError(result.tail() or "flatpak remote-ls завершился с ошибкой")
        if not result.out.strip():
            payload = []
        else:
            try:
                payload = json.loads(result.out)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"не удалось разобрать JSON от flatpak: {exc}") from exc
        if not isinstance(payload, list):
            raise RuntimeError("flatpak remote-ls вернул JSON неожиданной структуры")
        latest: dict[str, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            app_id = item.get("application") or item.get("name")
            version = item.get("version")
            if app_id and version:
                latest[str(app_id)] = str(version)
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
        return ["flatpak", "update", "--user", "-y", record.name]

    def update_many_command(
        self, records, *, latest: bool = False
    ) -> list[str] | None:
        return ["flatpak", "update", "--user", "-y", *(r.name for r in records)]

    def install_command(self, spec: str) -> list[str] | None:
        return ["flatpak", "install", "--user", "-y", spec]

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        return ["flatpak", "uninstall", "--user", "-y", record.name]
