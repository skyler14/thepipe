# Codegraph Port Spec

## Purpose

Port the comparator codegraph backend into `thepipe` in stages without dumping the
whole comparator source tree into this repo. The first fork uses the comparator
full binary as a sidecar. Python normalizes that API into `thepipe` outputs.
Later stages move the same contract to a shared library, then retire inferior
Python codegraph pieces.

This spec uses comparator source paths relative to the comparator repo root, not
temporary local paths.

## Implementation Status

Implemented on `codex/codegraph-sidecar`:

- complete Python facade for all 14 donor MCP tools;
- read-only local graph accessor for deployed SQLite graphs;
- hardened sidecar process boundary with timeout, cache isolation, MCP
  normalization, and version checks;
- SHA-256 verified atomic archive installation;
- repo-local manifest plus pointer-only SQLite master registry;
- DB/WAL/SHM accounting, soft-cap reporting, and explicit global LRU pruning;
- read-only donor SQLite adapter, integrity check, and schema fingerprint;
- `code-relations/v2`, v1 compatibility, compact digest, and per-file chunks;
- pinned temporary source build that packages only compiled artifacts;
- context-based ctypes ABI and C shim over `cbm_mcp_handle_tool`;
- explicit `code_relations: "graph"` package/CLI mode;
- public graph-mode tool schema and registration docs;
- explicit graph action output for `summary`, `files`, `entities`, `edges`,
  `neighbors`, and bounded read-only SQL;
- sidecar bootstrap from local tar/zip archive with required SHA-256 and
  pinned runtime version validation;
- local git exclusion through `.git/info/exclude`.

Measured on arm64 macOS at pinned commit
`b075f0506ce4286219edd1bc3dccb196f2ed7cb0`:

- donor checkout: 1.4 GB;
- generated/native source under `internal/`: 1.2 GB;
- temporary build directory: 537 MB;
- standard executable: 257 MB;
- gzip release archive: 36 MB;
- shared library: 257 MB;
- fresh `scripts/codegraph/build-shared-library.sh` output on June 27, 2026:
  37 MB sidecar archive and 36 MB shared-library archive under `/private/tmp`;
- two-file fixture graph: 1.69 MB, 8 nodes, 11 edges.

The grammar checkout and object files are build-only. Installed users need the
executable or shared library, not the 1.2 GB source tree.

Verified behavior:

- real sidecar indexing and all read/query tools;
- real shared-library indexing and the same tool contract;
- real shared-library archive installation through `codegraph_library_archive`;
- local graph actions through `codegraph_action`;
- both native modes in one Python process;
- v2 CLI output from an auto-detected deployment.

Still gated:

- published per-platform release checksums/catalog;
- Linux and Windows shared-library PIC/allocator CI;
- hand-authored skill-file rollout and default agent behavior;
- replacement/removal of existing Python analyzer modules.

## Source Findings

Scoped `thepipe` map over comparator `src/` found:

- 119 related files.
- 1439 functions.
- 1066 class-like constants/types in digest output.
- 93% token reduction.
- Core source size around 2.4 MB.

Full comparator checkout map was intentionally stopped because generated grammar
source dominates the tree and made repo-wide mapping too slow. That is part of
the design constraint: generated grammar C files are build material, not source
we want copied into this repo.

Important source anchors:

- `src/mcp/mcp.c`: MCP tool definitions, dispatch, handlers, JSON-RPC loop.
- `src/main.c`: CLI wrapper around MCP tools and stdio server entry.
- `src/store/store.h`: store/query/traversal/architecture/ADR/vector API.
- `src/store/store.c`: SQLite schema, indexes, FTS, search, BFS, architecture.
- `src/pipeline/pipeline.h`: indexing pipeline public API.
- `src/pipeline/pipeline.c`: pipeline orchestration.
- `src/pipeline/pipeline_incremental.c`: incremental indexing and file hash reuse.
- `src/cypher/cypher.h`, `src/cypher/cypher.c`: read-only Cypher subset.
- `src/semantic/semantic.h`, `src/semantic/semantic.c`: semantic similarity.
- `src/traces/traces.h`, `src/traces/traces.c`: trace helpers.
- `src/watcher/watcher.h`, `src/watcher/watcher.c`: background change detection.
- `src/ui/http_server.c`: UI HTTP API, useful later but not first port target.

