"""Интерактивный интерфейс на ``dialog``.

TUI — тонкая надстройка над тем же API, что и CLI: он не выполняет команды
менеджеров сам, а вызывает адаптеры и показывает результаты.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from typing import Sequence

from .model import ActionResult, Issue, PackageRecord, human_age
from .state import read_history

DIALOG = "dialog"
BACKTITLE = "lpkg — пакеты в домашнем каталоге"


class DialogError(RuntimeError):
    pass


# ------------------------------------------------------------------ обёртки


def _dialog(*args: str) -> tuple[int, str]:
    command = [DIALOG, "--stdout", "--backtitle", BACKTITLE, *args]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise DialogError("не найдена программа dialog") from exc
    except OSError as exc:
        raise DialogError(str(exc)) from exc
    return completed.returncode, completed.stdout


def _menu(title: str, text: str, items: Sequence[tuple[str, str]]) -> str | None:
    if not items:
        return None
    args = ["--title", title, "--menu", text, "0", "0", "0"]
    for tag, label in items:
        args.extend([tag, label])
    code, out = _dialog(*args)
    if code != 0:
        return None
    return out.strip() or None


def _msgbox(title: str, text: str) -> None:
    _dialog("--title", title, "--msgbox", text, "0", "0")


def _yesno(title: str, text: str) -> bool:
    code, _ = _dialog("--title", title, "--yesno", text, "0", "0")
    return code == 0


def _textbox(title: str, text: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="lpkg-", delete=False, encoding="utf-8"
    )
    try:
        handle.write(text)
        handle.close()
        _dialog("--title", title, "--textbox", handle.name, "0", "0")
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _infobox(text: str) -> None:
    try:
        _dialog("--title", "Подождите", "--infobox", text, "0", "0")
    except DialogError:
        pass


# ------------------------------------------------------------------ помощники


def _status(record: PackageRecord) -> str:
    if record.has_errors:
        return "ошибка"
    if record.outdated is True:
        return f"устарел → {record.latest}"
    if record.outdated is False:
        return "актуален"
    if record.is_manual:
        return "вручную"
    return "?"


def _record_text(record: PackageRecord) -> str:
    lines = [
        f"Пакет:      {record.name}",
        f"Менеджер:   {record.label}",
        f"Ключ:       {record.key}",
        f"Версия:     {record.version or '—'}",
        f"Последняя:  {record.latest or 'неизвестно'}",
        f"Статус:     {_status(record)}",
        (
            "Установлен: "
            + (
                f"{record.installed_at:%Y-%m-%d %H:%M} "
                f"({human_age(record.installed_at)})"
                if record.installed_at
                else "—"
            )
        ),
        f"Каталог:    {record.install_path or '—'}",
        f"Источник:   {record.source or '—'}",
    ]
    if record.bin_paths:
        lines.append("Бинари:")
        lines.extend(f"  {path}" for path in record.bin_paths)
    if record.extra:
        lines.append("Дополнительно:")
        for key, value in record.extra.items():
            lines.append(f"  {key}: {value}")
    if record.warnings:
        lines.append("Предупреждения:")
        for warning in record.warnings:
            lines.append(f"  [{warning.code}] {warning.message}")
    return "\n".join(lines)


def _issues_text(issues: Sequence[Issue]) -> str:
    if not issues:
        return "Проблем не найдено."
    order = {"error": 0, "warning": 1, "info": 2}
    lines = []
    for issue in sorted(issues, key=lambda i: (order.get(i.severity, 3), i.code)):
        marker = "✖" if issue.severity == "error" else "•"
        subject = ""
        if issue.manager and issue.name:
            subject = f" {issue.manager}:{issue.name}"
        elif issue.manager:
            subject = f" {issue.manager}"
        lines.append(f"{marker} [{issue.code}]{subject} {issue.message}")
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    lines.append("")
    lines.append(f"Итого: ошибок {errors}, предупреждений {warnings}.")
    return "\n".join(lines)


def _results_text(results: Sequence[ActionResult]) -> str:
    if not results:
        return "Операций не было."
    lines = []
    for result in results:
        status = "ok" if result.ok else "ОШИБКА"
        version = ""
        if result.from_version or result.to_version:
            version = f" {result.from_version or '?'} → {result.to_version or '?'}"
        lines.append(
            f"[{status}] {result.manager} {result.action} "
            f"{', '.join(result.names)}{version}"
        )
        if result.message and result.message != "ok":
            for line in result.message.splitlines():
                lines.append(f"    {line}")
    return "\n".join(lines)


# ------------------------------------------------------------------- разделы


def _run_update(
    ctx, records: Sequence[PackageRecord], *, latest: bool = False
) -> list[ActionResult]:
    _infobox("Обновление пакетов…\nЭто может занять время.")
    grouped: dict[str, list[PackageRecord]] = defaultdict(list)
    for record in records:
        grouped[record.manager].append(record)
    results: list[ActionResult] = []
    for manager, manager_records in grouped.items():
        adapter = ctx.adapters.get(manager)
        if adapter is None or not adapter.is_available():
            results.append(
                ActionResult(
                    manager=manager,
                    action="update",
                    names=[r.name for r in manager_records],
                    ok=False,
                    message="менеджер недоступен",
                )
            )
            continue
        results.extend(
            adapter.update(
                manager_records,
                latest=latest,
                allow_batch=len(manager_records) > 1,
            )
        )
    for result in results:
        ctx.record_history(result)
    ctx.save(ctx.collect(check_updates=False))
    return results


def _run_uninstall(ctx, records: Sequence[PackageRecord]) -> list[ActionResult]:
    _infobox("Удаление пакетов…")
    results: list[ActionResult] = []
    grouped: dict[str, list[PackageRecord]] = defaultdict(list)
    for record in records:
        grouped[record.manager].append(record)
    for manager, manager_records in grouped.items():
        adapter = ctx.adapters.get(manager)
        if adapter is None:
            results.append(
                ActionResult(
                    manager=manager,
                    action="uninstall",
                    names=[r.name for r in manager_records],
                    ok=False,
                    message="менеджер недоступен",
                )
            )
            continue
        results.extend(adapter.uninstall(manager_records))
    for result in results:
        ctx.record_history(result)
    ctx.save(ctx.collect(check_updates=False))
    return results


def _package_menu(ctx, record: PackageRecord) -> None:
    while True:
        choice = _menu(
            record.key,
            f"{record.name} {record.version or ''} — {_status(record)}",
            [
                ("info", "Подробности"),
                ("update", "Обновить"),
                ("remove", "Удалить"),
                ("back", "Назад"),
            ],
        )
        if choice is None or choice == "back":
            return
        if choice == "info":
            _msgbox(record.key, _record_text(record))
        elif choice == "update":
            if record.is_manual:
                _msgbox("Обновление", "Пакет установлен вручную — обновите его сами.")
                continue
            adapter = ctx.adapters.get(record.manager)
            if adapter is None or adapter.update_command(record) is None:
                _msgbox(
                    "Обновление",
                    f"{record.key}: обновление недоступно.\n"
                    "Запустите lpkg doctor для подробностей.",
                )
                continue
            if not _yesno(
                "Обновление",
                f"Обновить {record.key} {record.version or ''}?",
            ):
                continue
            results = _run_update(ctx, [record])
            _textbox("Результат обновления", _results_text(results))
            return
        elif choice == "remove":
            if record.is_manual:
                _msgbox(
                    "Удаление",
                    "Файл установлен вручную; удалите его самостоятельно:\n"
                    + str(record.install_path),
                )
                continue
            if not _yesno(
                "Удаление",
                f"Удалить {record.key} {record.version or ''}?",
            ):
                continue
            results = _run_uninstall(ctx, [record])
            _textbox("Результат удаления", _results_text(results))
            return


def _show_packages(ctx) -> None:
    _infobox("Сбор списка пакетов…")
    result = ctx.collect(check_updates=False, include_manual=True)
    records = result.records
    managers = sorted({r.manager for r in records})
    while True:
        filter_choice = _menu(
            "Пакеты",
            f"Всего записей: {len(records)}. Выберите менеджер или «Все».",
            [("all", f"Все ({len(records)})")]
            + [(manager, manager) for manager in managers]
            + [("back", "Назад")],
        )
        if filter_choice is None or filter_choice == "back":
            return
        subset = (
            records
            if filter_choice == "all"
            else [r for r in records if r.manager == filter_choice]
        )
        if not subset:
            _msgbox("Пакеты", "Пусто.")
            continue
        items = [
            (record.key, f"{record.name:<28} {record.version or '—':<14} {_status(record)}")
            for record in subset
        ]
        selected = _menu(
            "Пакеты",
            "Выберите пакет",
            items + [("back", "Назад")],
        )
        if selected is None or selected == "back":
            continue
        record = next((r for r in subset if r.key == selected), None)
        if record is not None:
            _package_menu(ctx, record)


def _show_outdated(ctx) -> None:
    _infobox("Проверяю обновления в сети…\nЭто может занять время.")
    result = ctx.collect(check_updates=True)
    outdated = [r for r in result.records if r.outdated]
    if not outdated:
        _msgbox("Обновления", "Всё актуально (насколько удалось проверить).")
        return
    items = [
        (record.key, f"{record.name} {record.version} → {record.latest}", "on")
        for record in outdated
    ]
    code, out = _dialog(
        "--title",
        "Доступные обновления",
        "--checklist",
        f"Найдено {len(outdated)} обновлений. Отметьте нужные.",
        "0",
        "0",
        "0",
        *[part for item in items for part in item],
    )
    if code != 0:
        return
    selected_keys = set(out.strip().split()) if out.strip() else set()
    selected = [r for r in outdated if r.key in selected_keys]
    if not selected:
        return
    if not _yesno(
        "Обновление",
        "Обновить выбранные пакеты?\n\n"
        + "\n".join(f"{r.key}: {r.version} → {r.latest}" for r in selected),
    ):
        return
    latest = _yesno(
        "Мажорные обновления",
        "Разрешить переход на новую мажорную версию?",
    )
    results = _run_update(ctx, selected, latest=latest)
    _textbox("Результат обновления", _results_text(results))


def _show_doctor(ctx) -> None:
    _infobox("Проверяю целостность…")
    result = ctx.collect(check_updates=False, include_manual=True)
    _textbox("Проверка", _issues_text(result.all_issues()))
    ctx.save(result)


def _show_history(ctx) -> None:
    entries = read_history(50)
    if not entries:
        _msgbox("Журнал", "Журнал пуст.")
        return
    lines = []
    for entry in entries:
        names = ", ".join(map(str, entry.get("names") or []))
        status = "ok" if entry.get("ok", True) else "ОШИБКА"
        lines.append(
            f"{entry.get('time') or '—'}  {status:<7} "
            f"{entry.get('manager') or '—'} {entry.get('action') or '—'}: {names}"
        )
    _textbox("Журнал операций", "\n".join(lines))


# ---------------------------------------------------------------------- вход


def run(ctx) -> int:
    if shutil.which(DIALOG) is None:
        print("lpkg: не найдена программа dialog (пакет dialog)", file=sys.stderr)
        return 1
    if not sys.stdin.isatty():
        print("lpkg: TUI требует терминал", file=sys.stderr)
        return 1
    try:
        while True:
            choice = _menu(
                "lpkg",
                "Единое управление пакетами, установленными в домашний каталог",
                [
                    ("list", "Пакеты"),
                    ("outdated", "Проверить и обновить"),
                    ("doctor", "Проверка целостности"),
                    ("history", "Журнал операций"),
                    ("exit", "Выход"),
                ],
            )
            if choice is None or choice == "exit":
                return 0
            if choice == "list":
                _show_packages(ctx)
            elif choice == "outdated":
                _show_outdated(ctx)
            elif choice == "doctor":
                _show_doctor(ctx)
            elif choice == "history":
                _show_history(ctx)
    except DialogError as exc:
        print(f"lpkg: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
