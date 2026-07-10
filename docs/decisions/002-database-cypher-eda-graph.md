# Database Cypher and EDA Graph Spec

Status: proposed

## Purpose

Thepipe database mode should become a strong first-contact tool for unfamiliar
databases, dataframe files, and archives. The goal is not to replace SQL or
database drivers. The goal is to build a compact, durable metadata graph that
helps an agent decide what to inspect, what to query, what has changed, and what
context should be sent to a model.

Although this starts with database mode, the same graph shape should eventually
cover structured data files that thepipe already understands: CSV, JSON, JSONL,
XML, Parquet, Arrow/Feather, spreadsheets, notebooks, archives, and unpacked
document containers such as DOCX/XLSX/PPTX internals. Ponytail rule: only add a
source once it can reuse the same snapshot and graph pipeline; do not build a
parallel graph stack per format.

Cypher is the candidate query grammar for that metadata graph because it is a
widely learned graph query language, maps naturally to schema topology, and is
more compact than large JSON relationship dumps for many graph-shaped questions.

This deliberately treats `Chunk[]` as an ephemeral interchange standard, not the
primary long-lived memory substrate. Chunks remain valuable because every
thepipe source can emit them, agents already understand them, and they are easy
to budget, stream, summarize, and discard. The persistent layer should be the
snapshot/graph middleware underneath those chunks: stable enough to reuse across
agent runs, cheap enough to refresh selectively, and structured enough to answer
relationship questions without re-crawling the source.

## Non-Goals

- Do not store raw database rows as graph nodes by default.
- Do not expose arbitrary SQL through a Cypher wrapper.
- Do not replace ODBC, DuckDB, SQLite, SQLAlchemy, or driver-level execution.
- Do not read private codegraph SQLite tables from Python.
- Do not require codegraph installation for ordinary database mode.
- Do not require graph construction before simple `schema`, `preview`, or
  explicit SQL operations can run.
- Do not route every structured file through the native codegraph parser stack
  just because grammars exist. Use native Python/DuckDB/XML/zip tools first;
  optional grammar packs are an accelerator for formats they already parse well.

## Separation of Query Languages

SQL remains the language for row-data operations:

```sql
SELECT customer_id, SUM(total)
FROM orders
GROUP BY customer_id
ORDER BY SUM(total) DESC
LIMIT 20
```

Cypher is for metadata, topology, profiling summaries, lineage, and planning:

```cypher
MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
WHERE c.name CONTAINS "customer"
RETURN t.name, collect(c.name) AS columns
LIMIT 25
```

This distinction is a product contract. A caller may use Cypher to find relevant
tables and relationships, then use SQL to inspect or aggregate actual data.

## Graph Model

Baseline node labels:

- `Source`: one database, dataframe file, archive, or connection target.
- `DatasetGroup`: explicit analysis scope spanning multiple sources.
- `Catalog`: database catalog when available.
- `Schema`: logical schema/namespace.
- `Table`: physical table.
- `View`: logical view.
- `Column`: table or view column.
- `Index`: database index.
- `Constraint`: primary key, unique key, check, or foreign key constraint.
- `Relationship`: inferred relationship when no declared foreign key exists.
- `JoinCandidate`: possible join path across tables, files, schemas, or sources.
- `Profile`: bounded EDA/profile snapshot for a source/table/column.
- `Sample`: optional, bounded, expiring sample reference.
- `Finding`: agent-readable observation, warning, trend, or anomaly.
- `Insight`: reusable conclusion produced by prior EDA or agent work, with
  provenance, freshness, confidence, and retention policy.
- `Query`: executed query shape, normalized and fingerprinted.
- `Operation`: crawl, query, profile, refresh, or analysis run that produced
  profiles, findings, insights, samples, or omissions.
- `Directive`: user-approved scratch knowledge or project-specific note.
- `CitationAnchor`: stable pointer to source content, row-free samples,
  document region, sheet range, XML path, JSON path, or chunk projection.
- `Document`: structured document container such as DOCX, XLSX, PPTX, notebook,
  XML document, or archive member set.
- `Sheet`: spreadsheet worksheet or table-like tab.
- `Field`: semi-structured object field for JSON/XML/document trees.
- `RecordShape`: inferred shape for JSONL, nested JSON arrays, XML repeated
  elements, or dataframe-like records.

Baseline edge types:

- `HAS_CATALOG`, `HAS_SCHEMA`, `HAS_TABLE`, `HAS_VIEW`, `HAS_COLUMN`.
- `HAS_INDEX`, `HAS_CONSTRAINT`.
- `PRIMARY_KEY`, `FOREIGN_KEY`, `REFERENCES`.
- `READS_FROM` for view lineage.
- `DERIVED_FROM` for dataframe/archive derived views.
- `HAS_PROFILE`, `PROFILED_COLUMN`.
- `HAS_FINDING`, `EVIDENCED_BY`.
- `HAS_INSIGHT`, `PRODUCED_BY`, `USED_AS_EVIDENCE`.
- `QUERY_READS`, `QUERY_FILTERS`, `QUERY_GROUPS`, `QUERY_JOINS`.
- `GROUPS_SOURCE`, `CROSS_SOURCE_JOIN`, `SAME_ENTITY_AS`, `MAY_JOIN_ON`.
- `HAS_OPERATION`, `REFRESHED_BY`, `PINNED`, `EXPIRES`, `REVOKED_BY`.
- `HAS_CITATION_ANCHOR`, `CITES`.
- `LIKELY_RELATED` for heuristic relationships.
- `SUPERSEDES` for snapshot/version lineage.
- `HAS_DIRECTIVE`.
- `HAS_DOCUMENT`, `HAS_SHEET`, `HAS_FIELD`, `HAS_RECORD_SHAPE`.
- `CONTAINS_NODE` for nested XML/JSON/document structure.
- `UNPACKED_FROM` for archive and office-document container lineage.
- `NORMALIZED_AS` for projections into DuckDB views/dataframes.

Raw row values should appear only as short-lived sample payloads or redacted
profile examples under an explicit retention policy.

Multi-source analysis is first-class. A `DatasetGroup` can connect multiple
databases, file-backed datasets, spreadsheets, and document-derived tables that
participate in one investigation. Cross-source edges must carry evidence and
confidence because matching `customer_id` in two systems is a hypothesis until
validated by type, cardinality, overlap, or user directive.

## Minimal Snapshot ABI

The graph is built from a versioned snapshot, not directly from ad hoc markdown.

```json
{
  "schema_version": "database-snapshot/v1",
  "source": {
    "source_id": "sha256:...",
    "kind": "odbc|sqlite|duckdb|postgres|mysql|file",
    "display_name": "warehouse-prod",
    "connection_fingerprint": "sha256:...",
    "read_only": true
  },
  "freshness": {
    "status": "fresh|aging|stale|partial",
    "captured_at": "2026-07-08T00:00:00Z",
    "expires_at": "2026-07-15T00:00:00Z",
    "schema_fingerprint": "sha256:...",
    "profile_fingerprint": "sha256:..."
  },
  "retention": {
    "policy": "memory|repo|user|pinned",
    "purge_after": "2026-08-08T00:00:00Z",
    "renewable": true
  },
  "objects": {
    "tables": [],
    "views": [],
    "columns": [],
    "indexes": [],
    "constraints": [],
    "relationships": []
  },
  "profiles": [],
  "findings": [],
  "insights": [],
  "operations": [],
  "citation_anchors": [],
  "diagnostics": [],
  "omissions": []
}
```

All model-facing formats should be projections of this ABI:

- Markdown schema report.
- Compact digest.
- `Chunk[]`.
- JSON snapshot.
- Cypher graph.
- Query-planning context.

The direction of dependency matters:

```text
database/source -> snapshot ABI -> persistent graph -> compact projections -> Chunk[]
```

`Chunk[]` should not be the canonical persisted database memory. Persisting
chunks alone would preserve text but lose too much executable structure:
freshness, object IDs, relationship confidence, profile provenance, omission
records, retention policy, and graph queryability. Chunks should be regenerated
from the graph/snapshot whenever possible.

## Structured Data Graph Intake

Use the same graph middleware for sources that are not SQL databases but have
stable internal structure.

The intent differs by source family. Database/dataframe graph mode is mainly
for analysis provenance, reusable insights, topology, and refresh. Document/XML
graph mode is mainly for structure-aware content storage and retrieval: layout,
regions, paths, anchors, and logical units that make later RAG/citation systems
easier to feed. Thepipe should prepare those graph-ready units, not become the
RAG engine itself.

Initial source families:

- relational databases through current database adapters;
- DuckDB-readable data files: CSV, JSON, JSONL, Parquet, ORC, Arrow/Feather;
- spreadsheets: workbook, sheet, header row, table/range, column;
- XML: document, repeated element shapes, attributes, text-bearing fields;
- JSON: object paths, arrays, repeated record shapes, scalar fields;
- ZIP/archive and Office containers: member graph plus selected parsed members;
- notebooks: cells, outputs, referenced data files, dataframe-looking outputs.

Minimal extraction strategy:

1. Prefer existing thepipe parsers and stdlib/container formats.
2. Normalize table-like sources to `Source -> Table/View -> Column`.
3. Normalize nested sources to `Document -> RecordShape/Field`.
4. Add `NORMALIZED_AS` edges when a nested/file source is made queryable through
   DuckDB `source_data` or a dataframe.
5. Persist only topology, profiles, findings, and omissions by default.

