"""Командный интерфейс lpkg."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from . import __version__
from .adapters import Adapter
from .config import Config, ConfigError
from .model import ActionResult, PackageRecord, iso
from .registry import ScanResult, build_adapters, collect
from .state import (
    append_history,
    apply_first_seen,
    diff_snapshot,
    load_snapshot,
    read_history,
    save_snapshot,
)
from .ui import Style, print_issues, print_record_detail, print_records, render_table

PROGRAM = "lpkg"


# ------------------------------------------------------------------ парсер CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Единое управление пакетами, установленными в домашний каталог: "
            "uv tool, cargo, npm, gem, pip --user, go и flatpak --user."
        ),
        epilog="Подробности: lpkg <команда> --help",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", metavar="PATH", help="путь к config.toml")
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=None,
        help="режим цветного вывода",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный вывод")

    sub = parser.add_subparsers(dest="command", required=True)

    # Флаги, которые удобно указывать и после подкоманды. default=SUPPRESS
    # не даёт подпарсеру затереть значение, заданное до подкоманды.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", metavar="PATH", default=argparse.SUPPRESS, help="путь к config.toml"
    )
    common.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default=argparse.SUPPRESS,
        help="режим цветного вывода",
    )
    common.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
        help="подробный вывод",
    )

    def add_parser(name: str, **kwargs) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], **kwargs)

    p_list = add_parser("list", aliases=["ls"], help="показать установленное")
    p_list.add_argument("--manager", "-m", action="append", help="фильтр по менеджеру")
    p_list.add_argument("--outdated", "-o", action="store_true", help="только устаревшие")
    p_list.add_argument("--manual", action="store_true", help="только установленное вручную")
    p_list.add_argument("--all", "-a", action="store_true", help="включая установленное вручную")
    p_list.add_argument("--check", action="store_true", help="проверить обновления (сеть)")
    p_list.add_argument("--paths", action="store_true", help="показать пути к бинарям")
    p_list.add_argument("--json", action="store_true", help="вывод в JSON")
    p_list.set_defaults(func=cmd_list)

    p_out = add_parser("outdated", help="показать доступные обновления")
    p_out.add_argument("--manager", "-m", action="append", help="фильтр по менеджеру")
    p_out.add_argument("--json", action="store_true", help="вывод в JSON")
    p_out.set_defaults(func=cmd_outdated)

    p_update = add_parser("update", aliases=["up"], help="обновить пакеты")
    p_update.add_argument("targets", nargs="*", help="manager:name или уникальное имя")
    p_update.add_argument("--all", "-a", action="store_true", help="обновить всё")
    p_update.add_argument(
        "--latest", action="store_true", help="разрешить переход на новую мажорную версию"
    )
    p_update.add_argument("--dry-run", action="store_true", help="только показать команды")
    p_update.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    p_update.add_argument("--json", action="store_true", help="вывод в JSON")
    p_update.set_defaults(func=cmd_update)

    p_install = add_parser("install", aliases=["add"], help="установить пакет")
    p_install.add_argument("spec", help="manager:spec, например uv:ruff или npm:@scope/pkg")
    p_install.add_argument("--dry-run", action="store_true", help="только показать команду")
    p_install.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    p_install.add_argument("--json", action="store_true", help="вывод в JSON")
    p_install.set_defaults(func=cmd_install)

    p_uninstall = add_parser("uninstall", aliases=["rm"], help="удалить пакет")
    p_uninstall.add_argument("targets", nargs="+", help="manager:name или уникальное имя")
    p_uninstall.add_argument("--dry-run", action="store_true", help="только показать команды")
    p_uninstall.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    p_uninstall.add_argument("--json", action="store_true", help="вывод в JSON")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_info = add_parser("info", help="подробности о пакете")
    p_info.add_argument("targets", nargs="+", help="manager:name или уникальное имя")
    p_info.add_argument("--check", action="store_true", help="проверить обновления (сеть)")
    p_info.add_argument("--json", action="store_true", help="вывод в JSON")
    p_info.set_defaults(func=cmd_info)

    p_doctor = add_parser("doctor", help="проверить целостность и сирот")
    p_doctor.add_argument("--strict", action="store_true", help="код 1 при любом замечании")
    p_doctor.add_argument("--json", action="store_true", help="вывод в JSON")
    p_doctor.set_defaults(func=cmd_doctor)

    p_diff = add_parser("diff", help="что изменилось с прошлого скана")
    p_diff.add_argument("--json", action="store_true", help="вывод в JSON")
    p_diff.set_defaults(func=cmd_diff)

    p_history = add_parser("history", help="журнал операций lpkg")
    p_history.add_argument("--limit", type=int, default=30, help="сколько записей")
    p_history.add_argument("--json", action="store_true", help="вывод в JSON")
    p_history.set_defaults(func=cmd_history)

    p_export = add_parser("export", help="снимок установленного в JSON")
    p_export.add_argument("--output", "-o", help="файл (по умолчанию stdout)")
    p_export.add_argument("--check", action="store_true", help="включить проверку обновлений")
    p_export.set_defaults(func=cmd_export)

    p_tui = add_parser("tui", help="интерактивный интерфейс на dialog")
    p_tui.set_defaults(func=cmd_tui)

    return parser


# -------------------------------------------------------------------- контекст


class Context:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        try:
            self.config = Config.load(args.config)
        except ConfigError as exc:
            raise SystemExit(f"{PROGRAM}: {exc}")
        mode = args.color or self.config.color
        self.style = Style(mode)
        self.adapters: dict[str, Adapter] = {
            adapter.id: adapter for adapter in build_adapters(self.config)
        }

    @property
    def json(self) -> bool:
        return bool(getattr(self.args, "json", False))

    def collect(self, *, check_updates: bool = False, include_manual: bool = True) -> ScanResult:
        if getattr(self.args, "verbose", False) and not self.json:
            for adapter in self.adapters.values():
                if adapter.is_available():
                    print(f"{PROGRAM}: {adapter.id}: доступен", file=sys.stderr)
                else:
                    print(
                        f"{PROGRAM}: {adapter.id}: {adapter.unavailable_reason()}",
                        file=sys.stderr,
                    )
        result = collect(
            self.config,
            check_updates=check_updates,
            include_manual=include_manual,
            adapters=self.adapters.values(),
        )
        if not self.json:
            for error in result.errors:
                print(
                    self.style(f"{PROGRAM}: {error.manager}: {error.message}", "red"),
                    file=sys.stderr,
                )
        return result

    def save(self, result: ScanResult) -> None:
        result.snapshot = save_snapshot(result.records, result.snapshot)

    def record_history(self, result: ActionResult) -> None:
        append_history(result.to_dict())


# --------------------------------------------------------------------- утилиты


def filter_by_managers(
    records: Iterable[PackageRecord], managers: Sequence[str] | None
) -> list[PackageRecord]:
    if not managers:
        return list(records)
    wanted = {m.lower() for m in managers}
    return [r for r in records if r.manager.lower() in wanted or r.label.lower() in wanted]


def resolve_targets(
    targets: Sequence[str], records: Sequence[PackageRecord]
) -> tuple[list[PackageRecord], list[str]]:
    by_key = {record.key: record for record in records}
    by_name: dict[str, list[PackageRecord]] = defaultdict(list)
    for record in records:
        by_name[record.name].append(record)

    resolved: list[PackageRecord] = []
    problems: list[str] = []
    seen: set[str] = set()
    for target in targets:
        candidates: list[PackageRecord] = []
        if target in by_key:
            candidates = [by_key[target]]
        elif ":" in target:
            manager, name = target.split(":", 1)
            key = f"{manager}:{name}"
            if key in by_key:
                candidates = [by_key[key]]
        if not candidates:
            candidates = by_name.get(target, [])
        if not candidates:
            problems.append(f"{target}: ничего не найдено")
            continue
        if len(candidates) > 1:
            options = ", ".join(record.key for record in candidates)
            problems.append(f"{target}: неоднозначно, уточните один из: {options}")
            continue
        record = candidates[0]
        if record.key not in seen:
            seen.add(record.key)
            resolved.append(record)
    return resolved, problems


def confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            f"{PROGRAM}: для неинтерактивного запуска добавьте --yes",
            file=sys.stderr,
        )
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes", "д", "да")


def print_action_results(
    results: Sequence[ActionResult], *, style: Style, json_output: bool
) -> int:
    if json_output:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
        return 0 if all(r.ok for r in results) else 1
    if not results:
        print("Нечего делать.")
        return 0
    rows = []
    for result in results:
        status = style("ok", "green") if result.ok else style("ошибка", "red")
        names = ", ".join(result.names)
        version = ""
        if result.from_version or result.to_version:
            version = f"{result.from_version or '?'} → {result.to_version or '?'}"
        rows.append([result.manager, result.action, names, version, status, result.message])
    print(
        render_table(
            ["Менеджер", "Действие", "Пакет", "Версия", "Итог", "Сообщение"],
            rows,
            max_widths=[14, 10, 30, 20, 8, 60],
        )
    )
    return 0 if all(r.ok for r in results) else 1


def _record_payload(records: Sequence[PackageRecord]) -> list[dict]:
    return [record.to_dict() for record in records]


# --------------------------------------------------------------------- команды


def cmd_list(ctx: Context) -> int:
    args = ctx.args
    # Список устаревших без проверки версий не имеет смысла.
    check = args.check or args.outdated
    result = ctx.collect(check_updates=check)
    records = result.records
    if args.manual:
        records = [r for r in records if r.is_manual]
    elif not args.all:
        records = [r for r in records if not r.is_manual]
    records = filter_by_managers(records, args.manager)
    if args.outdated:
        records = [r for r in records if r.outdated]
    ctx.save(result)

    if ctx.json:
        print(json.dumps(_record_payload(records), ensure_ascii=False, indent=2))
        return 0
    if not records:
        print("Ничего не найдено.")
        return 0
    print_records(records, style=ctx.style, show_paths=args.paths)
    _print_summary(result, style=ctx.style)
    return 0


def cmd_outdated(ctx: Context) -> int:
    result = ctx.collect(check_updates=True)
    records = [r for r in filter_by_managers(result.records, ctx.args.manager) if r.outdated]
    ctx.save(result)
    if ctx.json:
        print(json.dumps(_record_payload(records), ensure_ascii=False, indent=2))
        return 0
    if not records:
        print(ctx.style("Всё актуально.", "green"))
        return 0
    print_records(records, style=ctx.style)
    unknown = [
        r
        for r in result.managers_records
        if r.outdated is None and r.latest is None
    ]
    print()
    print(f"Устарело: {len(records)}.")
    if unknown:
        print(
            ctx.style(
                f"Не удалось проверить: {len(unknown)} "
                f"({', '.join(r.key for r in unknown[:10])}"
                + (" …" if len(unknown) > 10 else "")
                + ")",
                "dim",
            )
        )
    return 0


def cmd_update(ctx: Context) -> int:
    args = ctx.args
    if not args.all and not args.targets:
        print(f"{PROGRAM}: укажите пакеты или --all", file=sys.stderr)
        return 2

    result = ctx.collect(check_updates=True)
    records = result.records
    if args.all:
        targets = [
            r
            for r in records
            if not r.is_manual
            and r.outdated is True
            and _can_update(ctx, r, latest=args.latest)
        ]
        skipped = [
            r
            for r in records
            if not r.is_manual
            and (r.outdated is not True or not _can_update(ctx, r, latest=args.latest))
        ]
        for record in skipped:
            if record.outdated is not True:
                reason = (
                    "версия не проверена"
                    if record.outdated is None
                    else "уже актуален"
                )
            else:
                reason = "обновление недоступно"
            print(
                ctx.style(f"пропущено {record.key}: {reason}", "yellow"),
                file=sys.stderr,
            )
    else:
        targets, problems = resolve_targets(args.targets, records)
        if problems:
            for problem in problems:
                print(f"{PROGRAM}: {problem}", file=sys.stderr)
            return 2
        manual = [r for r in targets if r.is_manual]
        if manual:
            for record in manual:
                print(
                    f"{PROGRAM}: {record.key} установлен вручную, обновление невозможно",
                    file=sys.stderr,
                )
            return 2
        unsupported = [r for r in targets if not _can_update(ctx, r, latest=args.latest)]
        if unsupported:
            for record in unsupported:
                print(
                    f"{PROGRAM}: {record.key}: обновление недоступно "
                    "(см. lpkg doctor)",
                    file=sys.stderr,
                )
            return 2

    if not targets:
        print("Нечего обновлять.")
        return 0

    if not args.dry_run:
        _print_update_plan(targets, ctx.style, latest=args.latest)
        if not confirm("Продолжить обновление?", assume_yes=args.yes):
            print("Отменено.")
            return 1
    elif not ctx.json:
        _print_update_plan(targets, ctx.style, latest=args.latest)

    results: list[ActionResult] = []
    grouped: dict[str, list[PackageRecord]] = defaultdict(list)
    for record in targets:
        grouped[record.manager].append(record)
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
                latest=args.latest,
                dry_run=args.dry_run,
                allow_batch=args.all,
                on_progress=None if ctx.json else lambda line: print(
                    ctx.style(line, "dim"), file=sys.stderr
                ),
            )
        )
    for action_result in results:
        if not args.dry_run:
            ctx.record_history(action_result)
    if not args.dry_run:
        ctx.save(ctx.collect(check_updates=False))
    return print_action_results(results, style=ctx.style, json_output=ctx.json)


def cmd_install(ctx: Context) -> int:
    args = ctx.args
    if ":" not in args.spec:
        print(
            f"{PROGRAM}: укажите менеджер: lpkg install uv:ruff, cargo:ripgrep, "
            "npm:@scope/pkg, gem:rake, pip:requests, go:github.com/x/y@latest, "
            "flatpak:org.example.App",
            file=sys.stderr,
        )
        return 2
    manager, spec = args.spec.split(":", 1)
    adapter = ctx.adapters.get(manager)
    if adapter is None:
        print(f"{PROGRAM}: неизвестный менеджер {manager}", file=sys.stderr)
        return 2
    if not adapter.is_available():
        print(
            f"{PROGRAM}: {manager}: {adapter.unavailable_reason()}",
            file=sys.stderr,
        )
        return 2
    result = adapter.install(spec, dry_run=args.dry_run)
    if not args.dry_run:
        ctx.record_history(result)
    return print_action_results([result], style=ctx.style, json_output=ctx.json)


def cmd_uninstall(ctx: Context) -> int:
    args = ctx.args
    scan = ctx.collect(check_updates=False)
    targets, problems = resolve_targets(args.targets, scan.records)
    if problems:
        for problem in problems:
            print(f"{PROGRAM}: {problem}", file=sys.stderr)
        return 2
    manual = [r for r in targets if r.is_manual]
    if manual:
        for record in manual:
            print(
                f"{PROGRAM}: {record.key}: файл установлен вручную, "
                f"удалите его самостоятельно: {record.install_path}",
                file=sys.stderr,
            )
        return 2
    if not targets:
        print("Нечего удалять.")
        return 0
    if not args.dry_run:
        for record in targets:
            print(f"  {record.key} {record.version or ''}")
        if not confirm("Удалить перечисленные пакеты?", assume_yes=args.yes):
            print("Отменено.")
            return 1

    results: list[ActionResult] = []
    grouped: dict[str, list[PackageRecord]] = defaultdict(list)
    for record in targets:
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
        results.extend(adapter.uninstall(manager_records, dry_run=args.dry_run))
    for action_result in results:
        ctx.record_history(action_result)
    if not args.dry_run:
        ctx.save(ctx.collect(check_updates=False))
    return print_action_results(results, style=ctx.style, json_output=ctx.json)


def cmd_info(ctx: Context) -> int:
    args = ctx.args
    result = ctx.collect(check_updates=args.check)
    targets, problems = resolve_targets(args.targets, result.records)
    if problems:
        for problem in problems:
            print(f"{PROGRAM}: {problem}", file=sys.stderr)
        return 2
    if ctx.json:
        print(json.dumps(_record_payload(targets), ensure_ascii=False, indent=2))
        return 0
    for index, record in enumerate(targets):
        if index:
            print()
        print_record_detail(record, style=ctx.style)
    return 0


def cmd_doctor(ctx: Context) -> int:
    result = ctx.collect(check_updates=False, include_manual=True)
    issues = result.all_issues()
    if ctx.json:
        payload = {
            "scanned_at": iso(result.scanned_at),
            "issues": [issue.to_dict() for issue in issues],
            "errors": [error.to_dict() for error in result.errors],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_issues(issues, style=ctx.style)
        if issues:
            errors = sum(1 for i in issues if i.severity == "error")
            warnings = sum(1 for i in issues if i.severity == "warning")
            print()
            print(f"Итого: ошибок {errors}, предупреждений {warnings}.")
    ctx.save(result)
    if ctx.args.strict and any(i.severity in ("warning", "error") for i in issues):
        return 1
    return 0


def cmd_diff(ctx: Context) -> int:
    snapshot = load_snapshot()
    result = ctx.collect(check_updates=False)
    for record in result.records:
        if record.installed_at is None:
            apply_first_seen([record], snapshot)
    diff = diff_snapshot(result.records, snapshot)
    ctx.save(result)
    if ctx.json:
        payload = {
            "added": _record_payload(diff.added),
            "removed": diff.removed,
            "changed": [
                {"record": record.to_dict(), "previous_version": previous}
                for record, previous in diff.changed
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if diff.empty:
        print("Изменений с прошлого скана нет.")
        return 0
    if diff.added:
        print(ctx.style(f"Добавлено ({len(diff.added)}):", "green"))
        for record in diff.added:
            print(f"  + {record.key} {record.version or ''}")
    if diff.changed:
        print(ctx.style(f"Обновлено ({len(diff.changed)}):", "yellow"))
        for record, previous in diff.changed:
            print(f"  ~ {record.key} {previous or '?'} → {record.version or '?'}")
    if diff.removed:
        print(ctx.style(f"Удалено ({len(diff.removed)}):", "red"))
        for item in diff.removed:
            print(f"  - {item.get('key')} {item.get('version') or ''}")
    return 0


def cmd_history(ctx: Context) -> int:
    entries = read_history(ctx.args.limit)
    if ctx.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print("Журнал пуст.")
        return 0
    rows = []
    for entry in entries:
        names = entry.get("names") or []
        rows.append(
            [
                str(entry.get("time") or "—"),
                str(entry.get("manager") or "—"),
                str(entry.get("action") or "—"),
                ", ".join(map(str, names)),
                "ok" if entry.get("ok", True) else "ошибка",
                str(entry.get("message") or ""),
            ]
        )
    print(
        render_table(
            ["Время", "Менеджер", "Действие", "Пакет", "Итог", "Сообщение"],
            rows,
            max_widths=[20, 10, 10, 30, 8, 50],
        )
    )
    return 0


def cmd_export(ctx: Context) -> int:
    result = ctx.collect(check_updates=ctx.args.check, include_manual=True)
    payload = {
        "generated_at": iso(datetime.now().astimezone()),
        "lpkg_version": __version__,
        "packages": _record_payload(
            [r for r in result.records if not r.is_manual]
        ),
        "manual": _record_payload(result.manual_records),
        "issues": [issue.to_dict() for issue in result.all_issues()],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if ctx.args.output:
        Path(ctx.args.output).write_text(text, encoding="utf-8")
        print(f"Записано: {ctx.args.output}")
    else:
        sys.stdout.write(text)
    ctx.save(result)
    return 0


def cmd_tui(ctx: Context) -> int:
    from .tui import run as run_tui

    return run_tui(ctx)


# ------------------------------------------------------------------ внутреннее


def _can_update(ctx: Context, record: PackageRecord, *, latest: bool) -> bool:
    adapter = ctx.adapters.get(record.manager)
    if adapter is None or not adapter.is_available():
        return False
    return adapter.update_command(record, latest=latest) is not None


def _print_update_plan(
    records: Sequence[PackageRecord], style: Style, *, latest: bool
) -> None:
    print("Планируется обновить:")
    for record in records:
        current = record.version or "?"
        new = record.latest or "?"
        marker = style(f"{current} → {new}", "yellow")
        print(f"  {record.key}  {marker}")
    if latest:
        print(style("Режим --latest: разрешены мажорные обновления.", "dim"))


def _print_summary(result: ScanResult, *, style: Style) -> None:
    managers = len(result.managers_records)
    manual = len(result.manual_records)
    outdated = len(result.outdated)
    parts = [f"Пакетов менеджеров: {managers}"]
    if manual:
        parts.append(f"вручную: {manual}")
    if outdated:
        parts.append(style(f"устарело: {outdated}", "yellow"))
    if result.errors:
        parts.append(style(f"ошибок опроса: {len(result.errors)}", "red"))
    print()
    print("  ".join(parts))


# ----------------------------------------------------------------------- вход


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = Context(args)
    try:
        return int(args.func(ctx))
    except KeyboardInterrupt:
        print("\nПрервано.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
