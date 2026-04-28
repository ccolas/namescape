#!/usr/bin/env bash
# Deploy static/site/ to the ccolas.github.io repo under /namescape.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/static/site"
DEST_REPO="$HERE/../ccolas.github.io"
DEST="$DEST_REPO/namescape"

if [ ! -d "$SRC" ]; then
  echo "source not found: $SRC" >&2
  exit 1
fi
if [ ! -d "$DEST_REPO/.git" ]; then
  echo "destination repo not found: $DEST_REPO" >&2
  exit 1
fi

echo "wiping $DEST/*"
mkdir -p "$DEST"
rm -rf "$DEST"/* "$DEST"/.[!.]* 2>/dev/null || true

echo "copying $SRC/ -> $DEST/"
cp -R "$SRC"/. "$DEST"/

cd "$DEST_REPO"
git add namescape
if git diff --cached --quiet; then
  echo "no changes to commit"
  exit 0
fi
git commit -m "update namescape"
git push