For document-like sources, persist citation-ready anchors and structure first;
content payloads should remain chunk projections or separately retained excerpts
with explicit policy. Examples: DOCX paragraph/run/table anchors, sheet/range
anchors, XML paths, JSON paths, archive member paths, slide placeholders, and
notebook cell IDs.

Native grammar packs from codegraph may help for JSON, XML, YAML, TOML, and
similar formats, but they should remain optional. The default implementation can
get a long way with `json`, `xml.etree.ElementTree`, `zipfile`, DuckDB, pandas,
and existing thepipe extractors.

Useful Cypher examples:

```cypher
MATCH (d:Document)-[:HAS_FIELD]->(f:Field)
WHERE f.path CONTAINS "customer"
RETURN d.path, f.path, f.type
LIMIT 25
```

```cypher
MATCH (s:Sheet)-[:HAS_COLUMN]->(c:Column)
RETURN s.name, count(c) AS columns
ORDER BY columns DESC
LIMIT 20
```

```cypher
MATCH (src:Source)-[:NORMALIZED_AS]->(t:Table)-[:HAS_PROFILE]->(p:Profile)
RETURN src.path, t.name, p.freshness
LIMIT 50
```

## Cypher Interface

EDA should use the graph during the run, not only after it. Even when no durable
store is written, the active session should maintain a Cypher-queryable
operation graph so planning can ask "did we already inspect this table shape?",
"which query established this insight?", and "what source can join to this
one?" before issuing another SQL call.

Proposed options for database mode:

```json
{
  "database_graph": "off|snapshot|query|auto",
  "database_graph_query": "MATCH ...",
  "database_graph_refresh": "never|if_missing|if_stale|always",
  "database_graph_scope": "metadata|profiles|findings|samples",
  "database_graph_store": "repo|user|memory",
  "database_graph_persist": true,
  "database_graph_git_exclude": true
}
```

Proposed Python surface:

```python
snapshot_database(source, options) -> DatabaseSnapshot
build_database_graph(snapshot, store) -> DatabaseGraphBuildResult
query_database_graph(source_or_store, cypher, options) -> DatabaseGraphQueryResult
emit_database_digest(snapshot_or_graph, budget) -> list[Chunk]
```

Proposed CLI shape:

```bash
thepipe odbc://?... --options '{"database_graph":"snapshot"}'
thepipe data.duckdb --options '{"database_graph":"query","database_graph_query":"MATCH (t:Table) RETURN t.name LIMIT 20"}'
```

Initial Cypher support can be read-only:

- `MATCH`
- labels
- relationship directions
- `WHERE`
- `RETURN`
- `ORDER BY`
- `LIMIT`
- simple aggregation such as `count()` and `collect()`

Writes should be blocked until retention, trust, and provenance policies are
implemented.

### Current Implementation Contract

The first shippable version should not pretend to be a full graph database. It
should expose a deliberately small, tested, Cypher-shaped read surface over the
database graph ledger and make the upgrade path to native/shared-library Cypher
obvious.

Current branch scope:

- persist source topology, query operations, join candidates, structured-source
  anchors, record shapes, and pinned insights;
- default to repo-local persisted storage, with explicit memory/no-persist
  bypass;
- auto-exclude `.thepipe/database/` from Git while keeping it discoverable by
  thepipe;
- reuse fresh operation fingerprints before repeating identical discovery SQL;
- expose `mode="graph"` so callers can query graph state without opening a live
  database connection;
- support JSON/XML/ZIP-like structured source anchors without raw content
  persistence;
- return graph query results as ordinary `Chunk` projections so existing thepipe
  callers are not forced onto a new output primitive.

The current query layer should be treated as `Cypher Level 0`: enough syntax to
cover real planning queries, not enough to claim general Cypher compatibility.
It must parse and test the following forms before feature-ship:

```cypher
MATCH (op:Operation) RETURN op LIMIT 20
MATCH (i:Insight) WHERE i.pinned = true RETURN i.summary, i.confidence
MATCH (s:Source)-[:HAS_TABLE]->(t:Table) RETURN s.source_id, t.name
MATCH (a:Column)-[j:CROSS_SOURCE_JOIN]->(b:Column) RETURN a, j, b LIMIT 25
MATCH (r:RecordShape) WHERE r.path CONTAINS "customer" RETURN r.path, r.fields
```

Anything outside the supported subset should fail with a clear unsupported-query
diagnostic. Silent broad matching is worse than refusing the query because it
can cause an agent to trust incomplete topology.

`Cypher Level 1` should add enough of the graph grammar for ordinary EDA
planning:

- multiple labels only where we can evaluate them deterministically;
- `WHERE` predicates for equality, inequality, boolean literals, numeric
  comparisons, `CONTAINS`, `STARTS WITH`, and `ENDS WITH`;
