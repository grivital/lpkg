"""Состояние между запусками: снапшоты и журнал операций.

Менеджеры пакетов почти никогда не хранят время установки, поэтому lpkg
запоминает момент, когда пакет впервые попал в скан. ``installed_at``
дополняется mtime каталога пакета как приблизительной оценкой.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .model import PackageRecord, iso


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "lpkg"


def snapshot_path() -> Path:
    return state_dir() / "snapshot.json"


def history_path() -> Path:
    return state_dir() / "history.jsonl"


# ------------------------------------------------------------------ снапшоты


@dataclass
class Snapshot:
    taken_at: datetime | None = None
    first_seen: dict[str, str] = field(default_factory=dict)
    packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def empty(cls) -> "Snapshot":
        return cls()


def load_snapshot() -> Snapshot:
    path = snapshot_path()
    try:
        if not path.exists():
            return Snapshot.empty()
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return Snapshot(error=f"не удалось прочитать {path}: {exc}")
    if not isinstance(data, dict):
        return Snapshot(error=f"неверная структура {path}")
    errors: list[str] = []
    taken_at = None
    raw_taken = data.get("taken_at")
    if isinstance(raw_taken, str):
        try:
            taken_at = datetime.fromisoformat(raw_taken)
        except ValueError:
            errors.append("неверное taken_at")
    raw_first_seen = data.get("first_seen") or {}
    if not isinstance(raw_first_seen, dict):
        errors.append("first_seen должен быть объектом")
        raw_first_seen = {}
    raw_packages = data.get("packages") or {}
    if not isinstance(raw_packages, dict):
        errors.append("packages должен быть объектом")
        raw_packages = {}
    first_seen = {
        k: v for k, v in raw_first_seen.items()
        if isinstance(k, str) and isinstance(v, str)
    }
    packages = {
        k: v for k, v in raw_packages.items()
        if isinstance(k, str) and isinstance(v, dict)
    }
    error = "; ".join(errors) if errors else None
    return Snapshot(
        taken_at=taken_at,
        first_seen=first_seen,
        packages=packages,
        error=error,
    )


def save_snapshot(
    records: Iterable[PackageRecord],
    snapshot: Snapshot,
    *,
    preserve_managers: Iterable[str] = (),
) -> Snapshot:
    now = datetime.now().astimezone()
    preserved = set(preserve_managers)
    packages = {
        key: value
        for key, value in snapshot.packages.items()
        if value.get("manager") in preserved
    }
    first_seen = dict(snapshot.first_seen)
    stamp = now.isoformat(timespec="seconds")
    for record in records:
        if record.is_manual:
            continue
        key = record.key
        first_seen.setdefault(key, stamp)
        packages[key] = {
            "manager": record.manager,
            "name": record.name,
            "version": record.version,
            "outdated": record.outdated,
            "install_path": record.install_path,
        }
    updated = Snapshot(taken_at=now, first_seen=first_seen, packages=packages)
    _atomic_write_json(
        snapshot_path(),
        {
            "version": 1,
            "taken_at": iso(now),
            "first_seen": first_seen,
            "packages": packages,
        },
    )
    return updated


def apply_first_seen(records: Iterable[PackageRecord], snapshot: Snapshot) -> None:
    """Проставляет ``installed_at`` по первому наблюдению, если он не задан."""
    for record in records:
        if record.is_manual or record.installed_at is not None:
            continue
        raw = snapshot.first_seen.get(record.key)
        if not raw:
            continue
        try:
            record.installed_at = datetime.fromisoformat(raw)
        except ValueError:
            continue


# ---------------------------------------------------------------------- diff


@dataclass
class Diff:
    added: list[PackageRecord] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    changed: list[tuple[PackageRecord, str | None]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def diff_snapshot(records: Iterable[PackageRecord], snapshot: Snapshot) -> Diff:
    current = {r.key: r for r in records if not r.is_manual}
    previous = snapshot.packages
    added: list[PackageRecord] = []
    changed: list[tuple[PackageRecord, str | None]] = []
    for key, record in current.items():
        old = previous.get(key)
        if old is None:
            added.append(record)
            continue
        old_version = old.get("version")
        if old_version != record.version:
            changed.append((record, old_version if isinstance(old_version, str) else None))
    removed = [
        {"key": key, **value}
        for key, value in previous.items()
        if key not in current
    ]
    return Diff(added=added, removed=removed, changed=changed)


# -------------------------------------------------------------------- журнал


def append_history(entry: dict[str, Any]) -> None:
    payload = {"time": iso(datetime.now().astimezone()), **entry}
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        # Журнал не должен ломать основную операцию.
        pass


def read_history(limit: int = 50) -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
    except OSError:
        return []
    return entries[-limit:][::-1]


# -------------------------------------------------------------------- запись


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".snapshot-", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
