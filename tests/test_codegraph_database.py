from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from thepipe.codegraph.database import CodegraphDatabase, SchemaError


def _fixture_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                name TEXT PRIMARY KEY,
                indexed_at TEXT NOT NULL,
                root_path TEXT NOT NULL
            );
            CREATE TABLE file_hashes (
                project TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                size INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (project, rel_path)
            );
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY,
                project TEXT NOT NULL,
                label TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                file_path TEXT DEFAULT '',
                start_line INTEGER DEFAULT 0,
                end_line INTEGER DEFAULT 0,
                properties TEXT DEFAULT '{}'
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY,
                project TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                properties TEXT DEFAULT '{}'
            );
            CREATE TABLE project_summaries (
                project TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO projects VALUES ('demo', '2026-01-01', '/repo')"
        )
        connection.executemany(
            "INSERT INTO file_hashes VALUES ('demo', ?, ?, 0, ?)",
            [("src/app.py", "aaa", 100), ("src/lib.py", "bbb", 50)],
        )
        connection.executemany(
            "INSERT INTO nodes VALUES (?, 'demo', ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "Function", "main", "demo.src.app.main", "src/app.py", 1, 3, '{"complexity":2}'),
                (2, "Function", "helper", "demo.src.lib.helper", "src/lib.py", 1, 2, "{}"),
            ],
        )
        connection.execute(
            "INSERT INTO edges VALUES (1, 'demo', 1, 2, 'CALLS', '{\"line\":2}')"
        )


def test_database_reads_summary_nodes_and_edges_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "demo.db"
    _fixture_database(path)

    with CodegraphDatabase(path) as database:
        assert database.check_integrity()
        assert database.summary("demo") == {
            "project": "demo",
            "root_path": "/repo",
            "indexed_at": "2026-01-01",
            "files": 2,
            "nodes": 2,
            "edges": 1,
        }
        assert [node.qualified_name for node in database.nodes("demo")] == [
            "demo.src.app.main",
            "demo.src.lib.helper",
        ]
        assert database.nodes("demo")[0].properties == {"complexity": 2}
        assert database.edges("demo")[0].type == "CALLS"
        with pytest.raises(sqlite3.OperationalError):
            database.connection.execute("CREATE TABLE forbidden (id INTEGER)")


def test_schema_fingerprint_is_stable_across_data_changes(tmp_path: Path) -> None:
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    _fixture_database(first)
    _fixture_database(second)
    with sqlite3.connect(second) as connection:
        connection.execute(
            "INSERT INTO nodes VALUES (3, 'demo', 'Class', 'C', 'demo.C', '', 0, 0, '{}')"
        )

    with CodegraphDatabase(first) as one, CodegraphDatabase(second) as two:
        assert one.schema_fingerprint() == two.schema_fingerprint()


def test_database_rejects_unknown_or_incomplete_schema(tmp_path: Path) -> None:
    path = tmp_path / "wrong.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")

    with CodegraphDatabase(path) as database:
        with pytest.raises(SchemaError, match="missing"):
            database.validate_schema()