- `RETURN` of aliases, properties, and simple maps;
- `ORDER BY`, `SKIP`, and `LIMIT`;
- `count()` and `collect()` on one grouping key;
- relationship direction and relationship type filtering;
- stable errors for unsupported path-length, write clauses, subqueries, and
  procedure calls.

`Cypher Level 2` is the migration target. At that point the ledger should be
projected into a real graph backend or native shared-library query engine
through an explicit graph ABI. Thepipe should still own database snapshots,
privacy policy, source fingerprints, retention, and chunk/digest projections;
the native engine should own graph indexing and query execution.

### Graph ABI For Native Cypher

The migration seam is a property-graph projection, not private SQLite access.
The ABI should be simple enough to generate from JSON, SQLite, or in-memory
snapshots and simple enough for a C shared library to consume without knowing
database driver details.

Conceptual input:

```json
{
  "schema_version": "thepipe-property-graph/v1",
  "graph_id": "sha256:...",
  "source": "database-graph-ledger/v1",
  "nodes": [
    {
      "id": "source:warehouse",
      "labels": ["Source"],
      "properties": {"kind": "odbc", "display_name": "warehouse"}
    }
  ],
  "edges": [
    {
      "id": "source:warehouse->table:orders",
      "type": "HAS_TABLE",
      "from": "source:warehouse",
      "to": "table:orders",
      "properties": {"confidence": 1.0}
    }
  ]
}
```

Required native/shared-library calls:

```text
tp_graph_open(config_json) -> handle
tp_graph_upsert(handle, property_graph_json) -> result_json
tp_graph_query(handle, cypher_json) -> result_json
tp_graph_compact(handle, policy_json) -> result_json
tp_graph_close(handle) -> void
```

`cypher_json` should carry the query, parameter map, read-only flag, row limit,
timeout, and supported grammar level. This lets Python enforce policy before C
runs the query and lets C report exact grammar support back to Python.

The shared-library path should be promoted only when it beats the Python ledger
on at least one measured axis without losing policy behavior:

- faster multi-hop graph reads on large ledgers;
- lower memory for repeated query sessions;
- better Cypher coverage;
- stable cross-platform wheels or prebuilt libraries;
- no requirement to download raw grammar/build clutter during normal install.

The full binary remains a fallback and compatibility harness, not the preferred
long-term database graph runtime.

### Why Not Directly Use Codegraph Tables

Database graph mode should not read or write private codegraph storage. That
would couple database EDA to a code-analysis implementation detail, make
upgrades brittle, and blur privacy boundaries between source code metadata and
database/source metadata.

The acceptable integration shapes are:

- export database graph facts into a generic property graph ABI;
- export codegraph facts into the same ABI;
- query a combined read-only projection;
- keep each domain's canonical store independent;
- record cross-domain edges as exchanged facts, such as code entity `USES_TABLE`
  database table, migration `ALTERS_TABLE`, route `READS_TABLE`, or test
  `COVERS_QUERY`.

This preserves thepipe's database-specific policies while still allowing a
future agent to ask cross-domain questions with one Cypher-like grammar.

## Persistent Middleware Contract

The database graph can become middleware if it obeys a stricter contract than a
one-shot extraction output:

- stable object identity across runs;
- explicit source and credential-safe fingerprints;
- schema/profile freshness tracked separately;
- partial refresh of changed catalogs, schemas, tables, or profiles;
- durable diagnostics and omissions;
- confidence/evidence on inferred facts;
- bounded retention and compaction;
- deterministic projections into chunks, markdown, compact digest, and JSON.

Middleware does not mean one global mega-database by default. The default should
be repo-local or source-local metadata under `.thepipe/database/`, discoverable
by the tool but ignored by Git unless explicitly opted in. A small user-level
registry can bridge these deployments so an agent can discover "this repo has a
database graph here" without copying the graph into a master store.

The graph should support three durability tiers:

- `memory`: no persistence, useful for sensitive, throwaway, or explicit
  bypass-storage work.
- `repo`: default for project work; git-ignored, auto-discoverable.
- `user`: cross-project registry and optional shared cache, size-managed.

Persistent storage is the default because it is what makes savings, insights,
pins, and provenance survive into later runs. Storage can still be bypassed by
explicit opt-out. In bypass mode, graph-shaped planning, operation tracking, and
Cypher queries run against an in-memory graph for a single EDA session, avoiding
durable writes while still preventing repeated SQL calls within the active run.

Ancient detailed records should compact into durable summaries and trends.
Detailed profiles and samples should age out before schema topology and verified
findings.

## Insight Provenance And Retention

Database graph mode should remember enough past work to make future questions
faster even when the caller has no prior chat context.

Default persisted memory:

- operation records: what was crawled, queried, profiled, refreshed, omitted;
- query fingerprints and referenced tables/columns, not raw result sets;
- reusable discovery-query fingerprints so EDA does not repeat SQL calls when
  source/profile freshness still satisfies policy;
- bounded profile summaries and stale/fresh status;
- findings and insights with confidence, evidence links, and produced-by edges;
- citation anchors or sample references only when policy permits them.

Retention controls:

- unpinned operation detail can be purged by TTL or size budget;
- pinned insights survive purge until explicitly revoked;
- pinned insights may optionally renew their freshness when related metadata or
  profiles refresh cleanly;
- purged evidence should leave an omission/provenance stub so future agents know
  an insight existed but cannot inspect the full discovery trail;
- personal mode may retain more detail, but default mode should favor topology,
  summaries, and provenance over raw data.

Cypher should make both current topology and past reasoning queryable:

```cypher
MATCH (i:Insight)-[:USED_AS_EVIDENCE]->(f:Finding)<-[:HAS_FINDING]-(t:Table)
WHERE i.pinned = true
RETURN i.summary, t.name, i.freshness, i.confidence
LIMIT 20
```

```cypher
MATCH (op:Operation)-[:QUERY_READS]->(t:Table)
WHERE op.kind = "profile" AND op.status = "partial"
RETURN op.started_at, t.name, op.omission_reason
LIMIT 50
```

```cypher
MATCH (g:DatasetGroup)-[:GROUPS_SOURCE]->(s:Source)-[:HAS_TABLE|HAS_SCHEMA*1..2]->(t:Table)
WHERE g.name = "quarterly_revenue_investigation"
RETURN s.display_name, t.name
LIMIT 50
```

```cypher
MATCH (a:Column)-[j:CROSS_SOURCE_JOIN]->(b:Column)
WHERE j.confidence >= 0.8
RETURN a.qualified_name, b.qualified_name, j.evidence
LIMIT 25
```

## Useful Cypher Patterns

Find candidate join paths:

```cypher
MATCH p=(a:Table)-[:HAS_COLUMN]->(:Column)-[:REFERENCES]->(:Column)<-[:HAS_COLUMN]-(b:Table)
RETURN p
LIMIT 20
```

Find wide tables before profiling:

```cypher
MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
RETURN t.name, count(c) AS columns
ORDER BY columns DESC
LIMIT 20
```

Find stale profile summaries:

```cypher
MATCH (t:Table)-[:HAS_PROFILE]->(p:Profile)
WHERE p.freshness <> "fresh"
RETURN t.name, p.captured_at, p.reason
LIMIT 50
```

Find tables with no declared or inferred relationships:

```cypher
MATCH (t:Table)
WHERE NOT (t)-[:HAS_COLUMN]->(:Column)-[:REFERENCES|LIKELY_RELATED]-(:Column)
RETURN t.name
LIMIT 50
```

Select compact model context for a business term:

```cypher
MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
WHERE t.name CONTAINS "invoice" OR c.name CONTAINS "invoice"
OPTIONAL MATCH (t)-[:HAS_PROFILE]->(p:Profile)
RETURN t, collect(c), collect(p)
LIMIT 10
```

## EDA Graph Build Pipeline

1. **Identify source**
   Create a stable `source_id` without storing credentials. Include driver name,
   server/database/catalog where safe, file inode/hash where local, and explicit
   user alias when supplied.

2. **Enforce read policy**
   Default to read-only. For SQL backends, block writes at both generated-query
   and execution-adapter layers. For ODBC, prefer driver/session read-only modes
   when available and still validate SQL text defensively.

3. **Catalog crawl**
   Collect catalogs, schemas, tables, views, columns, indexes, constraints, row
   estimates, and permissions when exposed by the driver. Record unsupported
   metadata as diagnostics instead of failing the whole crawl.

4. **Fingerprint**
   Produce a schema fingerprint from stable object metadata. Produce profile
   fingerprints separately so a table can have fresh schema and aging statistics.

5. **Relationship discovery**
   Prefer declared foreign keys. Add heuristic `LIKELY_RELATED` edges only with
   confidence and evidence fields, such as matching names, compatible types,
   uniqueness, cardinality checks, or value-overlap samples.

6. **Profile planning**
   Decide what to profile after seeing table count, width, row estimates, index
   availability, remote/local cost, and user query intent. Broad metadata comes
   before expensive profiling.

7. **Operation lookup and recording**
   Before running a discovery query, check whether a prior `Operation` with the
   same source fingerprint, normalized query/profile shape, and freshness policy
   already produced usable metadata. If reused, emit a new lightweight operation
   that points to the earlier evidence instead of repeating the SQL call. If run,
   record the normalized SQL/query intent, touched objects, runtime, row/sample
   limits, omissions, and result fingerprint.

