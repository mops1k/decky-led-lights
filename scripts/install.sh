#!/bin/bash
# Install a built Led Lights ZIP into Decky's plugin directory.
# Run it as root (the plugin directory is root-owned), for example:
#   pkexec bash scripts/install.sh packages/LedLights-<version>.zip
#   systemctl restart plugin_loader
set -euo pipefail

ZIP="${1:?usage: install.sh <path-to-zip> [plugin-dir]}"
PLUGIN_ROOT="${2:-${DECKY_PLUGINS:-/home/deck/homebrew/plugins}}"

if [ ! -f "$ZIP" ]; then
    echo "no such ZIP: $ZIP" >&2
    exit 1
fi
if [ ! -d "$PLUGIN_ROOT" ]; then
    echo "no plugin directory: $PLUGIN_ROOT" >&2
    exit 1
fi

WORK="$(mktemp -d /tmp/led-lights-install.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

bsdtar -xf "$ZIP" -C "$WORK"
SOURCE="$(find "$WORK" -maxdepth 2 -name plugin.json -printf '%h\n' | head -1)"
if [ -z "$SOURCE" ]; then
    echo "bad archive: no plugin.json" >&2
    exit 1
fi
NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$SOURCE/plugin.json")"

rm -rf "${PLUGIN_ROOT:?}/$NAME"
cp -a "$SOURCE" "$PLUGIN_ROOT/$NAME"
chown -R root:root "$PLUGIN_ROOT/$NAME"
chmod -R u+rwX,go+rX "$PLUGIN_ROOT/$NAME"

echo "installed $NAME into $PLUGIN_ROOT/$NAME"
echo "restart Decky to load it: systemctl restart plugin_loader"
