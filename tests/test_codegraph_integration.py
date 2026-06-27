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


def test_graph_mode_archive_requires_checksum(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="codegraph_archive requires codegraph_sha256"):
        process_codegraph(
            tmp_path,
            options={"codegraph_archive": "/tmp/codegraph.tar.gz"},
        )


def test_graph_mode_installs_verified_sidecar_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {}

    def fake_install_sidecar_archive(
        archive,
        *,
        expected_sha256,
        required_version,
        install_dir=None,
        binary_name="codebase-memory-mcp",
    ):
        calls["install"] = {
            "archive": archive,
            "expected_sha256": expected_sha256,
            "required_version": required_version,
            "install_dir": install_dir,
            "binary_name": binary_name,
        }
        return tmp_path / "bin" / "codebase-memory-mcp-0.10.0"

    class FakeSidecarBackend:
        kind = "sidecar"

        def __init__(self, binary, *, cache_dir, timeout):
            calls["backend"] = {
                "binary": binary,
                "cache_dir": cache_dir,
                "timeout": timeout,
            }

        def close(self):
            calls["closed"] = True

    class FakeClient:
        def __init__(self, backend, *, registry, git_exclude):
            calls["client"] = {"git_exclude": git_exclude}

        def index_repository(self, root, *, mode, persistence):
            calls["index"] = {"root": root, "mode": mode, "persistence": persistence}

        def load_artifacts(self, root):
            from thepipe.codegraph.outputs import CodegraphArtifacts

            project, _ = _project_database(root)
            chunks = process_codegraph(root, options={})
            payload = chunks[0].meta["code_relations_payload"]
            assert payload["project"] == project
            return CodegraphArtifacts(
                payload=payload,
                digest="graph digest",
                chunks=chunks[1:],
            )

    from thepipe.codegraph import integration

    monkeypatch.setattr(integration, "install_sidecar_archive", fake_install_sidecar_archive)
    monkeypatch.setattr(integration, "SidecarBackend", FakeSidecarBackend)
    monkeypatch.setattr(integration, "CodegraphClient", FakeClient)

    chunks = process_codegraph(
        tmp_path,
        options={
            "codegraph_archive": "/tmp/codegraph.tar.gz",
            "codegraph_sha256": "abc123",
            "codegraph_required_version": "0.10.0",
            "codegraph_install_dir": str(tmp_path / "bin"),
            "codegraph_index_mode": "fast",
            "codegraph_timeout": 12,
        },
    )

    assert chunks[0].meta["schema_version"] == "code-relations/v2"
    assert calls["install"]["archive"] == "/tmp/codegraph.tar.gz"
    assert calls["install"]["expected_sha256"] == "abc123"
    assert calls["install"]["required_version"] == "0.10.0"
    assert calls["backend"]["binary"] == tmp_path / "bin" / "codebase-memory-mcp-0.10.0"
    assert calls["backend"]["timeout"] == 12
    assert calls["index"]["mode"] == "fast"
    assert calls["closed"] is True


def test_scrape_directory_routes_explicit_graph_mode(tmp_path: Path) -> None:
    _project_database(tmp_path)

    chunks = scrape_directory(
        str(tmp_path),
        options={"code_relations": "graph"},
    )

    assert chunks[0].meta["schema_version"] == "code-relations/v2"
