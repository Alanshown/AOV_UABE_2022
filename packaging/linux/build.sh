#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."

expected=${1:?Pass the full verified 40-character source commit SHA}
case "$expected" in *[!0123456789abcdef]*) echo "Expected a lowercase SHA" >&2; exit 2;; esac
test "${#expected}" -eq 40
actual=$(git rev-parse HEAD)
test "$actual" = "$expected" || { echo "Refusing to build unexpected commit $actual" >&2; exit 2; }
test -z "$(git status --porcelain=v1 --untracked-files=all)" || { echo "Build requires a clean checkout" >&2; exit 2; }
test "$(dpkg --print-architecture)" = amd64
. /etc/os-release
test "${ID:-}" = debian && test "${VERSION_ID:-}" = 13
python - <<'PY'
import struct
import sys
import tkinter
if sys.version_info[:2] != (3, 13) or struct.calcsize("P") != 8 or tkinter.TkVersion != 8.6:
    raise SystemExit("Debian 13 Python 3.13 x64 with Tk 8.6 is required")
PY
python -m pip install -r packaging/linux/requirements-build.txt
python -m pip check
python -m pytest -q
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
python -m PyInstaller --noconfirm --clean packaging/linux/AOV_UABE_2022.spec
sh packaging/linux/package.sh
deb=artifacts/AOV_UABE_2022-2.5-Linux-x64.deb
test "$(dpkg-deb -f "$deb" Package)" = aov-uabe-2022
test "$(dpkg-deb -f "$deb" Version)" = 2.5-2
test "$(dpkg-deb -f "$deb" Architecture)" = amd64
dpkg --compare-versions 2.5-2 gt 2.5-1
(cd artifacts && sha256sum AOV_UABE_2022-2.5-Linux-x64.deb > SHA256SUMS.txt)
printf 'commit: %s\nruntime: %s\nlinux: %s\n' "$actual" "$(python --version)" "$PRETTY_NAME" > artifacts/SOURCE-LINUX.txt