## Non-Goals

- Do not wire the sidecar into Codex/agent skills during early work.
- Do not install MCP hooks or skill instructions until full binary support is
  achieved and tested.
- Do not vendor generated grammar `parser.c` source into this repo.
- Do not make chunks the canonical graph model.
- Do not implement Cypher in Python.
- Do not remove current Python analyzer until native fallback and parity tests
  exist.

## Skill Integration Gate

Generated registration text may mention graph mode once full binary support is
green. Hand-authored skill files and default agent behavior stay gated until
distribution and support policy are cleaner.

Do not add sidecar-first instructions to skill files yet. Notes to add later:

- use `code_relations: "graph"` only when a pinned binary/archive is installed;
- treat graph freshness as policy, not a tax on every read: use cached graph
  actions for quick lookups, refresh periodically, after meaningful git
  changes, or when the user asks for current impact analysis;
- prefer `codegraph_action: "summary"` or `"entities"` before emitting full v2
  graph JSON on huge repos;
- use `codegraph_action: "neighbors"` for impact/caller/callee context;
- use `codegraph_action: "sql"` only for bounded read-only inspection;
- keep `.thepipe/codegraph/cache/` git-ignored unless explicitly requested;
- fall back to `code_relations: "map"` when no deployment or sidecar exists.

Default skill rollout remains blocked until:

1. Sidecar binary install/discovery works on supported OS/arch.
2. Binary version is pinned and verified by checksum.
3. `index_repository`, `search_graph`, `trace_path`, `get_code_snippet`,
   `get_graph_schema`, `get_architecture`, `search_code`, `detect_changes`,
   `list_projects`, `index_status`, and `delete_project` pass contract tests.
4. Projection to `Chunk[]`, compact digest markdown, and `code-relations/v2`
   passes snapshot tests.
5. Disk policy prevents unbounded DB growth.
6. Failure paths fall back to current `thepipe` code relations or emit clear
   diagnostics.

Only after that:

- expose sidecar in agent-facing skill docs,
- consider MCP/server install hooks.

## Product Boundary

Two surfaces exist.

### Graph-Native Surface

Canonical for code intelligence:

- persistent DB,
- nodes,
- edges,
- file hashes,
- schema,
- graph queries,
- source snippets,
- architecture summaries,
- impact analysis,
- cross-repo links,
- ADRs,
- traces.

### Thepipe Compatibility Surface

Projection from graph-native state:

- `Chunk[]`,
- compact digest markdown,
- `code-relations/v1` compatibility,
- `code-relations/v2`,
- `map`,
- `mapnn`,
- `mapnew`,
- normal `scrape_directory` integration.

Rule: graph-native features may exist without forcing a chunk representation.
Chunks are an LLM-friendly view, not the source of truth.

## Backend Interface

Python owns the public interface.

```python
class CodeGraphBackend:
    def index_repo(self, repo, db, options): ...
    def status(self, project_or_repo, options): ...
    def list_projects(self, options): ...
    def delete_project(self, project, options): ...
    def search_graph(self, request): ...
    def query_graph(self, request): ...
    def trace_path(self, request): ...
    def get_code_snippet(self, request): ...
    def get_graph_schema(self, request): ...
    def get_architecture(self, request): ...
    def search_code(self, request): ...
    def detect_changes(self, request): ...
    def manage_adr(self, request): ...
    def ingest_traces(self, request): ...
    def emit_chunks(self, request): ...
    def emit_code_relations(self, request): ...
```

Implementations:

- `PythonCodeGraphBackend`: current/fallback path.
- `SidecarCodeGraphBackend`: full comparator binary, first native path.
- `SharedLibCodeGraphBackend`: later `ctypes` path over shared library.

