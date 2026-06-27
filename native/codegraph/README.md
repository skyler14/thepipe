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
