"""Сканер домашних bin-каталогов: сироты, дубликаты, битые ссылки.

Менеджеры видят только «свои» пакеты. Всё, что установлено вручную или
положилось мимо ожидаемого каталога, остаётся невидимым. Сканер сопоставляет
файлы в bin-каталогах с записями менеджеров и добивает остаток.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .adapters.go import parse_go_version_m
from .model import Issue, PackageRecord
from .proc import home_path, is_elf, is_executable

#: Подкаталоги, по которым угадывается владелец симлинка.
_OWNER_HINTS: list[tuple[str, str]] = [
    ("/uv/tools/", "uv"),
    ("/node_modules/", "npm"),
    ("/gems/", "gem"),
    ("/site-packages/", "pip"),
]


@dataclass
class ScanOutcome:
    manual: list[PackageRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def scan_bin_dirs(
    records: list[PackageRecord],
    bin_dirs: list[Path],
    *,
    path_env: str | None = None,
) -> ScanOutcome:
    """Дополняет картину записями ``manual`` и проблемами ``doctor``."""
    outcome = ScanOutcome()
    owned_paths, owned_real = _owned_paths(records)
    duplicates = _duplicate_names(bin_dirs)
    path_winners = _path_winners(path_env)

    for bin_dir in bin_dirs:
        try:
            entries = sorted(bin_dir.iterdir())
        except OSError as exc:
            outcome.issues.append(
                Issue(
                    code="bin-dir-unreadable",
                    message=f"не удалось прочитать {bin_dir}: {exc}",
                    severity="warning",
                    path=str(bin_dir),
                )
            )
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_symlink():
                _inspect_symlink(
                    entry, bin_dir, owned_paths, owned_real, outcome
                )
            else:
                _inspect_regular(entry, bin_dir, owned_paths, outcome)

    for name, paths in duplicates.items():
        winner = path_winners.get(name)
        outcome.issues.append(
            Issue(
                code="duplicate-bin",
                message=(
                    f"команда {name} есть в нескольких каталогах: "
                    + ", ".join(paths)
                    + (f"; в PATH победит {winner}" if winner else "")
                ),
                severity="warning",
                name=name,
                path=winner or paths[0],
            )
        )

    outcome.issues.extend(_stale_receipts())
    return outcome


# --------------------------------------------------------------------- владение


def _owned_paths(
    records: list[PackageRecord],
) -> tuple[set[str], set[str]]:
    owned: set[str] = set()
    real: set[str] = set()
    for record in records:
        if record.is_manual:
            continue
        for raw in record.bin_paths:
            path = Path(raw)
            owned.add(str(path))
            try:
                real.add(os.path.realpath(path))
            except OSError:
                pass
    return owned, real


def _inspect_symlink(
    entry: Path,
    bin_dir: Path,
    owned_paths: set[str],
    owned_real: set[str],
    outcome: ScanOutcome,
) -> None:
    if not entry.exists():
        outcome.issues.append(
            Issue(
                code="broken-symlink",
                message=f"битая ссылка: {entry} → {_readlink(entry)}",
                severity="error",
                path=str(entry),
            )
        )
        return
    try:
        real = os.path.realpath(entry)
    except OSError:
        return
    if str(entry) in owned_paths or real in owned_real:
        return
    target = Path(real)
    hint = _owner_hint(real)
    name = entry.name
    record = PackageRecord(
        manager="manual",
        name=name,
        bin_paths=[str(entry)],
        install_path=str(target),
        extra={"kind": "symlink", "target": str(target)},
    )
    if hint:
        record.extra["hint"] = hint
        record.add_warning(
            "orphan-symlink",
            f"ссылка ведёт в каталог менеджера {hint}, но сам менеджер её не заявил",
            "warning",
        )
    try:
        record.installed_at = datetime.fromtimestamp(target.stat().st_mtime)
    except OSError:
        pass
    outcome.manual.append(record)


def _inspect_regular(
    entry: Path,
    bin_dir: Path,
    owned_paths: set[str],
    outcome: ScanOutcome,
) -> None:
    if str(entry) in owned_paths:
        return
    if not is_executable(entry):
        return
    record = PackageRecord(
        manager="manual",
        name=entry.name,
        install_path=str(entry),
        bin_paths=[str(entry)],
    )
    try:
        record.installed_at = datetime.fromtimestamp(entry.stat().st_mtime)
    except OSError:
        pass
    if is_elf(entry):
        info = _go_buildinfo(entry)
        if info is not None:
            module, version = info
            record.version = version
            record.source = module
            record.extra.update({"kind": "go-binary", "module": module})
        else:
            record.extra["kind"] = "elf"
    else:
        record.extra["kind"] = "script"
        version = _script_version(entry)
        if version:
            record.version = version
    outcome.manual.append(record)


def _go_buildinfo(binary: Path) -> tuple[str, str | None] | None:
    import subprocess

    try:
        completed = subprocess.run(
            ["go", "version", "-m", str(binary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_go_version_m(completed.stdout)


def _script_version(path: Path) -> str | None:
    """Осторожная попытка вытащить версию из текстового скрипта."""
    try:
        if path.stat().st_size > 512 * 1024:
            return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    if not head.startswith("#!"):
        return None
    for line in head.splitlines()[:40]:
        lowered = line.lower()
        if "version" in lowered and any(ch.isdigit() for ch in line):
            for token in line.replace("=", " ").replace(":", " ").split():
                candidate = token.strip("\"'(),")
                if candidate[:1].isdigit() and "." in candidate:
                    return candidate
    return None


def _owner_hint(real: str) -> str | None:
    for marker, manager in _OWNER_HINTS:
        if marker in real:
            return manager
    return None


def _readlink(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return "?"


# ------------------------------------------------------------------- дубликаты


def _duplicate_names(bin_dirs: list[Path]) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for bin_dir in bin_dirs:
        try:
            entries = sorted(bin_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if not (entry.is_symlink() or is_executable(entry)):
                continue
            seen.setdefault(entry.name, []).append(str(entry))
    return {name: paths for name, paths in seen.items() if len(paths) > 1}


def _path_winners(path_env: str | None) -> dict[str, str]:
    """Какой из файлов реально запустится при вызове по имени."""
    raw = path_env if path_env is not None else os.environ.get("PATH", "")
    winners: dict[str, str] = {}
    for directory in raw.split(os.pathsep):
        if not directory:
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for name in entries:
            if name in winners:
                continue
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                winners[name] = str(candidate)
    return winners


# --------------------------------------------------------------- следы установок


def _stale_receipts() -> list[Issue]:
    """Ищет cargo-dist/self-installer receipts, указывающие в пустоту."""
    issues: list[Issue] = []
    config_root = home_path("~/.config")
    try:
        receipt_files = sorted(config_root.glob("*/*-receipt.json"))
    except OSError:
        return issues
    for receipt in receipt_files:
        try:
            with receipt.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        binaries = payload.get("binaries")
        prefix = payload.get("install_prefix")
        if not isinstance(binaries, list) or not binaries:
            continue
        if not isinstance(prefix, str):
            continue
        existing = [b for b in binaries if (Path(prefix) / str(b)).exists()]
        if not existing:
            app = payload.get("source", {}).get("app_name") if isinstance(
                payload.get("source"), dict
            ) else None
            issues.append(
                Issue(
                    code="stale-receipt",
                    message=(
                        f"{receipt}: запись об установке "
                        f"{app or payload.get('source') or ''} "
                        f"версии {payload.get('version', '?')}, но ни одного файла "
                        f"в {prefix} нет"
                    ),
                    severity="warning",
                    path=str(receipt),
                )
            )
    return issues
