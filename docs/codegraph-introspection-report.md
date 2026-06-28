# Codegraph Introspection Report

Date: June 27, 2026
Branch: `codex/codegraph-sidecar`
Repo: `/Users/skyler/Documents/thepipe`

## Scope

I exercised the new graph functionality against this repo itself:

- full graph emit from an existing deployment;
- sidecar-backed graph indexing;
- local graph actions: `summary`, `files`, `entities`, `edges`, `neighbors`,
  `sql`;
- sidecar archive install path;
- shared-library archive install path;
- old Python `code_relations: "map"` as the comparator baseline.

Token counts use `thepipe.core.calculate_tokens`, which is the repo's current
`len(text) / 4` estimator for text chunks.

## Commands Used

### Index + Emit

Package path:

```python
scrape_directory(
    "/Users/skyler/Documents/thepipe",
    options={
        "code_relations": "graph",
        "codegraph_binary": "/private/tmp/codebase-memory-mcp-stripped",
        "codegraph_index_mode": "fast",
    },
)
```

Result:

- 0.575s package call;
- 87 files;
- 1273 entities;
- 5129 edges;
- DB size: 5,537,792 bytes;
- no `.thepipe/`, `.git/`, or `dist/` files indexed;
- markdown/chunk text estimate: 92,047 tokens.

CLI JSON path:

```bash
/opt/anaconda3/envs/thepipe/bin/thepipe /Users/skyler/Documents/thepipe \
  --options '{"code_relations":"graph"}' -f json
```

Result:

- 2.913s;
- 3,125,564 stdout bytes;
- 781,391 estimated tokens;
- schema: `code-relations/v2`.

### Local Graph Actions

All local action package calls used:

```python
scrape_directory(repo, options={"code_relations": "graph", ...})
```

Measured package path:

| Action | Options | Time | Bytes | Est. tokens |
|---|---:|---:|---:|---:|
| `summary` | none | 0.0105s | 357 | 89 |
| `files` | `codegraph_limit=5` | 0.0019s | 685 | 171 |
| `entities` | `query=codegraph`, `limit=5` | 0.0299s | 1,791 | 447 |
| `edges` | `limit=5` | 0.0272s | 1,142 | 285 |
| `neighbors` | `process_codegraph`, depth 1, limit 10 | 0.0346s | 17,690 | 4,422 |
| `sql` | top files, limit 5 | 0.0014s | 517 | 129 |

Measured CLI path:

| Action | Time | Bytes | Est. tokens |
|---|---:|---:|---:|
| `summary` | 3.080s | 422 | 105 |
| `files`, limit 5 | 3.145s | 924 | 231 |
| `entities`, limit 5 | 3.360s | 2,483 | 620 |
| `edges`, limit 5 | 3.381s | 1,568 | 392 |
| `neighbors`, limit 10 | 3.322s | 24,612 | 6,153 |
| `sql`, limit 5 | 3.566s | 739 | 184 |

CLI startup/import dominates small graph actions. Package calls are the right
path for agent/server reuse.

### Archive Paths

Sidecar archive:

```python
scrape_directory(repo, options={
    "code_relations": "graph",
    "codegraph_action": "summary",
    "codegraph_refresh": False,
    "codegraph_archive": "/private/tmp/thepipe-codegraph-dist/codebase-memory-mcp-darwin-arm64-b075f0506ce4.tar.gz",
    "codegraph_sha256": "...",
})
```

Result:

- 3.702s;
- 357 bytes;
- 89 estimated tokens.

Shared-library archive:

```python
scrape_directory(repo, options={
    "code_relations": "graph",
    "codegraph_action": "summary",
    "codegraph_refresh": False,
    "codegraph_library_archive": "/private/tmp/thepipe-codegraph-dist/libthepipe-codegraph-darwin-arm64-b075f0506ce4.tar.gz",
    "codegraph_library_sha256": "...",
    "codegraph_library_name": "libthepipe_codegraph.dylib",
})
```

Result:

- 4.095s;
- 357 bytes;
- 89 estimated tokens;
- emitted native `level=info msg=mem.init ...` to stderr/stdout stream during
  context initialization.

### Python Map Baseline

Package path:

- 2.889s;
- 154,994 chunk text bytes;
- 38,748 estimated tokens.

CLI JSON:

- 5.799s;
- 5,241,944 stdout bytes;
- 1,310,486 estimated tokens;
- 3,878 stderr bytes from missing `c_sharp` and `objective_c` parsers;
- schema: `code-relations/v1`.

## Problems Found

### Fixed During This Investigation

1. `codegraph_action` with `-f json` was being wrapped as legacy
   `code-relations/v1` raw text instead of returning
   `thepipe-codegraph-action/v1`.

   Fix: `build_code_relations_json_payload` now returns graph action JSON
   directly.

2. `codegraph_action: "files"` ignored `codegraph_limit`.

   Fix: file action now slices by `codegraph_limit`.

3. The repo-local manifest leaked into `git status`.

   Fix: `ensure_git_excluded` now writes the full recommended local exclude
   list, including `.thepipe/codegraph/manifest.json`.

Tests added:

- `test_graph_action_payload_survives_json_projection`
- `test_graph_files_action_respects_limit`
- storage coverage for manifest/local cache exclude entries

### Still Open

1. `neighbors` was too verbose for a "small" action.

   Limit 10 emitted 17,690 package bytes because each node carried full
   attributes. Fixed after this report: compact graph action output now strips
   attributes by default unless `codegraph_verbose=true`. The same neighbor
   query measured 4,436 bytes after compacting.

2. CLI action latency is high for tiny reads.

   Local package calls are 1-35ms. CLI calls are ~3.1-3.6s. Ponytail fix: do
   nothing until this hurts agent loops; then add a small persistent server path
   or use package API from the agent runtime.

3. `codegraph_refresh` used to default to true even for action calls when a backend is
   supplied.

   Fixed after this report: if `codegraph_action != "emit"` and a deployment
   exists, default refresh is false unless explicitly set true.

4. Shared-library context initialization logs to stderr/stdout.

   This can pollute command output in some execution contexts. Ponytail fix:
   set donor log level to quiet in `tp_context_new` or expose a
   `codegraph_quiet` env/default.

5. Full JSON modes are enormous.

   `graph -f json` is 781k estimated tokens. Python `map -f json` is 1.31M.
   Both are valid for programmatic use but bad defaults for agent context.
   Ponytail fix: keep docs pushing `summary`/`entities`/`neighbors` before full
   graph JSON.

6. Python map still emits parser warnings for `c_sharp` and `objective_c`.

   This is not new, but the sidecar graph path avoids it. Ponytail fix: downgrade
   missing optional parser tracebacks to a one-line warning.

## Easy Victories To Consider

1. Keep compact graph action output as the default.

   Attributes are now stripped unless `codegraph_verbose=true`. Next cheap win
   is snapshotting compact output shape before skill rollout.

2. Keep action refresh policy explicit.

   If a graph DB exists and the user asked for an action, read it. Reindex only
   when `codegraph_refresh=true` or no deployment exists.

3. Quiet shared-library init logs.

   One C-side default is better than filtering Python stderr.

4. Add a report-style action later, not now.

   Current `summary` + `sql` already covers most inspection. A custom
   `health`/`diagnostics` action is only worth it after repeated manual SQL.

5. Do not add skill defaults yet.

   The skill should eventually recommend graph actions, not full graph emit.
   But artifact publishing/checksum distribution should land first.
