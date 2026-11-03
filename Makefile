# lpkg — единое управление пакетами в домашнем каталоге.
#
# Установка через Portage (из ebuild):
#   emake DESTDIR="${D}" PREFIX="${EPREFIX}/usr" PYTHON="${PYTHON}" install
#
# Локальная установка:
#   make install PREFIX="$HOME/.local"
#   make install-pip
#
# Переменные:
#   PYTHON   интерпретатор (по умолчанию python3 из PATH)
#   PREFIX   префикс установки (по умолчанию /opt)
#   DESTDIR  корень staging-каталога (для Portage)

PYTHON ?= $(shell command -v python3 2>/dev/null || command -v python)
PREFIX ?= /opt
DESTDIR ?=

BINDIR  := $(PREFIX)/bin
SITELIB := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_path('purelib', vars={'base':'$(PREFIX)','platbase':'$(PREFIX)'}))")
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)

INSTALL      := install
INSTALL_DIR  := $(INSTALL) -d
INSTALL_DATA := $(INSTALL) -m 0644
INSTALL_PROG := $(INSTALL) -m 0755

.PHONY: all help check test install install-pip uninstall clean version

all: check

help:
	@echo "lpkg $(VERSION)"
	@echo
	@echo "Цели:"
	@echo "  make check        проверить синтаксис пакета"
	@echo "  make test         запустить тесты"
	@echo "  make install      установить файлы (PREFIX/DESTDIR)"
	@echo "  make install-pip  установить через pip в PREFIX"
	@echo "  make uninstall    удалить установленные файлы"
	@echo "  make clean        убрать кэш и артефакты сборки"
	@echo
	@echo "Текущие значения:"
	@echo "  PYTHON=$(PYTHON)"
	@echo "  PREFIX=$(PREFIX)"
	@echo "  DESTDIR=$(DESTDIR)"
	@echo "  SITELIB=$(SITELIB)"

version:
	@echo $(VERSION)

check:
	$(PYTHON) -m compileall -q lpkg
	@echo "Синтаксис в порядке."

test:
	$(PYTHON) -m unittest discover -s tests -v

# Файловая установка: не требует pip и build-изоляции, подходит для Portage.
install:
	$(INSTALL_DIR) "$(DESTDIR)$(SITELIB)/lpkg/adapters"
	$(INSTALL_DATA) lpkg/*.py "$(DESTDIR)$(SITELIB)/lpkg/"
	$(INSTALL_DATA) lpkg/adapters/*.py "$(DESTDIR)$(SITELIB)/lpkg/adapters/"
	$(INSTALL_DATA) lpkg/py.typed "$(DESTDIR)$(SITELIB)/lpkg/"
	$(INSTALL_DIR) "$(DESTDIR)$(BINDIR)"
	sed 's|@PYTHON@|$(PYTHON)|g' scripts/lpkg.in > "$(DESTDIR)$(BINDIR)/lpkg"
	chmod 0755 "$(DESTDIR)$(BINDIR)/lpkg"
	@echo "Установлено: $(DESTDIR)$(SITELIB)/lpkg"
	@echo "Команда:     $(DESTDIR)$(BINDIR)/lpkg"

install-pip:
	$(PYTHON) -m pip install --no-deps --no-build-isolation --prefix="$(PREFIX)" .

uninstall:
	rm -rf "$(DESTDIR)$(SITELIB)/lpkg"
	rm -f "$(DESTDIR)$(BINDIR)/lpkg"
	@echo "Удалено."

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.py[co]' -delete
	rm -rf build dist ./*.egg-info
