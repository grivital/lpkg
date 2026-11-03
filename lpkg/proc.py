"""Запуск внешних команд: таймауты, окружение, предсказуемый результат.

Модуль никогда не бросает исключения наружу — адаптеры получают
:class:`RunResult` и решают, что делать с ошибкой.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    cmd: list[str]
    ok: bool
    code: int
    out: str = ""
    err: str = ""
    duration: float = 0.0
    error: str | None = None
    timed_out: bool = False

    @property
    def command_line(self) -> str:
        return " ".join(self.cmd)

    @property
    def combined(self) -> str:
        parts = [self.out, self.err]
        return "\n".join(p for p in parts if p).strip()

    def tail(self, lines: int = 20) -> str:
        """Последние строки вывода — для сообщений об ошибках."""
        text = self.combined
        if not text:
            return self.error or f"код {self.code}"
        chunks = text.splitlines()
        return "\n".join(chunks[-lines:])


def which(name: str) -> str | None:
    return shutil.which(name)


def home_path(value: str) -> Path:
    """Раскрывает ``~`` и переменные окружения в пути."""
    return Path(os.path.expandvars(os.path.expanduser(value)))


def build_env(*, base: dict[str, str] | None = None, **overrides: str | None) -> dict[str, str]:
    """Копия окружения с переопределениями; ``None`` удаляет переменную."""
    env = dict(os.environ if base is None else base)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    # Стабильный вывод и отсутствие интерактивности.
    env.setdefault("NO_COLOR", "1")
    env.setdefault("PAGER", "cat")
    env.setdefault("GIT_PAGER", "cat")
    env.setdefault("CI", "1")
    return env


def run(
    cmd: list[str],
    *,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    input_text: str | None = None,
) -> RunResult:
    """Запускает команду без shell и возвращает результат."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            cmd=cmd,
            ok=False,
            code=124,
            out=_as_text(exc.stdout),
            err=_as_text(exc.stderr),
            duration=time.monotonic() - started,
            error=f"таймаут {timeout:.0f} с",
            timed_out=True,
        )
    except FileNotFoundError:
        return RunResult(
            cmd=cmd,
            ok=False,
            code=127,
            duration=time.monotonic() - started,
            error=f"команда не найдена: {cmd[0]}",
        )
    except OSError as exc:
        return RunResult(
            cmd=cmd,
            ok=False,
            code=126,
            duration=time.monotonic() - started,
            error=str(exc),
        )
    return RunResult(
        cmd=cmd,
        ok=completed.returncode == 0,
        code=completed.returncode,
        out=completed.stdout or "",
        err=completed.stderr or "",
        duration=time.monotonic() - started,
    )


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False
