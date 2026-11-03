"""Единая модель данных lpkg.

Все адаптеры менеджеров приводят свои сведения к :class:`PackageRecord`,
поэтому остальная часть программы ничего не знает о форматах uv/cargo/npm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

MANAGER_LABELS: dict[str, str] = {
    "uv": "uv tool",
    "cargo": "cargo",
    "npm": "npm",
    "gem": "gem",
    "pip": "pip --user",
    "go": "go",
    "flatpak": "flatpak --user",
    "manual": "вручную",
}

#: Порядок менеджеров в выводе.
MANAGER_ORDER: list[str] = [
    "uv",
    "cargo",
    "npm",
    "gem",
    "pip",
    "go",
    "flatpak",
    "manual",
]

SEVERITIES = ("info", "warning", "error")


@dataclass(frozen=True)
class Warning:
    """Предупреждение, привязанное к конкретному пакету."""

    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass
class Issue:
    """Проблема, найденная сканером или адаптером (для ``lpkg doctor``)."""

    code: str
    message: str
    severity: str = "warning"
    manager: str | None = None
    name: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.manager is not None:
            data["manager"] = self.manager
        if self.name is not None:
            data["name"] = self.name
        if self.path is not None:
            data["path"] = self.path
        return data


@dataclass
class PackageRecord:
    """Одна установленная единица: пакет, крейт, гем, инструмент или бинарь."""

    manager: str
    name: str
    version: str | None = None
    latest: str | None = None
    update_available: bool | None = None
    installed_at: datetime | None = None
    install_path: str | None = None
    bin_paths: list[str] = field(default_factory=list)
    source: str | None = None
    scope: str = "user"
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[Warning] = field(default_factory=list)

    # ------------------------------------------------------------------ ключи

    @property
    def key(self) -> str:
        """Устойчивый идентификатор записи: ``manager:name``."""
        return f"{self.manager}:{self.name}"

    @property
    def label(self) -> str:
        return MANAGER_LABELS.get(self.manager, self.manager)

    @property
    def outdated(self) -> bool | None:
        """``True``/``False``, если известна последняя версия, иначе ``None``."""
        if self.latest is None or self.version is None:
            return None
        if self.update_available is not None:
            return self.update_available
        newer = version_is_newer(self.latest, self.version)
        if newer is not None:
            return newer
        # Неоднозначные форматы не угадываем: лучше «неизвестно», чем
        # ложное «устарел».
        return None

    def set_latest(self, latest: str, *, fallback: bool = True) -> None:
        """Проставляет ``latest`` и выводит статус обновления.

        ``fallback`` используется, когда версии не удаётся сравнить: менеджер
        уже сказал, что пакет требует обновления.
        """
        self.latest = latest
        if self.version is None:
            self.update_available = None
            return
        newer = version_is_newer(latest, self.version)
        self.update_available = fallback if newer is None else newer

    @property
    def is_manual(self) -> bool:
        return self.manager == "manual"

    @property
    def has_errors(self) -> bool:
        return any(w.severity == "error" for w in self.warnings)

    # -------------------------------------------------------------- сериализация

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "manager": self.manager,
            "name": self.name,
            "version": self.version,
            "latest": self.latest,
            "update_available": self.update_available,
            "outdated": self.outdated,
            "installed_at": iso(self.installed_at),
            "install_path": self.install_path,
            "bin_paths": list(self.bin_paths),
            "source": self.source,
            "scope": self.scope,
            "extra": self.extra,
            "warnings": [w.to_dict() for w in self.warnings],
        }

    def add_warning(self, code: str, message: str, severity: str = "warning") -> None:
        candidate = Warning(code, message, severity)
        if candidate not in self.warnings:
            self.warnings.append(candidate)


@dataclass
class ActionResult:
    """Результат одной операции (обновление, установка, удаление)."""

    manager: str
    action: str
    names: list[str] = field(default_factory=list)
    ok: bool = True
    command: list[str] = field(default_factory=list)
    from_version: str | None = None
    to_version: str | None = None
    message: str = ""
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "manager": self.manager,
            "action": self.action,
            "names": list(self.names),
            "ok": self.ok,
            "command": list(self.command),
            "from_version": self.from_version,
            "to_version": self.to_version,
            "message": self.message,
            "duration": round(self.duration, 3),
        }


# --------------------------------------------------------------------- версии

_VERSION_SPLIT = re.compile(r"[.+\-]")
_LEADING_NUM = re.compile(r"(\d+)")
_CORE_RE = re.compile(r"^(?P<core>\d+(?:\.\d+)*)(?P<rest>.*)$", re.DOTALL)
_IDENT_RE = re.compile(r"[A-Za-z]+|\d+")

#: Стадии версии: предрелиз < стабильная < пострелиз.
_STAGE_PRE = 0
_STAGE_STABLE = 1
_STAGE_POST = 2


def normalize_version(version: str) -> str:
    """Убирает ведущую ``v`` и пробелы: ``v1.2.3`` → ``1.2.3``."""
    value = version.strip()
    if value[:1] in ("v", "V"):
        value = value[1:]
    return value


def version_key(version: str) -> tuple[int, ...]:
    """Числовой ключ версии для сортировки и сравнения.

    Хвостовые нули отбрасываются, чтобы ``1.2`` и ``1.2.0`` считались равными.
    Если версия начинается не с числа, ключ пуст.
    """
    value = normalize_version(version)
    parts: list[int] = []
    for part in _VERSION_SPLIT.split(value):
        match = _LEADING_NUM.match(part)
        if match is None:
            break
        parts.append(int(match.group(1)))
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def parse_version(
    version: str,
) -> tuple[tuple[int, ...], int, tuple[str, ...]] | None:
    """Разбирает версию на (числовое ядро, стадия, идентификаторы).

    Понимает semver (``1.2.3-alpha.1``), PEP 440-подобные суффиксы
    (``1.0rc1``, ``1.0.post1``) и отбрасывает build-метаданные после ``+``.
    Возвращает ``None`` для форматов, которые не удаётся сравнить.
    """
    value = normalize_version(version)
    base = value.split("+", 1)[0].strip()
    match = _CORE_RE.match(base)
    if match is None:
        return None

    core = tuple(int(part) for part in match.group("core").split("."))
    while core and core[-1] == 0:
        core = core[:-1]

    rest = match.group("rest").strip()
    if not rest:
        return core, _STAGE_STABLE, ()

    lowered = rest.lower().lstrip(".-")
    if lowered.startswith("post"):
        tail = lowered[len("post"):].strip(".-")
        return core, _STAGE_POST, ("post", *_IDENT_RE.findall(tail))

    identifiers = tuple(_IDENT_RE.findall(lowered))
    if not identifiers:
        return None
    return core, _STAGE_PRE, identifiers


def _compare_identifiers(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Semver-подобное сравнение идентификаторов prerelease."""
    for left_id, right_id in zip(left, right):
        if left_id == right_id:
            continue
        left_num = left_id.isdigit()
        right_num = right_id.isdigit()
        if left_num and right_num:
            return (int(left_id) > int(right_id)) - (int(left_id) < int(right_id))
        if left_num:
            return -1
        if right_num:
            return 1
        return (left_id > right_id) - (left_id < right_id)
    return (len(left) > len(right)) - (len(left) < len(right))


