"""Адаптеры менеджеров пакетов."""

from __future__ import annotations

from .base import Adapter
from .cargo import CargoAdapter
from .flatpak_user import FlatpakUserAdapter
from .gem import GemAdapter
from .go import GoAdapter
from .npm import NpmAdapter
from .pip_user import PipUserAdapter
from .uv_tool import UvToolAdapter

#: Порядок важен: он определяет порядок вывода и разрешения неоднозначностей.
ADAPTER_CLASSES: list[type[Adapter]] = [
    UvToolAdapter,
    CargoAdapter,
    NpmAdapter,
    GemAdapter,
    PipUserAdapter,
    GoAdapter,
    FlatpakUserAdapter,
]

__all__ = [
    "Adapter",
    "ADAPTER_CLASSES",
    "UvToolAdapter",
    "CargoAdapter",
    "NpmAdapter",
    "GemAdapter",
    "PipUserAdapter",
    "GoAdapter",
    "FlatpakUserAdapter",
]