## Sidecar Mode

Use comparator binary first because it already exposes tools through CLI:

```text
codebase-memory-mcp cli [--json] <tool_name> <json_args>
```

Source: `src/main.c` has CLI dispatch through `cbm_mcp_handle_tool`; help lists
tools in `src/main.c`.

Python wrapper rules:

- invoke sidecar with JSON arguments,
- request raw JSON where possible,
- parse MCP text envelope,
- normalize into stable dataclasses,
- never expose comparator stderr as structured JSON,
- pin version and checksum,
- treat schema changes as adapter migrations.

## Shared Library Mode

After sidecar contract stabilizes, build a shared library with the same JSON
contract. The implemented ABI uses an explicit context because the donor cache
directory is otherwise process-global:

```c
tp_context *tp_context_new(const char *cache_dir);
int tp_context_call(tp_context *context, const char *tool,
                    const char *request_json, char **out_json);
void tp_context_free(tp_context *context);
const char *tp_version(void);
void tp_string_free(char *ptr);
```

Python wrapper uses stdlib `ctypes`. Avoid CPython extension/Cython first:
calls are coarse, ABI pain is not worth it.

Keep sidecar fallback after shared library lands.

The first shim serializes calls only while installing/restoring
`CBM_CACHE_DIR`, then dispatches directly through `cbm_mcp_handle_tool`.
This preserves repo-local stores without leaking donor structs into Python.
Future donor work should move cache resolution into `cbm_mcp_server_t`, which
will remove the environment lock without changing the Python ABI.

## Shared-Library Migration Readiness

The sidecar is functional enough to remain the portability fallback. Migration
to shared-library focus should not mean "delete sidecar"; it should mean "prefer
shared library only where the process model is worth the tighter coupling."

Insights gathered after real repo introspection:

- Full JSON is not the LLM-facing product. On this repo, full graph JSON was
  roughly 781k estimated tokens and old map JSON was roughly 1.31M estimated
  tokens. Default agent behavior must prefer compact graph actions or old
  `map -f text`, not full `-f json`.
- Local package graph actions are fast enough for agent loops, roughly
  1-35ms on this repo. CLI action calls are roughly 3.1-3.6s due Python/CLI
  startup. Shared library mainly helps long-lived Python processes, not
  one-shot CLI startup.
- `neighbors` was too verbose for a small graph action because node and edge
  attributes were carried through. Compact graph action output now strips
  attributes unless `codegraph_verbose=true`; keep snapshotting this before
  skill rollout.
- `codegraph_refresh` defaults to false for read actions when a deployment
  already exists. Reindexing remains correct for `emit`, explicit refresh, and
  missing deployments, but read actions should not pay the freshness tax by
  default.
  Freshness can be a separate periodic/optional policy: read actions should read
  the existing DB by default, while agents or callers can request refresh when
  the repo changed, the DB is stale by age/head, or the user asks for current
  state.
- Shared-library initialization can emit native logs. Sidecar isolates this
  better. Before shared library becomes preferred, add a quiet native-log
  default or explicit log sink.
- Shared library removes process-spawn overhead but also removes crash
  isolation. Any allocator, SQLite, parser, or C UB crash can take down the
  Python process. Keep sidecar fallback and add stress tests before defaulting
  shared library in agent runtimes.
- The current context ABI is intentionally coarse JSON. Do not expose donor C
  structs yet. The fastest safe win is keeping `tp_context_call` stable while
  optimizing output shape and cache/context lifetime.
- Donor cache selection remains partly process-global. The shim serializes
  environment install/restore, but concurrent contexts still need stress tests
  before a shared library backend is considered thread-safe for servers.
- macOS shared-library build is proven. Linux/Windows still need PIC/linker,
  allocator, symbol visibility, SQLite, and packaging CI.
- Final prebuilt artifacts are the dependency boundary. Prebuilt grammar object
  packs are likely more fragile than final `.dylib/.so/.dll` artifacts because
  they become platform/compiler/ABI-specific.

