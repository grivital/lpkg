EAPI=8

PYTHON_COMPAT=( python3_{11..14} )

inherit python-single-r1

DESCRIPTION="Единое управление пакетами, установленными в домашний каталог"
HOMEPAGE="https://github.com/grivital/lpkg"
SRC_URI="https://github.com/grivital/lpkg/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"
IUSE="tui"
REQUIRED_USE="${PYTHON_REQUIRED_USE}"

RDEPEND="${PYTHON_DEPS}
	tui? ( dev-util/dialog )"
BDEPEND="${PYTHON_DEPS}"

src_install() {
	emake \
		DESTDIR="${D}" \
		PREFIX="${EPREFIX}/usr" \
		PYTHON="${PYTHON}" \
		install

	python_fix_shebang "${ED}/usr/bin/lpkg"
	python_optimize
}
