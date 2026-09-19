#!/usr/bin/env bash
# Build SaveWaypoint into a zip that Decky Loader can install directly.
#
# Decky expects the archive to contain ONE top-level folder named after the
# plugin, holding plugin.json, main.py, the built dist/ and py_modules/.
#
#   ./scripts/package.sh   ->   release/SaveWaypoint.zip

set -euo pipefail

PLUGIN_NAME="SaveWaypoint"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="$(mktemp -d)"
OUT="$ROOT/release"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

cd "$ROOT"

echo "==> Building frontend"
pnpm install --frozen-lockfile
pnpm run build

if [ ! -f "dist/index.js" ]; then
  echo "ERROR: dist/index.js was not produced" >&2
  exit 1
fi

echo "==> Staging $PLUGIN_NAME"
DEST="$STAGE/$PLUGIN_NAME"
mkdir -p "$DEST/dist" "$DEST/py_modules"

cp plugin.json package.json main.py LICENSE README.md "$DEST/"
cp dist/index.js "$DEST/dist/"
cp py_modules/*.py "$DEST/py_modules/"
if [ -f assets/logo.png ]; then
  mkdir -p "$DEST/assets" && cp assets/logo.png "$DEST/assets/"
fi

# Strip caches that would otherwise ride along.
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> Zipping"
mkdir -p "$OUT"
rm -f "$OUT/$PLUGIN_NAME.zip"
( cd "$STAGE" && zip -qr "$OUT/$PLUGIN_NAME.zip" "$PLUGIN_NAME" )

echo "==> Done: release/$PLUGIN_NAME.zip ($(du -h "$OUT/$PLUGIN_NAME.zip" | cut -f1))"
unzip -l "$OUT/$PLUGIN_NAME.zip"
