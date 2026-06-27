from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from thepipe.codegraph.access import CodegraphAccessError, CodegraphGraph
from thepipe.codegraph.storage import CodegraphDeployment, write_manifest


def _database(path: Path, repo: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (name TEXT, indexed_at TEXT, root_path TEXT);
            CREATE TABLE file_hashes (
                project TEXT, rel_path TEXT, sha256 TEXT, mtime_ns INTEGER, size INTEGER
            );
            CREATE TABLE nodes (
                id INTEGER, project TEXT, label TEXT, name TEXT, qualified_name TEXT,
                file_path TEXT, start_line INTEGER, end_line INTEGER, properties TEXT
            );
            CREATE TABLE edges (
                id INTEGER, project TEXT, source_id INTEGER, target_id INTEGER,
                type TEXT, properties TEXT
            );
            CREATE TABLE project_summaries (
                project TEXT, summary TEXT, source_hash TEXT,
                created_at TEXT, updated_at TEXT
            );
            INSERT INTO projects VALUES ('demo', '2026-01-01', '/repo');
            INSERT INTO file_hashes VALUES ('demo', 'src/app.py', 'aaa', 1, 100);
            INSERT INTO file_hashes VALUES ('demo', 'src/lib.py', 'bbb', 2, 50);
            INSERT INTO nodes VALUES (
                1, 'demo', 'Function', 'main', 'demo.src.app.main',
                'src/app.py', 1, 4, '{"signature":"def main()"}'
            );
            INSERT INTO nodes VALUES (
                2, 'demo', 'Function', 'helper', 'demo.src.lib.helper',
                'src/lib.py', 1, 2, '{}'
            );
            INSERT INTO nodes VALUES (
                3, 'demo', 'Class', 'Settings', 'demo.src.app.Settings',
                'src/app.py', 6, 9, '{}'
            );
            INSERT INTO edges VALUES (1, 'demo', 1, 2, 'CALLS', '{"line":3}');
            INSERT INTO edges VALUES (2, 'demo', 3, 1, 'CONFIGURES', '{}');
            """
        )
    write_manifest(
        CodegraphDeployment(
            repo_root=repo,
            db_path=path,
            project_name="demo",
        )
    )


def _graph(tmp_path: Path) -> CodegraphGraph:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = repo / ".thepipe" / "codegraph" / "cache" / "demo.db"
    db.parent.mkdir(parents=True)
    _database(db, repo)
    return CodegraphGraph.open_repo(repo)


def test_open_repo_exposes_summary_files_entities_and_edges(tmp_path: Path) -> None:
    with _graph(tmp_path) as graph:
        assert graph.summary()["nodes"] == 3
        assert [row["rel_path"] for row in graph.files()] == [
            "src/app.py",
            "src/lib.py",
        ]
        assert [entity["qualified_name"] for entity in graph.entities()] == [
            "demo.src.app.main",
            "demo.src.lib.helper",
            "demo.src.app.Settings",
        ]
        assert graph.edges()[0]["kind"] == "CALLS"


def test_find_entities_filters_by_query_kind_and_file(tmp_path: Path) -> None:
    with _graph(tmp_path) as graph:
        results = graph.find_entities(
            query="main",
            kind="Function",
            file_path="app.py",
        )

    assert [result["qualified_name"] for result in results] == ["demo.src.app.main"]


def test_neighbors_returns_bounded_traversal(tmp_path: Path) -> None:
    with _graph(tmp_path) as graph:
        outbound = graph.neighbors("demo.src.app.main", direction="outbound")
        both = graph.neighbors("native:1", direction="both", edge_types=["CONFIGURES"])

    assert [node["name"] for node in outbound["nodes"]] == ["main", "helper"]
    assert outbound["edges"][0]["kind"] == "CALLS"
    assert [node["name"] for node in both["nodes"]] == ["main", "Settings"]
    assert both["edges"][0]["kind"] == "CONFIGURES"


def test_query_sql_is_read_only_and_bounded(tmp_path: Path) -> None:
    with _graph(tmp_path) as graph:
        rows = graph.query_sql(
            "SELECT name FROM nodes WHERE project = ? ORDER BY id",
            ["demo"],
            max_rows=2,
        )
        with pytest.raises(ValueError, match="read-only"):
            graph.query_sql("DELETE FROM nodes")

    assert rows["rows"] == [{"name": "main"}, {"name": "helper"}]


def test_open_repo_requires_detected_deployment(tmp_path: Path) -> None:
    with pytest.raises(CodegraphAccessError, match="no codegraph deployment"):
        CodegraphGraph.open_repo(tmp_path)
