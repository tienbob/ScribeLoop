#!/bin/sh
set -eu
# Container/CI only. Never run against a personal desktop session or keyring.
case "${SCRIBELOOP_DISPOSABLE_TEST:-}" in
  1) ;;
  *) echo 'Set SCRIBELOOP_DISPOSABLE_TEST=1 in a disposable container.' >&2; exit 2 ;;
esac
test_root="$(mktemp -d)"
export XDG_DATA_HOME="$test_root/data"
export XDG_CONFIG_HOME="$test_root/config"
export XDG_CACHE_HOME="$test_root/cache"
export XDG_RUNTIME_DIR="$test_root/runtime"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
# The production launcher runs the native GTK notebook directly; no WebKit sandbox
# flag is needed in normal installs.
printf 'disposable-test-password' | gnome-keyring-daemon --unlock --components=secrets
/usr/bin/python3 /src/packaging/linux/verify.py
desktop-file-validate /usr/share/applications/scribeloop.desktop