8. **Bounded profiling**
   Compute portable statistics first: row count where cheap, null count, distinct
   estimate, min/max for numeric/date columns, top values for low-cardinality
   text, length ranges, and basic distribution sketches where supported.

9. **Finding synthesis**
   Derive observations from profiles and relationships: likely dimensions/facts,
   candidate keys, date grain, sparse columns, enum-like columns, broken
   referential hints, duplicate keys, and possible PII flags.

10. **Persist or bypass projection**
    Write the snapshot and graph projection according to store policy. Repo-local
    stores live under a git-ignored `.thepipe/` path by default but remain
    discoverable by the tool. Storage is the default. If the caller explicitly
    chooses store policy `memory` or `database_graph_persist=false`, keep the
    graph-shaped operation ledger in-process and emit projections without
    durable storage.

11. **Emit compact outputs**
    Do not dump the whole graph unless explicitly requested. Default outputs
    should include source summary, important tables, selected relationships,
    diagnostics, freshness state, and the next useful queries.

## Robustness Requirements

### Freshness

Read operations should not refresh by default when a valid snapshot exists.
Refresh policy should be explicit:

- `never`: only read existing metadata.
- `if_missing`: build only when no snapshot exists.
- `if_stale`: rebuild when TTL or fingerprint checks fail.
- `always`: force a crawl.

Skills can later add behavioral rules such as "refresh before risky refactors"
or "refresh after schema migration files changed." Do not wire skill behavior
until the database graph API is stable.

### Storage

Default repo-local layout:

```text
.thepipe/
  database/
    manifest.json
    snapshots/
    graph/
    profiles/
    insights/
    operations/
    samples/
    tmp/
```

Git policy:

- add `.thepipe/database/` to `.git/info/exclude` by default;
- never edit tracked `.gitignore` unless the user asks;
- allow explicit committed snapshots for benchmark/demo repositories;
- keep a user-level registry that records known repo-local deployments without
  copying their contents.

Retention policy:

- metadata can live longest;
- profiles expire sooner than schema;
- raw samples are off by default and expire fastest;
- findings and operations may be compacted into durable insights;
- pinned insights are permanent until revoked or explicitly unpinned;
- old detailed records can be squashed into trend snapshots.

### Privacy and Security

- Redact credentials from logs, manifests, source IDs, and diagnostics.
- Treat table/column names as potentially sensitive.
- Never persist raw rows unless a policy explicitly permits it.
- Mark heuristic edges and findings as hypotheses.
- Mark generated insights with evidence, operation provenance, and confidence.
- Record who/what created a directive or finding when available.
- Allow pinned insights to be revoked without deleting the whole graph.
- Support a "no persist" mode for regulated or temporary work.

### Driver Diversity

The graph builder must tolerate uneven metadata support:

- SQLite: PRAGMA-driven metadata and limited constraints.
- DuckDB/file sources: inferred tables/views and file-derived lineage.
- ODBC: metadata APIs first, dialect fallbacks second.
- PostgreSQL/MySQL/MSSQL: information schema plus dialect-specific extensions.
- Dataframes/archives: synthetic source/table/column nodes.
- JSON/XML/spreadsheets/notebooks: synthetic document, sheet, record-shape, and
  field nodes, with table projections only when a stable tabular view exists.

Every adapter should return partial snapshots with diagnostics rather than
claiming unsupported data is absent.

### Cost Control

- Use row estimates before row counts when exact counts are expensive.
- Prefer database-side aggregation over pulling data into Python.
- Cap table count, column count, profile count, sample bytes, query runtime, and
  total persisted bytes.
- Run expensive profiles only on selected tables after intent/scoping.
- Keep per-source accounting and LRU cleanup.
- Make large-source omissions first-class output.

### Determinism

- Sort objects and edges in stable order.
- Use stable IDs derived from source ID plus qualified object names.
- Include schema and adapter versions in snapshots.
- Make equivalent metadata produce equivalent compact output where possible.
- Keep confidence, evidence, and omissions explicit.

## Integration With Codegraph

Codegraph remains the code metadata engine. Database graph mode should not write
into private codegraph tables or depend on codegraph installation.

Future integration should happen through stable exchanged facts:

- application code entity uses table/view/column;
- migration file creates/alters/drops table;
- API route reads/writes a table;
- database snapshot contains table/column;
- EDA finding references application behavior.

Cross-domain Cypher may become useful later, but only after both sides expose
stable graph export/import or a shared action ABI. Until then, keep database
graph storage separate and join at the output/planning layer.

## Test Plan

Unit fixtures:

- SQLite database with primary keys, foreign keys, indexes, views.
- DuckDB file-source fixtures for CSV, JSONL, Parquet.
- JSON, XML, XLSX, DOCX-internal XML, ZIP archive, and notebook shape fixtures.
- ODBC SQLite fixture using a real driver when available.
- Mock ODBC metadata rows for catalog/schema edge cases.
- Multi-source fixture with two databases or one database plus one file-backed
  dataset, including at least one cross-source join candidate.
