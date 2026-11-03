"""Базовый класс адаптера менеджера пакетов.

Адаптер отвечает за три вещи:

* собрать установленные пакеты (:meth:`Adapter.list`);
* при необходимости узнать последние версии (:meth:`Adapter.check_outdated`);
* построить *команды* обновления/установки/удаления.

Команды строятся отдельно от исполнения — это делает ``--dry-run``
бесплатным и позволяет тестировать адаптеры без запуска менеджеров.
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from typing import Callable, Sequence

from ..config import Config
from ..model import ActionResult, Issue, PackageRecord
from ..proc import RunResult, build_env, run, which

ProgressCallback = Callable[[str], None]


class Adapter(ABC):
    #: Идентификатор, совпадающий с ключом ``[managers]`` в конфиге.
    id: str = ""
    #: Человеческое название для вывода.
    label: str = ""
    #: Исполняемый файл, наличие которого делает адаптер доступным.
    command: str | None = None

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._memo: dict[str, object] = {}

    # ------------------------------------------------------------- доступность

    def is_available(self) -> bool:
        return self.command is None or which(self.command) is not None

    def unavailable_reason(self) -> str | None:
        if self.command and which(self.command) is None:
            return f"не найдена команда {self.command}"
        return None

    # ------------------------------------------------------------------ данные

    @abstractmethod
    def list(self) -> list[PackageRecord]:
        """Возвращает установленные пакеты. Может бросить исключение."""

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        """Заполняет ``latest``. По умолчанию ничего не знает о сети."""
        return records

    def issues(self, records: list[PackageRecord]) -> list[Issue]:
        """Дополнительные проблемы, видимые адаптеру."""
        return []

    # ------------------------------------------------------------------ команды

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        return None

    def update_many_command(
        self, records: Sequence[PackageRecord], *, latest: bool = False
    ) -> list[str] | None:
        """Одна команда на все записи; ``None`` — обновлять по одной."""
        return None

    def install_command(self, spec: str) -> list[str] | None:
        return None

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        return None

    # -------------------------------------------------------------- исполнение

    def update(
        self,
        records: Sequence[PackageRecord],
        *,
        latest: bool = False,
        dry_run: bool = False,
        allow_batch: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> list[ActionResult]:
        records = list(records)
        if not records:
            return []
        batch = self.update_many_command(records, latest=latest) if allow_batch else None
        if batch is not None:
            return [
                self._execute(
                    batch,
                    action="update",
                    names=[r.name for r in records],
                    dry_run=dry_run,
                    on_progress=on_progress,
                    from_version=_common_version(records),
                    to_version=_common_latest(records),
                )
            ]
        results: list[ActionResult] = []
        for record in records:
            command = self.update_command(record, latest=latest)
            if command is None:
                results.append(
                    ActionResult(
                        manager=self.id,
                        action="update",
                        names=[record.name],
                        ok=False,
                        message="обновление не поддерживается адаптером",
                    )
                )
                continue
            results.append(
                self._execute(
                    command,
                    action="update",
                    names=[record.name],
                    dry_run=dry_run,
                    on_progress=on_progress,
                    from_version=record.version,
                    to_version=record.latest,
                )
            )
        return results

    def install(
        self,
        spec: str,
        *,
        dry_run: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> ActionResult:
        command = self.install_command(spec)
        if command is None:
            return ActionResult(
                manager=self.id,
                action="install",
                names=[spec],
                ok=False,
                message="установка не поддерживается адаптером",
            )
        return self._execute(
            command,
            action="install",
            names=[spec],
            dry_run=dry_run,
            on_progress=on_progress,
        )

    def uninstall(
        self,
        records: Sequence[PackageRecord],
        *,
        dry_run: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> list[ActionResult]:
        results: list[ActionResult] = []
        for record in records:
            command = self.uninstall_command(record)
            if command is None:
                results.append(
                    ActionResult(
                        manager=self.id,
                        action="uninstall",
                        names=[record.name],
                        ok=False,
                        message="удаление не поддерживается адаптером",
                    )
                )
                continue
            results.append(
                self._execute(
                    command,
                    action="uninstall",
                    names=[record.name],
                    dry_run=dry_run,
                    on_progress=on_progress,
                    from_version=record.version,
                )
            )
        return results

    # -------------------------------------------------------------- внутреннее

    def _execute(
        self,
        command: list[str],
        *,
        action: str,
        names: list[str],
        dry_run: bool,
        on_progress: ProgressCallback | None,
        from_version: str | None = None,
        to_version: str | None = None,
    ) -> ActionResult:
        if dry_run:
            return ActionResult(
                manager=self.id,
                action=action,
                names=names,
                ok=True,
                command=list(command),
                from_version=from_version,
                to_version=to_version,
                message="dry-run: " + shlex.join(command),
            )
        if on_progress:
            on_progress(f"$ {shlex.join(command)}")
        result = self.run_command(command, timeout=self.config.update_timeout)
        message = "ok" if result.ok else (result.tail() or result.error or "ошибка")
        return ActionResult(
            manager=self.id,
            action=action,
            names=names,
            ok=result.ok,
            command=list(command),
            from_version=from_version,
            to_version=to_version,
            message=message,
            duration=result.duration,
        )

    def run_command(
        self,
        command: list[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        return run(
            command,
            timeout=self.config.timeout if timeout is None else timeout,
            env=env or build_env(),
        )


def _common_version(records: Sequence[PackageRecord]) -> str | None:
    versions = {r.version for r in records}
    return versions.pop() if len(versions) == 1 else None


def _common_latest(records: Sequence[PackageRecord]) -> str | None:
    latests = {r.latest for r in records}
    return latests.pop() if len(latests) == 1 else None
