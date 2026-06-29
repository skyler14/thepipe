# ADR-001: Agentic Database First-Contact Architecture

## Status

Proposed. Documentation only. No implementation is authorized by this ADR.

## Date

2026-06-28

## Ownership and Branch Policy

This document on `main` is the authoritative design record. Database, ODBC,
codegraph, and extraction branches should merge `main` and reference this ADR
instead of carrying branch-specific copies or inline implementation TODOs.

The roadmap intentionally crosses several future workstreams, but belongs to none
of them individually:

- database execution and connection adapters;
- ODBC and file-backed source support;
- EDA and agent reasoning;
- compact digest and `Chunk` output;
- optional metadata graph projection;
- cache, retention, and scratch-knowledge policy.

## Product Intent

Thepipe should be a credible first tool used against an unfamiliar database,
dataframe, archive, or file-backed dataset.

A caller should be able to provide a source with little prior knowledge. Thepipe
should safely determine what it is, establish a read-only view, map its shape,
produce a compact digest, and help an agent choose useful next work. Users should
not need to know table names, storage engine details, or optimal profiling
queries before the first call.

This does not mean running every possible statistic immediately. First contact
should proceed in stages, retaining enough high-level knowledge to make later
calls faster and more focused.

## Historical Context: JupySQL and Ploomber

JupySQL was originally selected partly because the project was considering
Ploomber-based deployment of specialized data solutions. That direction made
notebook-oriented SQL execution, Ploomber integration, and reusable deployed
pipelines strategically related.

Agentic reasoning changed that assumption. Agents can now inspect, plan, execute,
revise, and package specialized workflows without requiring Ploomber to be the
central runtime abstraction. Thepipe currently uses JupySQL mostly as a wrapper
around ordinary database execution rather than for notebook magic or Ploomber
deployment.

Proposed future direction:

```text
PostgreSQL / MySQL / SQLite / MSSQL -> SQLAlchemy
ODBC                                -> pyodbc or SQLAlchemy ODBC
DuckDB and file-backed data         -> native DuckDB API
```

This is a substitution of execution plumbing, not a reduction in database
support. It should occur only after contract tests cover existing formats,
drivers, parameters, transactions, outputs, and failure behavior.

Why consider it:

- direct parameter binding and transaction control;
- SQLAlchemy Inspector for relational metadata;
- no notebook-global connection manager;
- no runtime package installation;
- fewer layers between thepipe and database drivers;
- native DuckDB handling for Parquet, CSV, JSON, Arrow, and related sources.

Why remain cautious:

- JupySQL may hide useful driver normalization;
- existing output behavior must remain stable;
- ODBC and less-common dialects need explicit test fixtures;
- replacement should reduce code and dependencies, not create a larger adapter
  framework.

## Safety Decision: Read-Only by Default

All first-contact, preview, schema, EDA, and LLM-generated database operations
should default to read-only.

Read-only means defense in depth:

1. Use read-only connections, sessions, or transactions where supported.
2. Reject mutation, DDL, and multi-statement generated SQL.
3. Apply query time, row, and result-size limits.
4. Redact connection credentials from logs and errors.
5. Require explicit user intent to enable writes.

Prompt instructions and SQL keyword checks are not sufficient security controls.

Explicit user-authored mutation can remain a supported capability, but it must be
separate from first-contact and agent-generated analysis. Personal workflows may
loosen policy deliberately; they should never inherit writable autocommit merely
because an LLM produced plausible SQL.

## First-Contact Pipeline

### Phase 0: Identify and Open

- Detect database, dataframe, archive, or file-backed source.
- Respect explicit source-type overrides.
- Open read-only when possible.
- Record source identity without storing credentials.
- Determine dialect and available metadata mechanisms.

This phase should be cheap and should fail with actionable dependency or driver
guidance.

### Phase 1: Structural Peek

Collect shape before deciding profiling strategy:

- catalogs and schemas;
- tables and views;
- columns and data types;
- primary and foreign keys;
- indexes and view dependencies;
- row estimates or cheap counts where available;
- partition/file layout;
- source size and modification indicators.

The structural peek determines whether later work should batch, sample, stream,
or narrow scope. Batching and aggressive budgets should therefore be selected
after this peek, not imposed blindly before source shape is known.

### Phase 2: Whole-Source Digest

Produce a compact digest of the entire source:

- database-level shape;
- table-level purpose hints;
- approximate sizes;
- key relationships;
- likely fact, dimension, lookup, event, and log tables;
- high-level null/cardinality/type observations;
- obvious date, identifier, measure, category, and text columns;
- warnings, unsupported surfaces, and omitted work.

Goal: meaningful model context, not exhaustive raw output. Large tables should
still receive significant digesting, but through samples, pushdown aggregates,
approximate statistics, partitions, and bounded scans rather than complete row
materialization.

### Phase 3: Relevant-Scope Selection

Select tables and columns using:

- explicit caller selection;
- SQL references;
- natural-language intent;
- names and semantic hints;
- foreign-key and view-dependency paths;
- prior scratch directives;
- prior observed trends and successful query history.

