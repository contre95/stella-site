#!/usr/bin/env bash
# Put a stella checkout at .stella/, which the docs (Hugo mounts) are built from.
#
#   STELLA_DIR=../stella scripts/fetch-stella.sh   # symlink a local working copy
#   STELLA_REF=v1.0.1 scripts/fetch-stella.sh      # clone a specific tag/branch
#   scripts/fetch-stella.sh                        # clone the latest release tag
#
# The resolved ref is written to .stella-ref.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${STELLA_REPO:-https://github.com/contre95/stella}"
DEST="$ROOT/.stella"

rm -rf "$DEST"

if [[ -n "${STELLA_DIR:-}" ]]; then
  src="$(realpath "$STELLA_DIR")"
  ln -s "$src" "$DEST"
  ref="$(git -C "$src" describe --tags --always --dirty 2>/dev/null || echo local)"
  echo "Linked $src as .stella ($ref)"
else
  ref="${STELLA_REF:-}"
  if [[ -z "$ref" ]]; then
    # Release tags only (vX.Y.Z), newest first.
    ref="$(git ls-remote --tags --refs --sort=-v:refname "$REPO" 'v*' |
      sed 's|.*refs/tags/||' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
    [[ -n "$ref" ]] || { echo "no release tag found in $REPO" >&2; exit 1; }
  fi
  git clone --quiet --depth 1 --branch "$ref" "$REPO" "$DEST"
  echo "Cloned $REPO at $ref into .stella"
fi

echo "$ref" >"$ROOT/.stella-ref"