Extra evidence to gather before changing default preference from sidecar to
shared library:

1. Sidecar vs shared-library latency for repeated tool calls in the same Python
   process: `summary`, `entities`, `neighbors`, `search_graph`, `query_graph`,
   and `trace_path`.
2. Shared-library repeated index/query/delete loop under one Python process to
   detect allocator or SQLite lifetime bugs.
3. Two-context test indexing two different repos with different cache dirs.
4. Threaded call test, even if the policy remains "not thread-safe"; document
   actual behavior.
5. Native log suppression test.
6. Artifact install test from release-like catalog, not just local paths.
7. Linux x64 sidecar artifact, then Linux x64 shared library artifact.
8. Windows sidecar artifact before Windows shared library.
9. More compact output snapshots for graph actions and token budget checks.
10. Incremental/no-op timing after no file changes, both sidecar and shared
    library.

Go/no-go for shared-library default in Python package contexts:

- all 14 tool contracts pass on sidecar and shared library;
- graph actions pass compact output snapshots;
- no native logs leak by default;
- repeated in-process calls are measurably faster than sidecar;
- two-context cache isolation passes;
- crash/stress loop survives repeated index/query/delete;
- sidecar fallback stays available for unsupported platforms and crash
  isolation.

## Grammar Strategy

Comparator generated grammar C files are not runtime source. They are build
inputs compiled into binary/library artifacts.

Do not commit generated grammar source into this repo. Instead:

- keep grammar pack manifests,
- build grammar packs in release/CI or separate native build repo,
- ship compiled artifacts,
- support core and full packs.

Packs:

- `core`: Python, JS, TS, TSX, Go, Rust, C, C++, Java, C#, Swift, Kotlin, PHP,
  HTML, CSS.
- `infra`: YAML, JSON, Dockerfile, HCL, TOML, K8s-related parsers.
- `full`: all supported comparator grammars.

## Storage Layout

Repo-local:

```text
.thepipe/
  codegraph/
    manifest.json
    cache/
      <normalized-absolute-repo-path>.db
```

The cache directory is added to `.git/info/exclude` by default. Set
`codegraph_git_exclude=false` when the caller manages ignore policy. Explicit
shareable donor artifacts remain `.codebase-memory/graph.db.zst` and are
created only when persistence is enabled.

Global master:

```text
~/.cache/thepipe/master.sqlite
```

Master stores pointers and stats only:

```sql
repos(root_path, db_path, artifact_path, project_name, backend_kind,
      backend_version, schema_fingerprint, last_seen, last_head,
      size_bytes, file_count, entity_count, edge_count, status)
```

Master does not copy project graphs.

## Disk Policy

Defaults:

- repo DB gitignored,
- global cache cap: 10 GB,
- per-repo soft cap: 500 MB,
- never prune project-owned `.thepipe` without explicit opt-in,
- prune global sidecar caches by LRU.

Retention order:

1. Keep graph core: projects, files, nodes/entities, edges, file hashes.
2. Keep compact digests.
3. Drop raw source/snippets first.
4. Drop old run records.
5. Vacuum if reclaim estimate exceeds 64 MB.
6. Mark oversize instead of deleting graph core.

The donor incremental pipeline is retained. It compares discovered files to
persisted `file_hashes` using mtime and size, reparses only changed files, and
returns through `incremental.noop` when there are no changes. Do not add a
second Python content scanner unless measurement proves this insufficient.

## MCP Tool Surface To Support

Tool definitions live in `src/mcp/mcp.c`. Dispatch lives in
`cbm_mcp_handle_tool`.

### `index_repository`

Source:

- `src/mcp/mcp.c`: tool schema and `handle_index_repository`.
- `src/pipeline/pipeline.h`: `cbm_pipeline_new`, `cbm_pipeline_run`,
  `cbm_pipeline_set_persistence`.
- `src/pipeline/pipeline.c`: orchestration.
- `src/pipeline/pipeline_incremental.c`: incremental indexing.

Inputs:

- `repo_path` required.
- `mode`: `full`, `moderate`, `fast`, `cross-repo-intelligence`.
- `target_projects`: for cross-repo mode.
- `persistence`: export compressed artifact.

Thepipe interface:

```python
index_repo(repo_path, mode="full", persistence=False,
           target_projects=None, db=None) -> IndexResult
```

Must expose:

- status,
- project name,
- DB path,
- node/edge counts,
- skipped dirs,
- degraded/error status,
- artifact status,
- backend diagnostics.

### `search_graph`

Source:

- `src/mcp/mcp.c`: `handle_search_graph`, BM25 path, semantic query.
- `src/store/store.h`: `cbm_store_search`, `cbm_store_vector_search`.
- `src/store/store.c`: regex/LIKE/FTS/vector implementation.

Inputs:

- `project` required.
- BM25 `query`.
- structural filters: `label`, `name_pattern`, `qn_pattern`, `file_pattern`,
  `relationship`, `min_degree`, `max_degree`, `exclude_entry_points`,
  `include_connected`.
- semantic vector `semantic_query: list[str]`.
- `limit`, `offset`.

Thepipe interface:

```python
search_graph(project, query=None, label=None, name_pattern=None,
             qn_pattern=None, file_pattern=None, relationship=None,
             semantic_query=None, limit=200, offset=0, **filters)
```

Must preserve:

- pagination (`total`, `has_more`),
- separate semantic results,
- connected names,
- result properties.

### `query_graph`

Source:

- `src/mcp/mcp.c`: `handle_query_graph`.
- `src/cypher/cypher.h`, `src/cypher/cypher.c`: parser/executor.

Inputs:

- `project` required.
- `query` required.
- `max_rows`.

Thepipe interface:

```python
query_graph(project, query, max_rows=None) -> QueryRows
```

Must expose:

- columns,
- rows,
- total,
- query errors.

Do not reimplement Cypher in Python.

### `trace_path` / `trace_call_path`

Source:

- `src/mcp/mcp.c`: `handle_trace_call_path`, edge type resolution.
- `src/store/store.h`: `cbm_store_bfs`, impact/risk helpers.

Inputs:

- `project` required.
- `function_name` required.
- `direction`: `inbound`, `outbound`, `both`.
- `depth`.
- `mode`: `calls`, `data_flow`, `cross_service`.
- `parameter_name`.
- explicit `edge_types`.
- `risk_labels`.
- `include_tests`.

Thepipe interface:

```python
trace_path(project, function_name, direction="both", depth=3,
           mode="calls", edge_types=None, risk_labels=False,
           include_tests=False, parameter_name=None)
```

Must expose:

- root,
- callers,
- callees,
- visited nodes with hop,
- edges,
- risk labels,
- test markers.

### `get_code_snippet`

Source:

- `src/mcp/mcp.c`: `handle_get_code_snippet`, suffix resolution,
  ambiguity suggestions, source containment check.
- `src/store/store.h`: QN/name lookup, degree, neighbor names.

Inputs:

- `project` required.
- `qualified_name` required.
- `include_neighbors`.

Thepipe interface:

```python
get_code_snippet(project, qualified_name, include_neighbors=False)
```

Must support:

- exact QN,
- suffix match,
- ambiguity suggestions,
- source range,
- node properties,
- caller/callee counts,
- optional neighbor names.

### `get_graph_schema`

Source:

- `src/mcp/mcp.c`: `handle_get_graph_schema`.
- `src/store/store.h`: `cbm_store_get_schema`.

Inputs:

- `project` required.

Thepipe interface:

```python
get_graph_schema(project) -> GraphSchema
```

Must expose:

- node labels and counts,
- node property keys,
- edge types and counts,
- edge property keys,
- ADR presence/hints.

### `get_architecture`

Source:

- `src/mcp/mcp.c`: `handle_get_architecture`.
- `src/store/store.h`: `cbm_store_get_architecture`,
  `cbm_store_get_schema_counts_scoped`.