The result is a focused working set. Unrelated first tables should not be
profiled merely because catalog order placed them first.

### Phase 4: Focused EDA

Run deeper profiling against the selected working set:

- distributions and cardinalities;
- missingness;
- numeric ranges and outliers;
- time coverage and change rates;
- category concentration;
- join viability;
- representative samples;
- data-quality warnings.

Profiling depth should adapt to source size, dialect capabilities, user policy,
and question. Significant digesting remains the objective. Budgets are the
mechanism for controlling massive or expensive sources, not a reason to make
ordinary first contact shallow.

### Phase 5: Agent Loop

Agent receives:

- structural snapshot;
- compact digest;
- relevant metadata subgraph;
- bounded EDA findings;
- freshness and omission diagnostics;
- scratch directives and prior trends;
- tools for additional read-only queries.

Agent can request narrower profiling, refresh stale facts, generate SQL, compare
periods, or produce a specialized output.

## Structured Snapshot

Create one versioned internal `DatabaseSnapshot` before rendering prose.

Conceptual contents:

```text
source
  identity, kind, dialect, capabilities, freshness
catalogs/schemas
tables/views
  names, kinds, estimates, partitions, dependencies
columns
  names, types, nullability, defaults, semantic hints
constraints/indexes
relationships
summary statistics
diagnostics and omissions
```

The snapshot is not necessarily one monolithic Python object. It is a versioned
contract that can be persisted and projected.

Consumers:

- `Chunk` and Markdown output;
- compact digest output;
- JSON/programmatic output;
- relevant-table selection;
- EDA planning;
- optional schema graph;
- freshness and drift checks.

This should replace duplicated introspection and formatting logic rather than add
a parallel representation beside it.

## Snapshot and Refresh Policy

Different facts need different freshness rules.

### Structural Metadata: Strict

Schema, keys, indexes, and dependencies should use a stable fingerprint. Refresh
when cheap consistency checks indicate structural change:

- schema/catalog version where available;
- normalized metadata hash;
- source modification time and size for local files;
- changed table/view count;
- explicit refresh request.

### Summary Statistics: Forgiving

Summary statistics may remain useful when data changed slightly. Cache entries
should carry:

- computed time;
- source and table identity;
- row count or estimate at computation;
- sample and scan policy;
- confidence/coverage;
- observed freshness signals.

Small row-count or timestamp changes can mark statistics `aging` rather than
invalid. Material drift, elapsed TTL, structural change, or user request should
trigger refresh. Callers must be able to distinguish fresh, aging, stale, and
partial results.

### Trends and Scratch Knowledge: Provenanced

Store learned trends and directives separately from observed metadata:

- user directive;
- agent hypothesis;
- verified observation;
- preferred table/join;
- known bad column;
- domain label;
- recurring trend;
- pending question.

Every record should include source, author/origin, timestamp, confidence, and
scope. Hypotheses must not silently become database facts.

## Actual Data Retention Policy

Raw rows are more sensitive and expensive than metadata. Retention should be
policy-driven.

### Default: Metadata and Digests

- persist schema, relationships, fingerprints, summaries, and bounded
  statistics;
- avoid persistent raw rows;
- keep transient query results only for the active operation.

### Personal/Local Mode

Explicitly permit bounded samples and scratch extracts:

- configurable size and age limits;
- local, git-ignored storage;
- source-specific opt-outs;
- manual pinning for useful working datasets;
- visible disk usage and purge controls.

This mode supports exploratory personal work where convenience outweighs strict
retention.

### Restricted/Managed Mode

- metadata-only or redacted samples;
- denylisted columns and tables;
- PII/secret detection;
- encryption and access policy supplied by deployment;
- short TTLs and auditable refresh;
- no silent policy loosening by agents.

Archive extraction should inherit the same policy after content is identified.

## Batching and Budget Selection

Do not decide batching before understanding source shape.

After structural peek:

- batch compatible aggregates into one query per table when dialect permits;
- split very wide tables into safe groups;
- use approximate distinct/count functions where available;
- sample or partition large tables;
- push computation to database;
- stream results rather than materializing large frames;
- stop with partial diagnostics when time/query/byte budgets are reached.

Policy inputs may include:

- table and column counts;
- row estimates and source bytes;
- remote versus local source;
- index/partition availability;
- database load sensitivity;
- user intent;
- personal versus managed mode.

## Profile Cache

Cache key should include:

- source identity without credentials;
- structural fingerprint;
- table/column selection;
- profiling policy and version;
- sample/scan method;
- relevant dialect capabilities.

Cache should support:

- cheap unchanged reads;
- soft reuse of aging summary statistics;
- explicit refresh;
- per-source size accounting;
- TTL and least-recently-used cleanup;
- pinned personal artifacts;
- provenance for scratch knowledge.

Ancient detailed records may be compacted into durable summaries and trends.
Raw samples should expire sooner than metadata and verified high-level findings.

## Optional Metadata Graph

Project snapshot metadata as:

```text
(Database)-[:HAS_SCHEMA]->(Schema)
(Schema)-[:HAS_TABLE]->(Table)
(Table)-[:HAS_COLUMN]->(Column)
(Column)-[:REFERENCES]->(Column)
(View)-[:READS_FROM]->(Table)
(Table)-[:HAS_INDEX]->(Index)
```

