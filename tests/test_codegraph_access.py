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


def test_neighbors_rejects_ambiguous_short_entity_names(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    with sqlite3.connect(graph.database.path) as connection:
        connection.execute(
            """
            INSERT INTO nodes VALUES (
                4, 'demo', 'Function', 'main', 'demo.src.other.main',
                'src/other.py', 1, 2, '{}'
            )
            """
        )

    try:
        with pytest.raises(CodegraphAccessError, match="ambiguous entity name"):
            graph.neighbors("main")
    finally:
        graph.close()


def test_neighbors_filters_low_confidence_edges(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    with sqlite3.connect(graph.database.path) as connection:
        connection.execute(
            """
            INSERT INTO nodes VALUES (
                4, 'demo', 'Function', 'uncertain', 'demo.src.lib.uncertain',
                'src/lib.py', 4, 5, '{}'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO edges VALUES (
                3, 'demo', 1, 4, 'CALLS',
                '{"confidence":0.2,"strategy":"suffix_match"}'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO edges VALUES (
                4, 'demo', 4, 4, 'CALLS',
                '{"confidence":0.1,"strategy":"suffix_match"}'
            )
            """
        )

    try:
        result = graph.neighbors(
            "demo.src.app.main",
            direction="outbound",
            edge_types=["CALLS"],
            min_confidence=0.5,
        )
    finally:
        graph.close()

    assert [node["name"] for node in result["nodes"]] == ["main", "helper"]
    assert result["filtered_edges"] == 1


def test_neighbors_prunes_high_degree_transit_hubs(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    with sqlite3.connect(graph.database.path) as connection:
        connection.executemany(
            "INSERT INTO nodes VALUES (?, 'demo', 'Function', ?, ?, 'src/hub.py', 1, 2, '{}')",
            [
                (4, "Chunk", "demo.src.hub.Chunk"),
                (5, "leaf_a", "demo.src.hub.leaf_a"),
                (6, "leaf_b", "demo.src.hub.leaf_b"),
                (7, "leaf_c", "demo.src.hub.leaf_c"),
            ],
        )
        connection.executemany(
            "INSERT INTO edges VALUES (?, 'demo', ?, ?, 'CALLS', '{}')",
            [
                (3, 1, 4),
                (4, 5, 4),
                (5, 6, 4),
                (6, 7, 4),
            ],
        )

    try:
        result = graph.neighbors(
            "demo.src.app.main",
            direction="both",
            depth=2,
            edge_types=["CALLS"],
            max_transit_degree=2,
        )
    finally:
        graph.close()

    assert [node["name"] for node in result["nodes"]] == ["main", "helper", "Chunk"]
    assert [node["hop"] for node in result["nodes"]] == [0, 1, 1]
    assert result["pruned_hubs"] == [
        {
            "entity_id": "native:4",
            "name": "Chunk",
            "qualified_name": "demo.src.hub.Chunk",
            "degree": 4,
            "hop": 1,
        }
    ]


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
