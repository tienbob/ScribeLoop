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
# A container root has no user namespace sandbox. This flag is confined to CI;
# the production launcher does not disable WebKit's sandbox.
export WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1
printf 'disposable-test-password' | gnome-keyring-daemon --unlock --components=secrets
/usr/bin/python3 /src/packaging/linux/verify.py
desktop-file-validate /usr/share/applications/scribeloop.desktop
