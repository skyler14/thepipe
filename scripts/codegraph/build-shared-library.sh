#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ "$(uname -s)" != "Darwin" ]]; then
    printf 'Shared-library packaging is currently verified only on Darwin.\n' >&2
    printf 'Use scripts/codegraph/build-sidecar.sh for the portable sidecar.\n' >&2
    exit 2
fi

BUILD_SHARED=1 "$ROOT/scripts/codegraph/build-sidecar.sh"
