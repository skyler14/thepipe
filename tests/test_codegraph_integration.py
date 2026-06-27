from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from thepipe.analyzer.integration import build_code_relations_json_payload
from thepipe.codegraph.integration import process_codegraph
from thepipe.codegraph.storage import (
    CodegraphDeployment,
    native_project_name,
    project_db_path,
    write_manifest,
)
from thepipe.scraper import scrape_directory


def _project_database(repo: Path) -> tuple[str, Path]:
    project = native_project_name(repo)
    db = project_db_path(repo, project)
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
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
            """
        )
        connection.execute(
            "INSERT INTO projects VALUES (?, '2026-01-01', ?)",
            (project, str(repo)),
        )
        connection.execute(
            "INSERT INTO file_hashes VALUES (?, 'app.py', 'abc', 1, 20)",
            (project,),
        )
        connection.execute(
            """
            INSERT INTO nodes VALUES (
                1, ?, 'Function', 'main', ?, 'app.py', 1, 2,
                '{"signature":"def main()"}'
            )
            """,
            (project, f"{project}.app.main"),
        )
    write_manifest(
        CodegraphDeployment(
            repo_root=repo,
            db_path=db,
            project_name=project,
        )
    )
    return project, db


def test_graph_mode_loads_detected_database_into_normal_chunks(tmp_path: Path) -> None:
    project, _ = _project_database(tmp_path)

    chunks = process_codegraph(tmp_path, options={})

    assert chunks[0].path == "__summary__"
    assert chunks[0].meta["artifact"] == "codegraph_summary"
    assert chunks[1].path == "app.py"
    assert "def main()" in chunks[1].text
    payload = build_code_relations_json_payload(chunks, "graph", str(tmp_path))
    assert payload["schema_version"] == "code-relations/v2"
    assert payload["project"] == project


def test_graph_mode_requires_deployment_or_explicit_backend(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="codegraph_binary.*codegraph_library"):
        process_codegraph(tmp_path, options={})


def test_scrape_directory_routes_explicit_graph_mode(tmp_path: Path) -> None:
    _project_database(tmp_path)

    chunks = scrape_directory(
        str(tmp_path),
        options={"code_relations": "graph"},
    )

    assert chunks[0].meta["schema_version"] == "code-relations/v2"
