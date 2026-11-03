"""Сбор данных: параллельный опрос адаптеров и запуск сканера."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .adapters import ADAPTER_CLASSES, Adapter
from .config import Config
from .model import Issue, PackageRecord, sort_records
from .scan import scan_bin_dirs
from .state import Snapshot, apply_first_seen, load_snapshot


@dataclass
class ManagerError:
    manager: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"manager": self.manager, "message": self.message}


@dataclass
class ScanResult:
    records: list[PackageRecord] = field(default_factory=list)
    errors: list[ManagerError] = field(default_factory=list)
    unavailable: list[ManagerError] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    snapshot: Snapshot = field(default_factory=Snapshot.empty)
    scanned_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @property
    def managers_records(self) -> list[PackageRecord]:
        return [r for r in self.records if not r.is_manual]

    @property
    def manual_records(self) -> list[PackageRecord]:
        return [r for r in self.records if r.is_manual]

    @property
    def outdated(self) -> list[PackageRecord]:
        return [r for r in self.records if r.outdated]

    def all_issues(self) -> list[Issue]:
        issues = list(self.issues)
        for record in self.records:
            for warning in record.warnings:
                issues.append(
                    Issue(
                        code=warning.code,
                        message=warning.message,
                        severity=warning.severity,
                        manager=record.manager,
                        name=record.name,
                        path=record.install_path,
                    )
                )
        for error in self.errors:
            issues.append(
                Issue(
                    code="manager-error",
                    message=error.message,
                    severity="error",
                    manager=error.manager,
                )
            )
        return issues


def build_adapters(config: Config) -> list[Adapter]:
    adapters: list[Adapter] = []
    for cls in ADAPTER_CLASSES:
        if not config.managers.get(cls.id, True):
            continue
        adapters.append(cls(config))
    return adapters


def collect(
    config: Config | None = None,
    *,
    check_updates: bool = False,
    include_manual: bool | None = None,
    adapters: Iterable[Adapter] | None = None,
    snapshot: Snapshot | None = None,
) -> ScanResult:
    """Опрашивает менеджеры, сканирует bin-каталоги и применяет first_seen."""
    config = config or Config()
    if include_manual is None:
        include_manual = bool(config.include_manual)
    else:
        include_manual = bool(include_manual)
    if snapshot is None:
        snapshot = load_snapshot()

    available = [a for a in (adapters if adapters is not None else build_adapters(config))]
    result = ScanResult(snapshot=snapshot)
    if snapshot.error:
        result.issues.append(
            Issue(code="state-error", message=snapshot.error, severity="error")
        )
    records: list[PackageRecord] = []

    def load(adapter: Adapter) -> tuple[Adapter, list[PackageRecord], str | None]:
        try:
            if not adapter.is_available():
                return adapter, [], adapter.unavailable_reason()
            return adapter, adapter.list(), None
        except Exception as exc:  # адаптер не должен ронять весь скан
            return adapter, [], f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, len(available))) as pool:
        loaded = list(pool.map(load, available))

    loaded_adapters: list[Adapter] = []
    for adapter, adapter_records, error in loaded:
        if error is not None:
            target = (
                result.errors if adapter.is_available() else result.unavailable
            )
            target.append(ManagerError(adapter.id, error))
            continue
        loaded_adapters.append(adapter)
        records.extend(adapter_records)

    if check_updates:
        def refresh(pair: tuple[Adapter, list[PackageRecord]]) -> None:
            adapter, adapter_records = pair
            if not adapter_records:
                return
            try:
                adapter.check_outdated(adapter_records)
            except Exception as exc:
                result.errors.append(
                    ManagerError(
                        adapter.id,
                        f"проверка обновлений: {type(exc).__name__}: {exc}",
                    )
                )

        pairs = [
            (adapter, [r for r in records if r.manager == adapter.id])
            for adapter in loaded_adapters
        ]
        with ThreadPoolExecutor(max_workers=max(1, len(pairs))) as pool:
            list(pool.map(refresh, pairs))

    result.records = records

    apply_first_seen(result.records, snapshot)

    if include_manual:
        bin_dirs = _bin_dirs(config, result.records)
        outcome = scan_bin_dirs(result.records, bin_dirs)
        result.records.extend(outcome.manual)
        result.issues.extend(outcome.issues)

    # Проблемы самих менеджеров (например, npm с prefix вне $HOME) не зависят
    # от того, сканируем ли мы каталоги вручную.
    for adapter in loaded_adapters:
        try:
            result.issues.extend(
                adapter.issues([r for r in records if r.manager == adapter.id])
            )
        except Exception:
            pass

    result.records = sort_records(result.records)
    return result


def _bin_dirs(config: Config, records: list[PackageRecord]):
    """Каталоги из конфига + каталоги, где менеджеры реально держат бинари."""
    candidates = config.resolved_bin_dirs()
    for record in records:
        for raw in record.bin_paths:
            parent = _safe_parent(raw)
            if parent is not None:
                candidates.append(parent)
    candidates.extend(config.resolved_extra_dirs())
    paths = []
    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _safe_parent(raw: str):
    from pathlib import Path

    try:
        parent = Path(raw).parent
    except (OSError, ValueError):
        return None
    return parent if parent.is_dir() else None