Uses:

- relevant-table and join-path discovery;
- view lineage;
- migration impact;
- schema drift;
- compact context selection;
- connecting application code to tables later.

SQL remains the row-data query language. Raw rows and distributions should not
become graph nodes by default.

## What "Graph Store" Means

Current codegraph persistence is a SQLite database. It stores projects, file
hashes, nodes, edges, summaries, and related properties. Native C code owns
indexing and Cypher-like graph queries; Python currently reads the donor SQLite
schema through a read-only adapter.

It is not a special in-memory native data structure, and "use the graph store"
does not mean replacing databases with C structures.

For database metadata, future options are:

1. Separate SQLite metadata graph using the same action/output conventions.
2. A namespaced overlay database owned by thepipe.
3. Generic sidecar/shared-library ingestion added to donor ABI.

Do not write database metadata directly into private donor tables. Begin with the
smallest stable SQLite representation. Consider native ingestion only after
measured metadata scale or query latency requires it.

## Existing Output Contract

Keep all useful thepipe forms:

- structured snapshot;
- `Chunk[]`;
- Markdown;
- compact digest;
- programmatic JSON;
- optional graph actions.

Graph metadata supplements these outputs. It does not make graph JSON the
default model-facing payload.

## Known Defects Motivating Future Work

Observed against current database paths:

- LLM iterative SQL can execute writes under autocommit.
- SQLAlchemy/JupySQL parameter mappings do not reach driver execution correctly.
- Direct SQL triggers repeated schema and automatic-analysis work.
- EDA profiles the first table rather than the queried table.
- Profiling uses repeated per-column scans.
- schema/analysis formatting is duplicated.
- `.db` detection conflates SQLite and DuckDB; `.sqlite` file detection is weak.
- verbose connection logs may expose credentials.
- cleanup is not consistently protected by `finally`.
- JupySQL may be installed at runtime.
- the only JSONL integration test uses the wrong import gate and skips.

These are evidence for the roadmap, not authorization to rewrite database code
without scoped tests and review.

## Proposed Delivery Sequence

1. **Safety tests and read-only guardrails**
   Prove generated SQL cannot mutate sources; repair parameter tests.
2. **Snapshot contract**
   Define versioned structure and render existing outputs from fixtures.
3. **Single introspection pass**
   Reuse snapshot across schema, preview, SQL, and NL paths.
4. **First-contact structural crawl**
   Add whole-source shape and consistency fingerprints.
5. **Digest and EDA policy**
   Add adaptive depth, batching, budgets, and partial diagnostics.
6. **Profile cache and retention**
   Implement strict metadata and forgiving-statistics freshness.
7. **Relevant-scope selection**
   Use query intent, metadata, relationships, and directives.
8. **Optional metadata graph**
   Add schema topology without raw-row graph storage.
9. **Execution adapter substitution**
   Replace JupySQL only after parity contracts are green.
10. **Native generic ingestion, only if measured**
    Preserve sidecar/shared-library symmetry and fallback.

Sequence may change after tests. In particular, adapter replacement can move
earlier if repairing JupySQL proves more complex than direct SQLAlchemy parity.

## Alternatives Considered

### Keep JupySQL indefinitely

Reasonable if Ploomber/notebook deployment becomes strategic again. Currently
rejected as default direction because notebook-specific behavior adds complexity
without serving primary agent workflows.

### Replace database mode with codegraph

Rejected. Codegraph does not provide database connections, transactions,
profiling, row queries, or dataframe/archive handling.

### Crawl and profile everything on every call

Rejected. Good first contact needs broad shape, but repeated exhaustive scans are
unsafe and expensive. Persisted snapshots and adaptive refresh preserve breadth.

### Budget before inspecting source

Rejected as sole policy. Structural peek should inform batching, sampling, and
limits. Hard safety ceilings still apply from the first operation.

### Store raw data by default

Rejected. Metadata and digests provide most reusable value with lower privacy,
disk, and staleness risk. Personal mode can explicitly loosen retention.

## Open Questions

- Exact `DatabaseSnapshot` versioned schema.
- How source identity should work for rotating credentials and replicas.
- Default thresholds for `fresh`, `aging`, and `stale` statistics.
- Which summary statistics are portable enough for baseline support.
- Whether SQL parsing needs a new dependency or adapter-native validation.
- How dataframe/archive snapshots share policy with database snapshots.
- How scratch directives are edited, invalidated, and compacted.
- Whether metadata graph shares lifecycle registry with codegraph.
- When personal mode may persist raw samples and whether encryption is required.
- Which server-database integration tests run in CI versus optional environments.

## Implementation Gate

Before implementation begins:

- approve snapshot contract;
- approve retention modes;
- define read-only enforcement per supported backend;
- define compatibility tests for current database/file formats;
- measure representative small, wide, large, local, and remote sources;
- split work into independently reviewable safety, snapshot, EDA, cache, and
  graph changes.

Until then, this ADR remains proposed and documentation-only.
