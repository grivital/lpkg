"""go install — бинари из ``$GOBIN``.

Go не ведёт никакого реестра установленных программ, поэтому единственный
источник истины — buildinfo, вшитая в бинарь (``go version -m``).
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from ..model import ActionResult, PackageRecord, version_is_newer
from ..proc import home_path, is_elf, is_executable
from .base import Adapter, ProgressCallback

_GO_PATH_LINE = re.compile(r"^\s*path\t(?P<path>[^\t]+)")
_GO_MODULE_LINE = re.compile(r"^\s*mod\t(?P<module>[^\t]+)\t(?P<version>[^\t]*)")


def parse_go_buildinfo(text: str) -> tuple[str, str, str | None] | None:
    """Извлекает путь устанавливаемого пакета, модуль и версию."""
    package = None
    module = None
    version = None
    for line in text.splitlines():
        path_match = _GO_PATH_LINE.match(line)
        if path_match is not None:
            package = path_match.group("path").strip()
        module_match = _GO_MODULE_LINE.match(line)
        if module_match is not None:
            module = module_match.group("module").strip()
            version = module_match.group("version").strip() or None
    if module is None:
        return None
    if package is None:
        package = module
    if version in ("(devel)", ""):
        version = None
    return package, module, version


def parse_go_version_m(text: str) -> tuple[str, str | None] | None:
    """Из вывода ``go version -m`` берёт (module path, version)."""
    info = parse_go_buildinfo(text)
    return (info[1], info[2]) if info is not None else None


class GoAdapter(Adapter):
    id = "go"
    label = "go"
    command = "go"

    def gobin(self) -> Path:
        cached = self._memo.get("gobin")
        if isinstance(cached, Path):
            return cached
        result = self.run_command(["go", "env", "GOBIN"], timeout=30)
        raw = result.out.strip() if result.ok else ""
        if not raw:
            gapath = self.run_command(["go", "env", "GOPATH"], timeout=30)
            base = gapath.out.strip() if gapath.ok and gapath.out.strip() else "~/go"
            raw = str(home_path(base) / "bin")
        path = Path(raw)
        self._memo["gobin"] = path
        return path

    def _buildinfo(self, binary: Path) -> tuple[str, str, str | None] | None:
        if not is_elf(binary):
            return None
        result = self.run_command(
            ["go", "version", "-m", str(binary)], timeout=30
        )
        if not result.ok:
            return None
        return parse_go_buildinfo(result.out)

    def list(self) -> list[PackageRecord]:
        gobin = self.gobin()
        records: list[PackageRecord] = []
        try:
            entries = sorted(gobin.iterdir())
        except OSError:
            return records
        for binary in entries:
            if binary.name.startswith(".") or not is_executable(binary):
                continue
            info = self._buildinfo(binary)
            if info is None:
                continue
            package, module, version = info
            record = PackageRecord(
                manager="go",
                name=binary.name,
                version=version,
                install_path=str(binary),
                bin_paths=[str(binary)],
                source=module,
                extra={"module": module, "package": package},
            )
            try:
                record.installed_at = datetime.fromtimestamp(binary.stat().st_mtime)
            except OSError:
                pass
            if version is None:
                record.add_warning(
                    "go-unknown-version",
                    "бинарь собран из рабочей копии: версия неизвестна",
                    "info",
                )
            records.append(record)
        return records

    def check_outdated(self, records: list[PackageRecord]) -> list[PackageRecord]:
        targets = [r for r in records if r.extra.get("module")]
        errors: list[str] = []

        def query(record: PackageRecord) -> None:
            module = str(record.extra["module"])
            result = self.run_command(
                ["go", "list", "-m", "-json", f"{module}@latest"],
                timeout=self.config.network_timeout,
            )
            if not result.ok:
                errors.append(f"{module}: {result.tail()}")
                return
            try:
                import json

                payload = json.loads(result.out)
            except (ValueError, TypeError) as exc:
                errors.append(f"{module}: неверный JSON: {exc}")
                return
            version = payload.get("Version") if isinstance(payload, dict) else None
            if not version:
                errors.append(f"{module}: ответ не содержит Version")
                return
            record.latest = str(version)
            if record.version is not None:
                record.update_available = version_is_newer(record.latest, record.version)

        if not targets:
            return records
        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            list(pool.map(query, targets))
        if errors:
            raise RuntimeError("; ".join(errors))
        return records

    # ----------------------------------------------------------------- команды

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        package = record.extra.get("package") or record.extra.get("module")
        if not package:
            return None
        return ["go", "install", f"{package}@latest"]

    def update_many_command(
        self, records, *, latest: bool = False
    ) -> list[str] | None:
        packages = [
            str(r.extra.get("package") or r.extra.get("module"))
            for r in records
            if r.extra.get("package") or r.extra.get("module")
        ]
        if len(packages) != len(records):
            return None
        packages = list(dict.fromkeys(packages))
        return ["go", "install", *(f"{package}@latest" for package in packages)]

    def install_command(self, spec: str) -> list[str] | None:
        return ["go", "install", spec]

    def uninstall_command(self, record: PackageRecord) -> list[str] | None:
        return None

    def uninstall(
        self,
        records,
        *,
        dry_run: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> list[ActionResult]:
        """У Go нет удаления пакетов — удаляем сам бинарь из GOBIN."""
        gobin = self.gobin()
        results: list[ActionResult] = []
        for record in records:
            path = Path(record.install_path or "")
            try:
                inside = path.resolve().parent == gobin.resolve()
            except OSError:
                inside = False
            if not inside or not path.exists():
                results.append(
                    ActionResult(
                        manager=self.id,
                        action="uninstall",
                        names=[record.name],
                        ok=False,
                        message=(
                            f"отказ: {path} не является файлом в GOBIN ({gobin})"
                        ),
                    )
                )
                continue
            command = ["rm", "--", str(path)]
            if dry_run:
                results.append(
                    ActionResult(
                        manager=self.id,
                        action="uninstall",
                        names=[record.name],
                        ok=True,
                        command=command,
                        from_version=record.version,
                        message="dry-run: " + " ".join(command),
                    )
                )
                continue
            if on_progress:
                on_progress(f"$ rm -- {path}")
            try:
                os.unlink(path)
            except OSError as exc:
                results.append(
                    ActionResult(
                        manager=self.id,
                        action="uninstall",
                        names=[record.name],
                        ok=False,
                        command=command,
                        message=str(exc),
                    )
                )
                continue
            results.append(
                ActionResult(
                    manager=self.id,
                    action="uninstall",
                    names=[record.name],
                    ok=True,
                    command=command,
                    from_version=record.version,
                    message="ok",
                )
            )
        return results
