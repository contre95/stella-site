#!/usr/bin/env bash
# Build the static Stella demo.
#
# Generates decks with a simulated study history (demo/gen, using Stella's own
# scheduler), runs a real Stella instance, seeds it through its API and crawls
# it into a static site (default: ./dist-demo).
# Stella is built from $STELLA_DIR (default: .stella, see scripts/fetch-stella.sh).
#
# Requires: go, git, curl, python3.
# Usage: [STELLA_DIR=...] demo/build.sh [out_dir]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO="$ROOT/demo"
SRC="$(realpath -m "${STELLA_DIR:-$ROOT/.stella}")"
OUT="$(realpath -m "${1:-$ROOT/dist-demo}")"
PORT="${DEMO_PORT:-8959}"
BASE="http://127.0.0.1:$PORT"
WORK="$(mktemp -d -t stella-demo.XXXXXX)"
PID=""

cleanup() {
  # wait reports the server's SIGTERM status (143); don't let set -e turn it into ours.
  if [[ -n "$PID" ]]; then kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

[[ -f "$SRC/go.mod" ]] || { echo "no stella checkout at $SRC (run scripts/fetch-stella.sh)" >&2; exit 1; }

echo "==> Generating decks in $WORK/decks"
# A copy of demo/gen, pointed at the stella checkout for the srs package.
cp -r "$DEMO/gen" "$WORK/gen"
(cd "$WORK/gen" && go mod edit -replace "github.com/contre95/stella=$SRC" && go run . "$DEMO/decks.json" "$WORK/decks")

# demo/config.yaml placeholders: @WORK@ (stripped from the output later) and @PORT@.
mkdir -p "$WORK/images"
sed -e "s|@WORK@|$WORK|g" -e "s|@PORT@|$PORT|g" "$DEMO/config.yaml" >"$WORK/config.yaml"

echo "==> Starting Stella on :$PORT"
# The server finds views/ and public/ relative to its cwd. public/css/app.css is committed.
cd "$SRC"
go build -o "$WORK/stella" ./src
IMAGE_TAG="${IMAGE_TAG:-demo}" STELLA_CONFIG_PATH="$WORK/config.yaml" \
  "$WORK/stella" >"$WORK/server.log" 2>&1 &
PID=$!
for _ in $(seq 1 180); do
  curl -fs "$BASE/health" >/dev/null 2>&1 && break
  kill -0 "$PID" 2>/dev/null || { cat "$WORK/server.log"; exit 1; }
  sleep 1
done
curl -fs "$BASE/health" >/dev/null || { echo "server did not start"; cat "$WORK/server.log"; exit 1; }

echo "==> Seeding"
python3 "$DEMO/seed.py" "$BASE" "$WORK/decks"

echo "==> Crawling into $OUT"
python3 "$DEMO/crawl.py" "$BASE" "$OUT" --public "$SRC/public" --strip "$WORK"

echo "==> Done: $(find "$OUT" -type f | wc -l) files, $(du -sh "$OUT" | cut -f1)"
