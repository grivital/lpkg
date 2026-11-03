"""Форматирование вывода: таблицы, цвета, карточки пакетов."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Iterable, Sequence

from .model import Issue, PackageRecord, human_age

RESET = "\033[0m"
STYLES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


class Style:
    def __init__(self, mode: str = "auto", stream=None) -> None:
        self.stream = stream or sys.stdout
        if mode == "always":
            self.enabled = True
        elif mode == "never":
            self.enabled = False
        else:
            self.enabled = bool(
                getattr(self.stream, "isatty", lambda: False)()
                and os.environ.get("NO_COLOR") is None
            )

    def __call__(self, text: str, *names: str) -> str:
        if not self.enabled or not names:
            return text
        prefix = "".join(STYLES[name] for name in names if name in STYLES)
        return f"{prefix}{text}{RESET}" if prefix else text


def _visible_len(text: str) -> int:
    """Длина строки без ANSI-последовательностей."""
    result = 0
    index = 0
    while index < len(text):
        if text[index] == "\033":
            end = text.find("m", index)
            if end == -1:
                break
            index = end + 1
            continue
        result += 1
        index += 1
    return result


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _visible_len(text) <= width:
        return text
    if width <= 1:
        return "…"
    return text[: width - 1] + "…"


def render_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    max_widths: Sequence[int | None] | None = None,
    min_widths: Sequence[int] | None = None,
) -> str:
    rows = [list(map(str, row)) for row in rows]
    widths = [_visible_len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row[: len(widths)]):
            widths[index] = max(widths[index], _visible_len(cell))
    if max_widths:
        for index, limit in enumerate(max_widths):
            if index < len(widths) and limit is not None and limit > 0:
                widths[index] = min(widths[index], limit)
    if min_widths:
        for index, value in enumerate(min_widths):
            if index < len(widths):
                widths[index] = max(widths[index], value)

    lines = []
    header = "  ".join(
        _truncate(str(h), widths[i]).ljust(widths[i]) for i, h in enumerate(headers)
    )
    lines.append(header)
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        cells = []
        for index in range(len(widths)):
            cell = row[index] if index < len(row) else ""
            cells.append(_truncate(str(cell), widths[index]).ljust(widths[index]))
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def status_cell(record: PackageRecord, style: Style) -> str:
    if record.has_errors:
        return style("ошибка", "red")
    if record.outdated is True:
        return style("устарел", "yellow")
    if record.outdated is False:
        return style("актуален", "green")
    if record.is_manual:
        return style("вручную", "magenta")
    return style("?", "dim")


def record_row(record: PackageRecord, style: Style, *, show_manager: bool = True) -> list[str]:
    installed = "—"
    if record.installed_at is not None:
        installed = record.installed_at.strftime("%Y-%m-%d")
        age = human_age(record.installed_at)
        if age and age != "—":
            installed = f"{installed} ({age})"
    bins = record.bin_paths[0] if record.bin_paths else "—"
    if len(record.bin_paths) > 1:
        bins = f"{bins} (+{len(record.bin_paths) - 1})"
    row = [
        record.name,
        record.version or "—",
        record.latest or "—",
        installed,
        status_cell(record, style),
    ]
    if show_manager:
        row.insert(0, record.label)
    return row


def print_records(
    records: Sequence[PackageRecord],
    *,
    style: Style,
    show_manager: bool = True,
    show_paths: bool = False,
) -> None:
    headers = ["Пакет", "Версия", "Последняя", "Установлен", "Статус"]
    if show_manager:
        headers.insert(0, "Менеджер")
    if show_paths:
        headers.append("Бинарь")
    rows = []
    for record in records:
        row = record_row(record, style, show_manager=show_manager)
        if show_paths:
            row.append(record.bin_paths[0] if record.bin_paths else "—")
        rows.append(row)
    terminal = shutil.get_terminal_size((120, 24)).columns
    widths: list[int | None] = [None] * len(headers)
    if len(headers) > 4:
        widths[0] = 16 if show_manager else 0
        widths[-1 if show_paths else 4] = max(12, terminal - 70)
    print(render_table(headers, rows, max_widths=widths))


def print_record_detail(record: PackageRecord, *, style: Style) -> None:
    print(style(f"{record.name} {record.version or ''}".strip(), "bold"))
    fields = [
        ("Менеджер", record.label),
        ("Ключ", record.key),
        ("Версия", record.version or "—"),
        ("Последняя", record.latest or "неизвестно"),
        ("Статус", status_cell(record, style)),
        (
            "Установлен",
            (
                record.installed_at.strftime("%Y-%m-%d %H:%M")
                + f" ({human_age(record.installed_at)})"
                if record.installed_at
                else "—"
            ),
        ),
        ("Каталог", record.install_path or "—"),
        ("Источник", record.source or "—"),
        ("Область", record.scope),
    ]
    for label, value in fields:
        print(f"  {style(label + ':', 'dim'):<16} {value}")
    if record.bin_paths:
        print(f"  {style('Бинари:', 'dim')}")
        for path in record.bin_paths:
            print(f"    {path}")
    if record.extra:
        print(f"  {style('Дополнительно:', 'dim')}")
        for key, value in record.extra.items():
            print(f"    {key}: {value}")
    if record.warnings:
        print(f"  {style('Предупреждения:', 'dim')}")
        for warning in record.warnings:
            color = {"error": "red", "warning": "yellow"}.get(warning.severity, "dim")
            print(f"    {style('•', color)} [{warning.code}] {warning.message}")


def print_issues(issues: Sequence[Issue], *, style: Style) -> None:
    if not issues:
        print(style("Проблем не найдено.", "green"))
        return
    order = {"error": 0, "warning": 1, "info": 2}
    for issue in sorted(issues, key=lambda i: (order.get(i.severity, 3), i.code)):
        color = {"error": "red", "warning": "yellow", "info": "dim"}.get(
            issue.severity, "dim"
        )
        marker = style("✖" if issue.severity == "error" else "•", color)
        subject = ""
        if issue.manager and issue.name:
            subject = f" {issue.manager}:{issue.name}"
        elif issue.manager:
            subject = f" {issue.manager}"
        print(f"{marker} [{issue.code}]{subject} {issue.message}")