- `src/store/store.c`: language, package, entry point, route, hotspot,
  boundary, service, layer, cluster, file tree builders.

Inputs:

- `project` required.
- optional `path`.
- optional `aspects`.

Thepipe interface:

```python
get_architecture(project, path=None, aspects=None)
```

Must expose:

- total/scoped nodes and edges,
- labels,
- edge types,
- relationship patterns,
- languages,
- packages,
- entry points,
- routes,
- hotspots,
- boundaries,
- services,
- layers,
- clusters,
- file tree,
- cross-repo summary.

### `search_code`

Source:

- `src/mcp/mcp.c`: `handle_search_code`.
- Uses grep over indexed file list, graph node overlap, batch degree scoring.
- `src/store/store.h`: `cbm_store_list_files`,
  `cbm_store_find_nodes_by_file_overlap`, `cbm_store_batch_count_degrees`.

Inputs:

- `project` required.
- `pattern` required.
- `file_pattern`.
- `path_filter`.
- `mode`: `compact`, `full`, `files`.
- `context`.
- `regex`.
- `limit`.

Thepipe interface:

```python
search_code(project, pattern, file_pattern=None, path_filter=None,
            mode="compact", context=None, regex=False, limit=10)
```

Must expose:

- raw grep match count,
- deduped result count,
- ranked containing symbols,
- compact signatures,
- optional source/context,
- file-only result mode.

### `list_projects`

Source:

- `src/mcp/mcp.c`: `handle_list_projects`.
- Scans cache directory for `.db` files.

Inputs: none.

Thepipe interface:

```python
list_projects() -> list[ProjectStatus]
```

Must expose:

- project name,
- root path,
- git context,
- node/edge counts,
- DB size.

### `delete_project`

Source:

- `src/mcp/mcp.c`: `handle_delete_project`.
- Deletes DB, WAL, SHM under lock.

Inputs:

- `project` required.

Thepipe interface:

```python
delete_project(project) -> DeleteResult
```

Must expose:

- `deleted`,
- `not_found`,
- `delete_failed`,
- error detail.

Python must not delete repo-local DBs unless explicit policy allows.

### `index_status`

Source:

- `src/mcp/mcp.c`: `handle_index_status`.

Inputs:

- `project` required.

Thepipe interface:

```python
index_status(project) -> IndexStatus
```

Must expose:

- status: `ready`, `empty`, `missing`, `indexing`, `degraded`,
- nodes,
- edges,
- root path,
- git context,
- backend version/schema.

### `detect_changes`

Source:

- `src/mcp/mcp.c`: `handle_detect_changes`.
- Shells out to `git diff --name-only`.
- Maps changed files to symbols with `cbm_store_find_nodes_by_file`.

Inputs:

- `project` required.
- `scope`.
- `depth`.
- `base_branch`.
- `since`.

Thepipe interface:

```python
detect_changes(project, scope="symbols", depth=2,
               base_branch="main", since=None)
```

Must expose:

- changed files,
- changed count,
- impacted symbols,
- depth,
- git errors/hints.

Roadmap should improve blast radius beyond current simple changed-file symbol
list by combining `detect_changes` with `trace_path`.

### `manage_adr`

Source:

- `src/mcp/mcp.c`: `handle_manage_adr`.
- `src/store/store.h`: ADR store/update/delete/section helpers.

Inputs:

- `project` required.
- `mode`: `get`, `update`, `sections`.
- `content`.
- `sections`.

Thepipe interface:

```python
manage_adr(project, mode="get", content=None, sections=None)
```

Must expose:

- stored content,
- update status,
- section list,
- no-ADR hint.

### `ingest_traces`

Source:

- `src/mcp/mcp.c`: `handle_ingest_traces`.
- `src/traces/traces.h`: HTTP trace extraction helpers.

Inputs:

- `project` required.
- `traces` required.

Current comparator handler only acknowledges; runtime edge creation is not yet
implemented.

Thepipe interface:

```python
ingest_traces(project, traces) -> TraceIngestResult
```

