# Database Hardening TODO

Status: deferred database work, tracked on `codex/odbc-db-sources`.

This document records observed defects without changing database behavior. It is
intended to merge or cherry-pick independently of codegraph work.

## P0: Prevent LLM-Generated Writes

Observed: `execute_iterative_analysis` executes every SQL block returned by the
LLM. A test strategy containing `DELETE FROM items` removed all rows because
connections use autocommit.

Required:

- Default all natural-language and iterative analysis to read-only execution.
- Reject multi-statement SQL and mutating/DDL statements before dispatch.
- Enforce read-only mode at the connection or transaction layer where supported;
  lexical checks alone are insufficient.
- Make `debug_mode=True` bypass execution in iterative and non-iterative modes.
- Require an explicit, separate opt-in for mutation-capable database operations.

Acceptance tests:

- `DELETE`, `UPDATE`, `INSERT`, `CREATE`, `DROP`, and writable CTEs are rejected.
- SQL hidden after comments or preceding read statements is rejected.
- SQLite/DuckDB read-only connections cannot mutate even if validation fails.
- Iterative debug mode returns generated SQL and leaves database state unchanged.

## P0: Repair Parameter Binding

Observed: `Database.query("SELECT :value", {"value": 42})` raises
`A value is required for bind parameter 'value'`. `_bind_params` updates a
JupySQL namespace or returns unchanged SQL, but parameters are not passed to
SQLAlchemy execution.

Required:

- Pass parameters through driver-native binding.
- Never interpolate user values into SQL strings.
- Define one mapping/sequence parameter contract for SQLAlchemy, DuckDB, and
  ODBC adapters.

Acceptance tests:

- Named and positional parameters work for SQLite, DuckDB, and fake ODBC.
- Quotes and SQL-looking parameter values remain data.
- Missing and extra parameters produce stable errors.

## P1: Make EDA Explicit and Budgeted

Observed: one direct `SELECT COUNT(*) FROM orders` caused 23 statements. Twenty
two statements were schema/EDA overhead, mostly profiling the unrelated first
table. Wide tables add per-column `typeof`, `COUNT(DISTINCT)`, and key scans.

Required:

- Do not run EDA for ordinary explicit SQL unless requested.
- Select relevant tables from query references or an explicit table list.
- Add limits for tables, columns, scans, samples, elapsed time, and returned data.
- Batch compatible aggregate statistics into one query per selected table.
- Prefer sampled or approximate cardinality for large sources.
- Cache profiles by source identity plus schema/data freshness fingerprint.

Acceptance tests:

- Direct SQL performs no profiling statements by default.
- Profiling `orders` does not inspect `users` unless dependency context requires it.
- Query count remains bounded as column count grows.
- Budget exhaustion returns partial structured results with warnings.

## P1: Remove Duplicate Introspection and Formatting

Observed: `process_database`, `execute_query`, and `process_nl_query` each
retrieve schema or rebuild automatic-analysis Markdown. The initial schema from
`process_database` is discarded by query paths.

Required:

- Build one structured `DatabaseSnapshot` per operation.
- Keep discovery/profiling separate from Chunk/Markdown rendering.
- Share one formatter across SQL, preview, and natural-language paths.
- Reuse the snapshot throughout iterative analysis.

Acceptance tests:

- Schema discovery runs once per operation.
- SQL and natural-language outputs render from the same snapshot fixture.
- Multi-table metadata remains structured before final serialization.

## P1: Fix Connection Safety and Lifecycle

Observed:

- Verbose logs print raw connection URLs and may expose passwords.
- `process_database` does not close a manager in `finally`.
- JupySQL is installed with `pip` during runtime when unavailable.
- Connections default to autocommit, including LLM-driven paths.
- Dictionary-built URLs do not URL-encode credentials.

Required:

- Redact credentials and sensitive query parameters in every log/error.
- Use context managers or `finally` for connections and temporary files.
- Declare optional dependencies through install extras; never invoke `pip` at runtime.
- Make transaction/autocommit policy explicit per operation.
- Build URLs with SQLAlchemy URL helpers rather than string concatenation.

Acceptance tests:

- Passwords never appear in verbose output or raised errors.
- Connections and Excel temporary files close after every exception path.
- Missing optional dependencies return install guidance without modifying environment.
- Reserved characters in usernames/passwords produce valid URLs.

## P1: Replace Dialect Guesswork with Introspection Adapters

Observed:

- `.db` always resolves to DuckDB and `.sqlite` is not recognized as a file path.
- PostgreSQL and MySQL discovery assumes `table_schema='public'`.
- Generic schema and relationship queries mix incompatible dialect syntax.
- File paths and identifiers are interpolated directly into SQL.

Required:

- Separate source detection from explicit caller overrides.
- Use SQLAlchemy Inspector for supported relational databases.
- Use native DuckDB metadata for file-backed sources.
- Quote identifiers through each adapter and bind metadata values.
- Represent catalogs, schemas, tables, views, columns, keys, and indexes explicitly.

Acceptance tests:

- `.sqlite`, SQLite `.db`, and DuckDB `.db` ambiguity has deterministic policy and override.
- Non-public PostgreSQL schemas and MySQL databases are discoverable.
- Quoted/reserved table and column names work.
- Paths containing quotes cannot alter generated SQL.

## P1: Repair Test Gating and Coverage

Observed: JSONL integration test checks `find_spec("jupysql")`, while runtime
checks importable module `sql`; installed JupySQL therefore still skips the test.
No tests cover iterative write safety, parameter binding, EDA budgets, schema
reuse, or cleanup failures.

Required:

- Gate tests on the same import contract used by production.
- Add SQLite and DuckDB integration tests for core contracts.
- Add adapter fakes for unavailable server databases and ODBC drivers.
- Record query counts in EDA tests.

## Deferred Schema Graph

Do not replace database functionality with codegraph. Database adapters remain
responsible for connections, SQL, transactions, and profiling.

After structured snapshots exist, optionally project metadata as:

```text
(Database)-[:HAS_SCHEMA]->(Schema)
(Schema)-[:HAS_TABLE]->(Table)
(Table)-[:HAS_COLUMN]->(Column)
(Column)-[:REFERENCES]->(Column)
(View)-[:READS_FROM]->(Table)
(Table)-[:HAS_INDEX]->(Index)
```

Motivation: compact schema context, foreign-key path discovery, migration impact,
and schema drift. SQL remains the row-data query language. This work requires a
generic graph-ingestion contract; direct writes into donor codegraph tables are
not supported.
