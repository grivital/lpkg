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

from ..model import ActionResult, PackageRecord
from ..proc import home_path, is_elf, is_executable
from .base import Adapter, ProgressCallback

_GO_MODULE_LINE = re.compile(r"^\s*mod\t(?P<module>[^\t]+)\t(?P<version>[^\t]*)")


def parse_go_version_m(text: str) -> tuple[str, str | None] | None:
    """Из вывода ``go version -m`` берёт (module path, version)."""
    for line in text.splitlines():
        match = _GO_MODULE_LINE.match(line)
        if not match:
            continue
        module = match.group("module").strip()
        version = match.group("version").strip() or None
        if version in ("(devel)", ""):
            version = None
        return module, version
    return None


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

    def _buildinfo(self, binary: Path) -> tuple[str, str | None] | None:
        if not is_elf(binary):
            return None
        result = self.run_command(
            ["go", "version", "-m", str(binary)], timeout=30
        )
        if not result.ok:
            return None
        return parse_go_version_m(result.out)

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
            module, version = info
            record = PackageRecord(
                manager="go",
                name=binary.name,
                version=version,
                install_path=str(binary),
                bin_paths=[str(binary)],
                source=module,
                extra={"module": module},
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

        def query(record: PackageRecord) -> None:
            module = str(record.extra["module"])
            result = self.run_command(
                ["go", "list", "-m", "-json", f"{module}@latest"],
                timeout=self.config.network_timeout,
            )
            if not result.ok:
                return
            try:
                import json

                payload = json.loads(result.out)
            except (ValueError, TypeError):
                return
            version = payload.get("Version") if isinstance(payload, dict) else None
            if version:
                record.latest = str(version)

        if not targets:
            return records
        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            list(pool.map(query, targets))
        return records

    # ----------------------------------------------------------------- команды

    def update_command(
        self, record: PackageRecord, *, latest: bool = False
    ) -> list[str] | None:
        module = record.extra.get("module")
        if not module:
            return None
        return ["go", "install", f"{module}@latest"]

    def update_many_command(
        self, records, *, latest: bool = False
    ) -> list[str] | None:
        modules = [str(r.extra["module"]) for r in records if r.extra.get("module")]
        if len(modules) != len(records):
            return None
        return ["go", "install", *(f"{module}@latest" for module in modules)]

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