Must expose current limitation:

- accepted count,
- no graph mutation unless backend supports it,
- diagnostics.

## Store Surface Needed For Shared Library Stage

From `src/store/store.h`, Python/shared-library API needs wrappers for:

- lifecycle: open query DB, close, check integrity, checkpoint;
- project CRUD: list/get/delete project;
- file hashes: get/upsert/delete file hashes;
- nodes: by id, qn, suffix, name, label, file, overlap;
- edges: by source, target, type;
- counts: nodes/edges, scoped counts, vector counts;
- search: structured search with filters;
- traversal: BFS with direction and edge types;
- schema: labels/types/property keys;
- architecture: languages, packages, routes, hotspots, boundaries, services,
  layers, clusters, file tree;
- ADR: get/store/delete/update sections;
- vector search: semantic query;
- utility: risk labels, glob/LIKE hints, test path detection.

Shared library should not expose all C structs directly. It should expose JSON
requests and JSON responses, plus a small raw SQL read-only escape hatch only if
absolutely needed for projection speed.

## Thepipe-Specific Interfaces To Add

Comparator API is graph-native. `thepipe` needs projection APIs:

### `emit_chunks_from_graph`

```python
emit_chunks_from_graph(project, mode="map", include_patterns=None,
                       code_n1=3, code_n2=5, token_budget=None)
```

Returns `Chunk[]` with:

- `__summary__`,
- compact per-file digests,
- optional full source for primary files,
- metadata containing graph IDs/provenance.

### `emit_code_relations`

```python
emit_code_relations(project, mode="map", version="v2")
```

Returns:

- `code-relations/v1` compatibility when requested,
- `code-relations/v2` by default.

`v2` must include:

- backend kind/version,
- schema fingerprint,
- source: `fresh`, `cache`, `mixed`,
- files,
- entities,
- edges,
- diagnostics,
- omitted/pruned records,
- parser/backend warnings.

### `access_graph`

Implemented as `CodegraphGraph` and `codegraph_action`.

```python
CodegraphGraph.open_repo(repo).find_entities(query="main", kind="Function")
CodegraphGraph.open_repo(repo).neighbors("main", direction="outbound", depth=1)
CodegraphGraph.open_repo(repo).query_sql("SELECT name FROM nodes LIMIT 20")
```

CLI/package actions:

- `summary`: project/file/entity/edge counts;
- `files`: indexed file hashes/sizes;
- `entities`: bounded local entity search;
- `edges`: bounded edge list;
- `neighbors`: bounded BFS around an entity;
- `sql`: bounded read-only `SELECT`/`WITH`/`PRAGMA`.

`neighbors` quality controls:

- short names must resolve uniquely; ambiguous names fail with qualified-name
  candidates instead of silently selecting the first row;
- `codegraph_min_confidence` defaults to `0.5` for package/CLI actions and drops
  only edges carrying a lower numeric confidence;
- `codegraph_max_transit_degree` defaults to `25` and returns high-degree hubs
  without expanding through them;
- each returned node includes its shortest `hop`;
- `filtered_edges` and `pruned_hubs` make quality reduction explicit;
- either filter accepts `null` to preserve every indexed edge or traverse every
  hub.

Motivation: native resolution deliberately preserves uncertain edges, and common
symbols such as `close`, `query`, or `Chunk` can connect unrelated regions. A
bounded local action should favor a useful neighborhood over exhaustive recall,
but it must report what it omitted. Native `trace_path` remains available when
the caller needs donor behavior without these local filters.

These actions are intentionally local SQLite reads. They make existing graph DBs
useful even when the sidecar is not running.

### `emit_mapnew_from_graph`

```python
emit_mapnew_from_graph(project, old="HEAD", new=None,
                       include_patterns=None)
```

Combines git diff, DB entity ranges, compact changed-region digest, and impact
trace.

### `compact_trace`

```python
compact_trace(trace_result, style="digest")
```

Outputs compact textual graph paths, not module digests.

### `compact_architecture`

