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
- `Catalog`: database catalog when available.
- `Schema`: logical schema/namespace.
- `Table`: physical table.
- `View`: logical view.
- `Column`: table or view column.
- `Index`: database index.
- `Constraint`: primary key, unique key, check, or foreign key constraint.
- `Relationship`: inferred relationship when no declared foreign key exists.
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

Proposed options for database mode:

```json
{
  "database_graph": "off|snapshot|query|auto",
  "database_graph_query": "MATCH ...",
  "database_graph_refresh": "never|if_missing|if_stale|always",
  "database_graph_scope": "metadata|profiles|findings|samples",
  "database_graph_store": "repo|user|memory",
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

- `memory`: no persistence, useful for sensitive or throwaway work.
- `repo`: default for project work; git-ignored, auto-discoverable.
- `user`: cross-project registry and optional shared cache, size-managed.

Ancient detailed records should compact into durable summaries and trends.
Detailed profiles and samples should age out before schema topology and verified
findings.

## Insight Provenance And Retention

Database graph mode should remember enough past work to make future questions
faster even when the caller has no prior chat context.

Default persisted memory:

- operation records: what was crawled, queried, profiled, refreshed, omitted;
- query fingerprints and referenced tables/columns, not raw result sets;
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

7. **Bounded profiling**
   Compute portable statistics first: row count where cheap, null count, distinct
   estimate, min/max for numeric/date columns, top values for low-cardinality
   text, length ranges, and basic distribution sketches where supported.

8. **Finding synthesis**
   Derive observations from profiles and relationships: likely dimensions/facts,
   candidate keys, date grain, sparse columns, enum-like columns, broken
   referential hints, duplicate keys, and possible PII flags.

9. **Persist projection**
   Write the snapshot and graph projection according to store policy. Repo-local
   stores live under a git-ignored `.thepipe/` path by default but remain
   discoverable by the tool.

10. **Emit compact outputs**
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

## Delivery Roadmap

1. Define `DatabaseSnapshot` dataclasses and JSON schema.
2. Render current schema/preview outputs from snapshots.
3. Add read-only guardrails and mutation tests.
4. Build one-pass introspection for SQLite and DuckDB file sources.
5. Add ODBC snapshot adapter with real-driver fixture coverage.
6. Add structured-file snapshot adapters for JSON/XML/spreadsheet/archive
   topology using existing parsers before considering grammar packs.
7. Add repo-local storage, manifest, git exclude, and user registry.
8. Add compact snapshot digest and chunk projection.
9. Add a small read-only Cypher engine or embedded graph query dependency.
10. Add relationship heuristics and confidence/evidence reporting.
11. Add bounded EDA profiling and profile freshness.
12. Add operation/insight provenance with pin, revoke, purge, and renew policy.
13. Add relevant table selection driven by query intent and retained insights.
14. Add citation anchors for structured document/XML/spreadsheet sources.
15. Add cross-domain exported facts for codegraph integration.

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
- Which structured-file formats need native grammar packs versus existing Python
  parsers and DuckDB normalization.
- How pinned insights should renew when their supporting profiles are aging but
  schema fingerprints are still unchanged.
- Whether citation anchors should be global graph nodes or projection-only IDs
  generated during chunk emission.
