# Changelog

Все заметные изменения проекта. Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [Semantic Versioning](https://semver.org/lang/ru/).

## [0.1.1] — 2026-11-03

### Исправлено

- `lpkg`, установленный в нестандартный префикс (`/opt`), запускается без
  `PYTHONPATH`: консольный скрипт сам добавляет
  `<prefix>/lib/pythonX.Y/site-packages` в `sys.path`.
- Smoke-тест CI теперь устанавливает пакет в `/opt` и проверяет автономный
  запуск.

## [0.1.0] — 2026-11-03

Первая рабочая версия.

### Добавлено

- Единая инвентаризация пакетов из `uv tool`, `cargo install`, `npm -g`,
  `gem`, `pip --user`, `go install` и `flatpak --user`.
- Сканер `bin`-каталогов: сироты, битые симлинки, дубликаты команд,
  «невидимые» менеджеру пакеты, устаревшие receipts.
- Распознавание Go-бинарей по buildinfo (`go version -m`).
- Команды `list`, `outdated`, `update`, `install`, `uninstall`, `info`,
  `doctor`, `diff`, `history`, `export`, `tui`.
- Проверка обновлений и обновление «оптом» с `--dry-run` и подтверждением.
- Снапшоты и журнал операций в `~/.local/state/lpkg`.
- JSON-вывод для всех команд, кроме `tui`.
- Интерактивный интерфейс на `dialog`.
- Makefile с поддержкой `DESTDIR` для установки через Portage.
- 36 модульных тестов без внешних зависимостей.

[0.1.1]: https://github.com/grivital/lpkg/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/grivital/lpkg/releases/tag/v0.1.0