def _compare_parsed(
    left: tuple[tuple[int, ...], int, tuple[str, ...]],
    right: tuple[tuple[int, ...], int, tuple[str, ...]],
) -> int:
    if left[0] != right[0]:
        return (left[0] > right[0]) - (left[0] < right[0])
    if left[1] != right[1]:
        return (left[1] > right[1]) - (left[1] < right[1])
    return _compare_identifiers(left[2], right[2])


def versions_equal(left: str, right: str) -> bool:
    """Семантическое равенство: без build-метаданных и хвостовых нулей."""
    left_norm = normalize_version(left)
    right_norm = normalize_version(right)
    if left_norm == right_norm:
        return True
    left_parsed = parse_version(left_norm)
    right_parsed = parse_version(right_norm)
    if left_parsed is None or right_parsed is None:
        return False
    return _compare_parsed(left_parsed, right_parsed) == 0


def version_is_newer(candidate: str, current: str) -> bool | None:
    """``True``/``False``, если версии сравнимы; иначе ``None``."""
    candidate_parsed = parse_version(candidate)
    current_parsed = parse_version(current)
    if candidate_parsed is None or current_parsed is None:
        return None
    return _compare_parsed(candidate_parsed, current_parsed) > 0


def version_sort_key(version: str) -> tuple[tuple[int, ...], str]:
    return (version_key(version), normalize_version(version))


def max_version(versions: Iterable[str]) -> str | None:
    result: str | None = None
    for version in versions:
        if not version:
            continue
        if result is None:
            result = version
            continue
        newer = version_is_newer(version, result)
        if newer is True:
            result = version
        elif newer is None and normalize_version(version) > normalize_version(result):
            result = version
    return result


# --------------------------------------------------------------------- время


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def human_age(value: datetime | None, *, now: datetime | None = None) -> str:
    """Человекочитаемый возраст: ``3 дня``, ``2 месяца``, ``сегодня``."""
    if value is None:
        return "—"
    if now is None:
        now = datetime.now(value.tzinfo) if value.tzinfo else datetime.now()
    try:
        delta = now - value
    except TypeError:
        delta = datetime.now() - value.replace(tzinfo=None)
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "в будущем"
    if seconds < 60:
        return "только что"
    minutes = seconds // 60
    if minutes < 60:
        return _plural(minutes, ("минуту", "минуты", "минут"))
    hours = minutes // 60
    if hours < 24:
        return _plural(hours, ("час", "часа", "часов"))
    days = hours // 24
    if days < 30:
        return _plural(days, ("день", "дня", "дней"))
    months = days // 30
    if months < 12:
        return _plural(months, ("месяц", "месяца", "месяцев"))
    years = days // 365
    return _plural(years, ("год", "года", "лет"))


def _plural(number: int, forms: tuple[str, str, str]) -> str:
    number = abs(number)
    if number % 10 == 1 and number % 100 != 11:
        form = forms[0]
    elif 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{number} {form}"


def human_size(size: float) -> str:
    units = ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"


def manager_rank(manager: str) -> int:
    try:
        return MANAGER_ORDER.index(manager)
    except ValueError:
        return len(MANAGER_ORDER)


def sort_records(records: Iterable[PackageRecord]) -> list[PackageRecord]:
    return sorted(records, key=lambda r: (manager_rank(r.manager), r.name.lower()))
