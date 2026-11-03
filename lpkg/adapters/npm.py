"""npm -g — глобальные Node-пакеты.

На Gentoo префикс npm нередко указывает на ``~/.npm``, при этом root -g
оказывается ``lib64/node_modules``, а старые пакеты лежат в
``lib/node_modules``. Сканируем оба каталога и помечаем «невидимые» пакеты,
которые сам npm больше не видит.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..model import PackageRecord
from ..proc import home_path
from .base import Adapter


def _read_package_json(directory: Path) -> dict | None:
    manifest = directory / "package.json"
    if not manifest.is_file():
        return None
    try:
        with manifest.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def iter_packages(root: Path):
    """Обходит ``node_modules`` с учётом scoped-пакетов ``@scope/name``."""
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if entry.name.startswith("@"):
            try:
                scoped = sorted(entry.iterdir())
            except OSError:
                continue
            for item in scoped:
                if item.is_dir() and not item.name.startswith("."):
                    yield item
        else:
            yield entry


def _bin_links(bin_dir: Path) -> list[tuple[Path, Path]]:
    """Пары (ссылка, реальный путь цели) из каталога бинарей npm."""
    links: list[tuple[Path, Path]] = []
    try:
        entries = sorted(bin_dir.iterdir())
    except OSError:
        return links
    for entry in entries:
        if not entry.is_symlink():
            continue
        try:
            target = Path(os.path.realpath(entry))
        except OSError:
            continue
        links.append((entry, target))
    return links


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


class NpmAdapter(Adapter):
    id = "npm"
    label = "npm"
    command = "npm"

    # ------------------------------------------------------------- окружение

    def prefix(self) -> Path:
        cached = self._memo.get("prefix")
        if isinstance(cached, Path):
            return cached
        result = self.run_command(["npm", "config", "get", "prefix"], timeout=60)
        raw = result.out.strip() if result.ok else ""
        path = home_path(raw) if raw else home_path("~/.npm")
        self._memo["prefix"] = path
        return path

    def root_g(self) -> Path | None:
        cached = self._memo.get("root_g")
        if isinstance(cached, Path) or cached is False:
            return cached if isinstance(cached, Path) else None
        result = self.run_command(["npm", "root", "-g"], timeout=60)
        raw = result.out.strip() if result.ok else ""
        path = Path(raw) if raw else None
        self._memo["root_g"] = path if path is not None else False
        return path

    def module_dirs(self) -> list[Path]:
        prefix = self.prefix()
        candidates = [
            prefix / "lib" / "node_modules",
            prefix / "lib64" / "node_modules",
        ]
        root = self.root_g()
        if root is not None:
            candidates.append(root)
        seen: set[str] = set()
        result: list[Path] = []
        for path in candidates:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.is_dir():
                result.append(path)
        return result

    # ----------------------------------------------------------------- данные

    def list(self) -> list[PackageRecord]:
        prefix = self.prefix()
        root = self.root_g()
        bin_links = _bin_links(prefix / "bin")
        records: list[PackageRecord] = []
        seen: set[str] = set()

        for module_dir in self.module_dirs():
            for package_dir in iter_packages(module_dir):
                manifest = _read_package_json(package_dir)
                if manifest is None:
                    record = PackageRecord(
                        manager="npm",
                        name=package_dir.name,
                        install_path=str(package_dir),
                    )
                    record.add_warning(
                        "npm-broken-package",
                        "нет package.json или он повреждён",
                        "error",
                    )
                else:
                    name = str(manifest.get("name") or package_dir.name)
                    record = PackageRecord(
                        manager="npm",
                        name=name,
                        version=(
                            str(manifest["version"])
                            if manifest.get("version") is not None
                            else None
                        ),
                        install_path=str(package_dir),
                        source=(
                            str(manifest.get("repository"))
                            if isinstance(manifest.get("repository"), str)
                            else None
                        ),
                    )
                try:
                    record.installed_at = datetime.fromtimestamp(
                        package_dir.stat().st_mtime
                    )
                except OSError:
                    pass

                for link, target in bin_links:
                    if _is_within(target, package_dir):
                        record.bin_paths.append(str(link))

                if root is not None and not _is_within(package_dir, root):
                    record.add_warning(
                        "npm-hidden",
                        (
                            f"пакет лежит в {module_dir}, а npm root -g = {root}; "
                            "npm его не видит и обновлять откажется"
                        ),
                        "error",
                    )
                key = record.key
                if key in seen:
                    record.add_warning(
                        "npm-duplicate-dir",
                        f"тот же пакет найден ещё раз в {module_dir}",
                        "warning",
                    )
                seen.add(key)
                records.append(record)
        return records

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        result = self.run_command(
            ["npm", "outdated", "-g", "--json"],
            timeout=self.config.network_timeout,
        )
        # Код 1 — это нормально, когда есть устаревшие пакеты.
        payload: dict = {}
        if result.out.strip():
            try:
                parsed = json.loads(result.out)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}
        for record in records:
            info = payload.get(record.name)
            if isinstance(info, dict) and info.get("latest"):
                record.latest = str(info["latest"])
            elif record.has_errors:
                record.latest = None
            elif record.version is not None:
                record.latest = record.version
        return records

    # ----------------------------------------------------------------- команды

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        if any(w.code == "npm-hidden" for w in record.warnings):
            return None
        if latest:
            return [
                "npm",
                "install",
                "-g",
                f"{record.name}@latest",
                "--no-fund",
                "--no-audit",
            ]
        return ["npm", "update", "-g", record.name, "--no-fund", "--no-audit"]

    def install_command(self, spec: str) -> list[str] | None:
        return ["npm", "install", "-g", spec, "--no-fund", "--no-audit"]

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        if any(w.code == "npm-hidden" for w in record.warnings):
            return None
        return ["npm", "uninstall", "-g", record.name, "--no-fund", "--no-audit"]