- Wide table, empty table, weird identifiers, reserved words.
- Missing metadata permissions and partial driver support.

Golden outputs:

- snapshot JSON stability;
- compact digest stability;
- Cypher query result stability;
- markdown projection stability;
- diagnostics for unsupported metadata.

Behavior tests:

- read actions reuse existing snapshots by default;
- `if_stale` refreshes only when TTL/fingerprint requires it;
- repo-local graph store is git-ignored but auto-discoverable;
- raw samples are not persisted in default policy;
- unpinned operation detail can be purged while pinned insights remain;
- repeated discovery calls reuse fresh operation metadata instead of repeating
  SQL, unless forced by refresh policy;
- revoked pinned insights no longer appear in default planning context;
- write SQL is rejected under read-only policy;
- heuristic relationships carry confidence and evidence;
- large-source budgets produce omissions, not crashes.
- structured files can emit graph topology without persisting raw values.

Integration tests:

- live SQLite;
- live DuckDB file-backed sources;
- optional live ODBC SQLite;
- optional PostgreSQL/MySQL/MSSQL containers;
- optional remote ODBC DSN-less connections in developer-only environments.

### Conformance Matrix

Every graph feature should have three test levels unless the source family makes
one impossible:

- unit: pure Python fixture, no driver or native dependency;
- integration: real local source such as SQLite, DuckDB, JSON, XML, ZIP, or an
  available ODBC driver;
- backend conformance: same graph query against Python ledger and native backend
  when native/shared-library support is installed.

Mandatory query conformance cases:

- operation query by kind/status/fingerprint;
- source-to-table-to-column traversal;
- cross-source join candidate traversal with confidence/evidence;
- pinned insight lookup and revoked insight omission;
- record-shape and citation-anchor lookup for structured sources;
- unsupported Cypher diagnostic;
- `LIMIT` behavior;
- no-persist mode leaves no repo graph files;
- persisted mode remains discoverable after a fresh process starts.

Mandatory EDA/source conformance cases:

- SQLite schema with primary key, foreign key, index, view, empty table, and
  weird identifiers;
- ODBC path using a real local driver when present, otherwise a driver-shaped
  fake that exercises metadata/result handling;
- DuckDB-readable CSV/JSON/Parquet where dependencies are present, otherwise
  skip with explicit reason;
- multi-source analysis where two sources share a likely join key;
- repeated query operation where the second run reuses the fresh graph record;
- stale/forced refresh where the second run does not reuse the graph record;
- large-table budget fixture that records omissions instead of failing;
- structured JSON/XML/ZIP source that produces anchors and record shapes without
  storing raw content.

Performance and footprint checks should be recorded as test output or benchmark
artifacts, not as claims in docs without measurement:

- cold graph build time;
- warm graph query time;
- repeated-operation skip time;
- graph ledger size after one run, ten runs, and after compaction;
- emitted compact chunk token estimate;
- raw snapshot JSON size;
- native/shared-library binary size when installed;
- install path size with prebuilt artifacts versus source-build fallback.

### Feature-Ship Definition

This feature is not ready to ship just because a graph file exists. It is ready
when thepipe can complete this loop reliably:

1. open a database or structured source under read-only/default policy;
2. capture topology and bounded profiles with diagnostics for omissions;
3. persist or bypass storage according to explicit policy;
4. query current and prior graph state with the supported Cypher subset;
5. avoid repeating fresh discovery work;
6. pin, revoke, purge, and preserve insight provenance correctly;
7. emit compact chunks/digests that are smaller than raw graph JSON while still
   preserving the relationships needed for planning;
8. recover from missing drivers, missing native graph runtime, partial metadata,
   stale snapshots, and unsupported Cypher with explicit diagnostics;
9. run the conformance matrix in CI with optional skips only for genuinely
   unavailable external drivers or native artifacts.

Native/shared-library support is a performance and capability upgrade, not the
definition of feature-ship for database graph mode. The Python path must remain
complete enough for correctness and fallback. The native path becomes the
default only after it passes the same conformance tests and proves it can reduce
query latency, memory, or Cypher implementation burden without increasing normal
install footprint.

## Delivery Roadmap

### Phase A: Shippable Python Graph Middleware

Goal: make database graph mode useful without native codegraph, extra graph
dependencies, or a new install burden.

Required work:

1. Define `DatabaseSnapshot` dataclasses and JSON schema.
2. Render current schema/preview outputs from snapshots.
3. Add read-only guardrails and mutation tests.
4. Build one-pass introspection for SQLite and DuckDB file sources.
5. Add ODBC snapshot adapter with real-driver fixture coverage.
6. Add structured-file snapshot adapters for JSON/XML/spreadsheet/archive
   topology using existing parsers before considering grammar packs.