```python
compact_architecture(architecture_result)
```

Outputs compact architecture text for LLM context.

## Missing Surfaces Not Captured Earlier

Earlier spec missed or underweighted these:

- `list_projects`: needed for master index and cross-repo discovery.
- `delete_project`: needed for cache lifecycle and disk policy.
- `index_status`: needed before agent-visible integration.
- `ingest_traces`: currently stub-like, but API surface exists and should be
  represented honestly.
- `semantic_query`: part of `search_graph`, not separate MCP tool.
- vector search/counts in store API.
- ADR persistence in SQLite, not just files.
- architecture clusters/community detection.
- file tree projection.
- graph-augmented grep via `search_code`.
- source snippet ambiguity workflow.
- query pagination/truncation semantics.
- sidecar CLI `--json` raw MCP envelope path.
- watcher exists but should not be exposed in fork until cache policy is stable.
- UI HTTP API exists but is not first-stage product surface.
- cross-repo mode is implemented through `index_repository` mode, not its own
  tool.

## Test Plan

Aggressive TDD loop:

1. Golden sidecar contracts for every MCP tool.
2. Tiny fixture repo with Python calls.
3. TSX fixture for component/function surfaces.
4. Multi-file import/call fixture.
5. Git diff fixture for `detect_changes` and `mapnew`.
6. Ambiguous symbol fixture for `get_code_snippet`.
7. DB schema fingerprint test.
8. Projection snapshots for `Chunk[]`, digest markdown, and
   `code-relations/v2`.
9. Disk policy tests for repo-local and global caches.
10. Failure tests: missing binary, bad version, corrupt DB, unknown schema,
    sidecar timeout, malformed JSON.

## Migration Roadmap

### Stage 1: Sidecar Contract

- Add backend protocol.
- Add sidecar wrapper.
- Pin binary version and checksum.
- Implement contract tests.
- No skill integration.

### Stage 2: SQL Middleware

- Add read-only DB adapters.
- Add schema fingerprinting.
- Add graph read models.
- Preserve upstream schema with adapters, not migrations.

### Stage 3: Projection

- Implement graph to `Chunk[]`.
- Implement graph to compact markdown.
- Implement `code-relations/v2`.
- Add v1 compatibility output.

### Stage 4: Disk + Discovery

- Add `.thepipe/manifest.json`.
- Add master DB.
- Add size policy.
- Add explicit shared artifact command.

### Stage 5: Full Binary Support Achieved

- Full sidecar tool surface tested. Done.
- Projections stable. Done.
- Disk policy stable. Done.
- Fallback stable. Done.
- Public `code_relations: "graph"` option schema. Done.
- Local archive/checksum install path. Done.

Only here may registration/skills mention sidecar-backed codegraph.

### Stage 6: Shared Library

- Build curated native source. Initial macOS build done.
- Use compiled grammar packs, not generated source checkout. Done for packaged
  artifacts.
- Expose same JSON contract through `ctypes`. Done through `tp_context_call`.
- Install from `codegraph_library_archive` plus SHA-256. Done.
- Keep sidecar fallback. Done.
- Add Linux/Windows PIC/link/allocator CI before making shared library the
  default.

### Stage 7: Retire Inferior Python Pieces

Replace current Python codegraph pieces one surface at a time:

- dependency graph search,
- import/call graph traversal,
- impact mapping,
- code snippet lookup,
- parser coverage for native-supported languages.

Keep:

- multimodal extraction,
- `Chunk`,
- compact distillation,
- docs/media/DB/web handling,
- Python fallback,
- SQL extraction work,
- `map/mapnn/mapnew` UX.

## Open Questions

- Which comparator release/commit is pinned first?
- Is first sidecar distributed by separate extra package or installer command?
- Which grammar pack is first native library target?
- Do we expose `query_graph` to all users or gate behind advanced flag?
- Should repo-local `.thepipe/codegraph.sqlite` be default, or comparator cache
  default with manifest pointer first?
- Do we store snippets in our overlay DB, or always read source on demand?
