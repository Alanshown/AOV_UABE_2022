#!/bin/sh
# Run this after installing the .deb on a disposable Debian 13 runner.
# Launch the installed application under a clean profile and an Xvfb display.
set -eu
cd "$(dirname "$0")/../.."
test "$(dpkg-query -W -f='${Version}' aov-uabe-2022)" = 2.5-1
test -x /usr/bin/aov-uabe-2022
if [ "${1:-}" = --under-xvfb ]; then
    scratch=$2
    export HOME="$scratch" XDG_CONFIG_HOME="$scratch/config" XDG_DATA_HOME="$scratch/data" XDG_CACHE_HOME="$scratch/cache"
    export LANG=C.UTF-8 LC_ALL=C.UTF-8
    /usr/bin/aov-uabe-2022 >"$scratch/application.log" 2>&1 &
    app=$!
    trap 'kill "$app" 2>/dev/null || true; wait "$app" 2>/dev/null || true' EXIT
    for _ in $(seq 1 60); do
        if ! kill -0 "$app" 2>/dev/null; then
            cat "$scratch/application.log" >&2
            echo "Installed program exited before starting" >&2
            exit 1
        fi
        xwininfo -root -tree >"$scratch/windows.txt"
        # Match real application title, not an error dialog or the root window.
        if grep -E '"[^"]*2022 2\.5"' "$scratch/windows.txt" >"$scratch/match.txt"; then
            sleep 2
            kill -0 "$app"
            exit 0
        fi
        sleep 1
    done
    cat "$scratch/application.log" "$scratch/windows.txt" >&2
    echo "No actual 2.5 application window detected" >&2
    exit 1
fi
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
timeout --kill-after=5s 85s xvfb-run -a -s '-screen 0 1440x900x24' sh "$0" --under-xvfb "$scratch"
{
    printf 'Installed package: %s\n' "$(dpkg-query -W -f='${Version}' aov-uabe-2022)"
    printf '%s\n' 'Actual X11 application window:'
    cat "$scratch/match.txt"
} > artifacts/INSTALL-VERIFICATION.txt