7. Add repo-local storage, manifest, git exclude, and user registry.
8. Add compact snapshot digest and chunk projection.
9. Add `Cypher Level 0` over the ledger property graph.
10. Add relationship heuristics and confidence/evidence reporting.
11. Add bounded EDA profiling and profile freshness.
12. Add operation/insight provenance with pin, revoke, purge, and renew policy.
13. Add operation reuse for repeated EDA discovery queries.
14. Add multi-source `DatasetGroup` and cross-source join candidate discovery.
15. Add relevant table selection driven by query intent and retained insights.
16. Add citation anchors for structured document/XML/spreadsheet sources.

Exit criteria:

- all existing database mode contracts still pass;
- `mode="graph"` can answer topology, operation, insight, join, anchor, and
  record-shape queries without opening a live connection;
- repeated discovery SQL can be skipped from a fresh operation fingerprint;
- repo-local graph state is ignored by Git but auto-discoverable;
- memory/no-persist mode writes no files;
- output can be projected as compact chunks, JSON rows, and stable test goldens;
- unsupported Cypher emits explicit diagnostics.

### Phase B: Native-Compatible Property Graph ABI

Goal: make the Python ledger and native/shared-library graph engine speak the
same facts.

Required work:

1. Add `to_property_graph()` projection from snapshots and ledgers.
2. Add stable node and edge IDs for every supported graph object.
3. Add import/export golden tests for property graph JSON.
4. Add a native adapter interface that can upsert/query a property graph without
   knowing database connection details.
5. Add conformance tests that run the same Cypher fixtures against Python and
   native backends when native is available.
6. Add compaction policy exchange so native storage can purge or summarize old
   facts under the same retention rules as Python.
7. Add benchmark fixtures for small repo-local graphs, medium database graphs,
   and large multi-source graphs.

Exit criteria:

- Python and native backends return equivalent rows for the supported Cypher
  subset;
- native query execution is optional and auto-detected;
- installation can use prebuilt artifacts without pulling raw grammar source;
- fallback to Python remains correct when native is absent;
- native failures degrade to explicit diagnostics, not silent empty results.

### Phase C: Shared-Library Default

Goal: make shared-library graph execution the default when installed, while
Python remains the policy owner and fallback.

Required work:

1. Ship prebuilt shared libraries for the supported platform matrix.
2. Keep raw grammar/build clutter out of normal installs.
3. Build from source only as an explicit fallback path.
4. Add library version negotiation and ABI capability reporting.
5. Run graph conformance tests across Python, shared-library, and full-binary
   fallback backends.
6. Add size and cold-start regression checks.
7. Retire duplicated Python query paths only after conformance and fallback are
   stable.

Exit criteria:

- shared library supports the full database graph query contract;
- full binary is no longer required for database graph features;
- Python fallback remains available for pure-Python installs;
- branch can delete any replaced code instead of carrying three divergent
  implementations indefinitely.

### Phase D: Cross-Domain Graph Facts

Goal: let database graph and codegraph cooperate without merging their private
stores.

Required work:

1. Export codegraph facts through the property graph ABI.
2. Export database graph facts through the same ABI.
3. Define cross-domain edge types: `USES_TABLE`, `WRITES_TABLE`,
   `ALTERS_TABLE`, `READS_COLUMN`, `TESTS_QUERY`, `OWNS_MIGRATION`.
4. Add query fixtures that answer code/database questions from a combined
   read-only projection.
5. Keep source-code refresh and database refresh policies separate.

Exit criteria:

- an agent can ask code/database topology questions with one graph query;
- database privacy policy still controls database-derived facts;
- codegraph remains optional for database-only mode.

The implementation should stay incremental. Each phase must delete duplicated
database formatting or repeated crawl logic where possible, not add another
parallel path.

## Open Questions

- Whether to implement a small Cypher subset ourselves, embed a graph database,
  or reuse the native codegraph Cypher engine through a generic graph ABI.
- Whether repo-local database graph storage should be SQLite, normalized JSONL,
  or both.
- What summary statistics are portable enough for the baseline contract.
- How to represent approximate statistics across engines.
- How to identify sources when credentials rotate or replicas move.
- How much PII detection belongs in first-contact profiling.
- Whether optional committed snapshots should require redaction manifests.
- How cross-domain code/database graph joins should be queried once both exist.
- Whether multi-source joins should execute through one engine, staged temp
  tables, or agent-planned per-source SQL plus local reconciliation.
- Which structured-file formats need native grammar packs versus existing Python
  parsers and DuckDB normalization.
- How pinned insights should renew when their supporting profiles are aging but
  schema fingerprints are still unchanged.
- Whether citation anchors should be global graph nodes or projection-only IDs
  generated during chunk emission.
