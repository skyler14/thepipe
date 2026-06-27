#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../native/codegraph/upstream.lock
source "$ROOT/native/codegraph/upstream.lock"

BUILD_ROOT="${TMPDIR:-/tmp}/thepipe-codegraph-${UPSTREAM_COMMIT:0:12}"
CHECKOUT="$BUILD_ROOT/source"
STAGE="$BUILD_ROOT/stage"
DIST="${DIST_DIR:-$ROOT/dist/codegraph}"

cleanup() {
    if [[ "${KEEP_BUILD:-0}" != "1" ]]; then
        rm -rf "$BUILD_ROOT"
    fi
}
trap cleanup EXIT

rm -rf "$BUILD_ROOT"
mkdir -p "$CHECKOUT" "$STAGE" "$DIST"

git -C "$CHECKOUT" init -q
git -C "$CHECKOUT" remote add origin "$UPSTREAM_REPO"
git -C "$CHECKOUT" fetch --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$CHECKOUT" checkout -q FETCH_HEAD

make -C "$CHECKOUT" -f Makefile.cbm "$UPSTREAM_BUILD_TARGET" \
    LIBGIT2_CFLAGS= LIBGIT2_LIBS=

cp "$CHECKOUT/build/c/codebase-memory-mcp" "$STAGE/codebase-memory-mcp"
strip -x "$STAGE/codebase-memory-mcp" 2>/dev/null || true

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
ARCHIVE="$DIST/codebase-memory-mcp-${OS}-${ARCH}-${UPSTREAM_COMMIT:0:12}.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGE" codebase-memory-mcp
shasum -a 256 "$ARCHIVE" > "$ARCHIVE.sha256"

printf 'Built %s\n' "$ARCHIVE"
