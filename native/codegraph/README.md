# Codegraph Native Boundary

`upstream.lock` pins the donor source used by sidecar and future shared-library
builds. Generated grammar C files are build inputs fetched into a temporary
checkout. They are not vendored into this repository or installed with the
Python package.

Run `scripts/codegraph/build-sidecar.sh`. It:

1. fetches exactly `UPSTREAM_COMMIT`;
2. builds the standard binary with optional libgit2 disabled for portability;
3. packages only the compiled executable;
4. writes a SHA-256 checksum;
5. removes the large temporary checkout unless `KEEP_BUILD=1`.

The generated archive belongs in release storage, not git.

Run `scripts/codegraph/build-shared-library.sh` on macOS to build the
context-based ctypes library archive as well as the standard sidecar archive.
This is equivalent to `BUILD_SHARED=1 scripts/codegraph/build-sidecar.sh` but is
clearer for CI/release jobs. The shared library reuses the donor MCP dispatcher
through `tp_context_call`; it does not expose donor C structs. Each context
carries a repo-local cache directory. Calls temporarily install that directory
under a global lock because the pinned donor still resolves `CBM_CACHE_DIR`
process-wide.

Runtime installation supports either compiled artifact:

- sidecar archive: `codegraph_archive` + `codegraph_sha256`;
- shared-library archive: `codegraph_library_archive` +
  `codegraph_library_sha256`.

Both paths install only the compiled artifact into the thepipe cache. Neither
path installs raw generated grammar C source.

Linux and Windows shared-library packaging remain gated. The sidecar is the
portable fallback until PIC/linking and allocator tests pass on those targets.
