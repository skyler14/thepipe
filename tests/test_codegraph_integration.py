from __future__ import annotations

import sqlite3
import json
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


def test_graph_mode_library_archive_requires_checksum(tmp_path: Path) -> None:
    with pytest.raises(
        RuntimeError,
        match="codegraph_library_archive requires codegraph_library_sha256",
    ):
        process_codegraph(
            tmp_path,
            options={"codegraph_library_archive": "/tmp/libthepipe_codegraph.tar.gz"},
        )


def test_graph_mode_uses_explicit_shared_library_with_quiet_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {}

    class FakeSharedLibraryBackend:
        kind = "shared-library"

        def __init__(self, path, *, cache_dir, quiet=True):
            calls["backend"] = {
                "path": path,
                "cache_dir": cache_dir,
                "quiet": quiet,
            }

        def close(self):
            calls["closed"] = True

    class FakeClient:
        def __init__(self, backend, *, registry, git_exclude):
            pass

        def index_repository(self, root, *, mode, persistence):
            calls["index"] = {"mode": mode, "persistence": persistence}
            _project_database(root)

        def load_artifacts(self, root):
            from thepipe.codegraph.outputs import CodegraphArtifacts

            chunks = process_codegraph(root, options={})
            return CodegraphArtifacts(
                payload=chunks[0].meta["code_relations_payload"],
                digest="graph digest",
                chunks=chunks[1:],
            )

    from thepipe.codegraph import integration

    monkeypatch.setattr(integration, "SharedLibraryBackend", FakeSharedLibraryBackend)
    monkeypatch.setattr(integration, "CodegraphClient", FakeClient)

    chunks = process_codegraph(
        tmp_path,
        options={
            "codegraph_library": "/tmp/libthepipe_codegraph.dylib",
            "codegraph_quiet": False,
        },
    )

    assert chunks[0].meta["schema_version"] == "code-relations/v2"
    assert calls["backend"]["path"] == "/tmp/libthepipe_codegraph.dylib"
    assert calls["backend"]["quiet"] is False
    assert calls["index"]["mode"] == "fast"
    assert calls["closed"] is True


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


def test_graph_mode_installs_verified_shared_library_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {}

    def fake_install_shared_library_archive(
        archive,
        *,
        expected_sha256,
        required_version,
        install_dir=None,
        library_name=None,
    ):
        calls["install"] = {
            "archive": archive,
            "expected_sha256": expected_sha256,
            "required_version": required_version,
            "install_dir": install_dir,
            "library_name": library_name,
        }
        return tmp_path / "lib" / "libthepipe_codegraph-0.10.0.dylib"

    class FakeSharedLibraryBackend:
        kind = "shared-library"

        def __init__(self, path, *, cache_dir, quiet=True):
            calls["backend"] = {
                "path": path,
                "cache_dir": cache_dir,
                "quiet": quiet,
            }

        def version(self):
            return "0.10.0"

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

    monkeypatch.setattr(
        integration,
        "install_shared_library_archive",
        fake_install_shared_library_archive,
    )
    monkeypatch.setattr(integration, "SharedLibraryBackend", FakeSharedLibraryBackend)
    monkeypatch.setattr(integration, "CodegraphClient", FakeClient)

    chunks = process_codegraph(
        tmp_path,
        options={
            "codegraph_library_archive": "/tmp/lib.tar.gz",
            "codegraph_library_sha256": "abc123",
            "codegraph_library_name": "libthepipe_codegraph.dylib",
            "codegraph_required_version": "0.10.0",
            "codegraph_install_dir": str(tmp_path / "lib"),
            "codegraph_quiet": False,
        },
    )

    assert chunks[0].meta["schema_version"] == "code-relations/v2"
    assert calls["install"]["archive"] == "/tmp/lib.tar.gz"
    assert calls["install"]["library_name"] == "libthepipe_codegraph.dylib"
    assert calls["backend"]["path"] == (
        tmp_path / "lib" / "libthepipe_codegraph-0.10.0.dylib"
    )
    assert calls["backend"]["quiet"] is False
    assert calls["index"]["mode"] == "fast"
    assert calls["closed"] is True


def test_graph_mode_rejects_wrong_shared_library_archive_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_install_shared_library_archive(*args, **kwargs):
        return tmp_path / "lib" / "libthepipe_codegraph-0.10.0.dylib"

    class FakeSharedLibraryBackend:
        def __init__(self, path, *, cache_dir, quiet=True):
            self.closed = False

        def version(self):
            return "0.9.0"

        def close(self):
            self.closed = True

    from thepipe.codegraph import integration

    monkeypatch.setattr(
        integration,
        "install_shared_library_archive",
        fake_install_shared_library_archive,
    )
    monkeypatch.setattr(integration, "SharedLibraryBackend", FakeSharedLibraryBackend)

    with pytest.raises(RuntimeError, match="requires version 0.10.0"):
        process_codegraph(
            tmp_path,
            options={
                "codegraph_library_archive": "/tmp/lib.tar.gz",
                "codegraph_library_sha256": "abc123",
                "codegraph_required_version": "0.10.0",
            },
        )


def test_scrape_directory_routes_explicit_graph_mode(tmp_path: Path) -> None:
    _project_database(tmp_path)

    chunks = scrape_directory(
        str(tmp_path),
        options={"code_relations": "graph"},
    )

    assert chunks[0].meta["schema_version"] == "code-relations/v2"


def test_scrape_directory_can_query_detected_graph_entities(tmp_path: Path) -> None:
    _project_database(tmp_path)

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_action": "entities",
            "codegraph_query": "main",
        },
    )

    payload = json.loads(chunks[0].text)
    assert chunks[0].path == "codegraph-action.json"
    assert chunks[0].meta["action"] == "entities"
    assert payload["schema_version"] == "thepipe-codegraph-action/v1"
    assert payload["result"][0]["qualified_name"].endswith(".app.main")
    assert "attributes" not in payload["result"][0]


def test_graph_actions_can_return_verbose_attributes(tmp_path: Path) -> None:
    _project_database(tmp_path)

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_action": "entities",
            "codegraph_query": "main",
            "codegraph_verbose": True,
        },
    )

    payload = json.loads(chunks[0].text)
    assert payload["result"][0]["attributes"] == {"signature": "def main()"}


def test_graph_read_action_uses_existing_deployment_without_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project_database(tmp_path)
    calls = {}

    class FakeBackend:
        kind = "sidecar"

        def call(self, tool, payload):
            raise AssertionError(f"unexpected backend call: {tool}")

        def close(self):
            calls["closed"] = True

    class FakeClient:
        def __init__(self, backend, *, registry, git_exclude):
            pass

        def index_repository(self, root, *, mode, persistence):
            calls["indexed"] = True

    from thepipe.codegraph import integration

    monkeypatch.setattr(integration, "SidecarBackend", lambda *a, **k: FakeBackend())
    monkeypatch.setattr(integration, "CodegraphClient", FakeClient)

    chunks = process_codegraph(
        tmp_path,
        options={
            "codegraph_binary": "/tmp/native",
            "codegraph_action": "summary",
        },
    )

    payload = json.loads(chunks[0].text)
    assert payload["action"] == "summary"
    assert "indexed" not in calls
    assert calls["closed"] is True


def test_graph_read_action_refreshes_when_no_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {}

    class FakeBackend:
        kind = "sidecar"

        def close(self):
            calls["closed"] = True

    class FakeClient:
        def __init__(self, backend, *, registry, git_exclude):
            pass

        def index_repository(self, root, *, mode, persistence):
            calls["indexed"] = True
            _project_database(root)

    from thepipe.codegraph import integration

    monkeypatch.setattr(integration, "SidecarBackend", lambda *a, **k: FakeBackend())
    monkeypatch.setattr(integration, "CodegraphClient", FakeClient)

    chunks = process_codegraph(
        tmp_path,
        options={
            "codegraph_binary": "/tmp/native",
            "codegraph_action": "summary",
        },
    )

    payload = json.loads(chunks[0].text)
    assert payload["result"]["nodes"] == 1
    assert calls["indexed"] is True
    assert calls["closed"] is True


def test_graph_action_payload_survives_json_projection(tmp_path: Path) -> None:
    _project_database(tmp_path)
    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_action": "summary",
        },
    )

    payload = build_code_relations_json_payload(chunks, "graph", str(tmp_path))

    assert payload["schema_version"] == "thepipe-codegraph-action/v1"
    assert payload["action"] == "summary"
    assert payload["result"]["nodes"] == 1


def test_graph_files_action_respects_limit(tmp_path: Path) -> None:
    project, _ = _project_database(tmp_path)
    with sqlite3.connect(
        tmp_path / ".thepipe" / "codegraph" / "cache" / f"{project}.db"
    ) as connection:
        connection.execute(
            "INSERT INTO file_hashes VALUES (?, 'extra.py', 'def', 1, 20)",
            (project,),
        )

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_action": "files",
            "codegraph_limit": 1,
        },
    )

    payload = json.loads(chunks[0].text)
    assert len(payload["result"]) == 1


def test_scrape_directory_can_run_read_only_graph_sql(tmp_path: Path) -> None:
    _project_database(tmp_path)

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_action": "sql",
            "codegraph_sql": "SELECT name FROM nodes ORDER BY id",
            "codegraph_limit": 1,
        },
    )

    payload = json.loads(chunks[0].text)
    assert payload["result"]["rows"] == [{"name": "main"}]


@pytest.mark.parametrize(
    ("action", "options", "expected_tool", "expected_payload", "native_result"),
    [
        (
            "search_graph",
            {"codegraph_query": "main", "codegraph_kind": "Function", "codegraph_limit": 7},
            "search_graph",
            {"query": "main", "label": "Function", "limit": 7, "offset": 0},
            {"results": [{"name": "main"}], "total": 1},
        ),
        (
            "query_graph",
            {"codegraph_cypher": "MATCH (n) RETURN n", "codegraph_limit": 3},
            "query_graph",
            {"query": "MATCH (n) RETURN n", "max_rows": 3},
            {"columns": ["n"], "rows": [["main"]]},
        ),
        (
            "trace_path",
            {
                "codegraph_entity": "main",
                "codegraph_direction": "outbound",
                "codegraph_depth": 2,
                "codegraph_edge_types": ["CALLS"],
                "codegraph_include_tests": True,
            },
            "trace_path",
            {
                "function_name": "main",
                "direction": "outbound",
                "depth": 2,
                "mode": "calls",
                "edge_types": ["CALLS"],
                "risk_labels": False,
                "include_tests": True,
            },
            {"root": "main", "callees": ["helper"]},
        ),
        (
            "get_code_snippet",
            {"codegraph_qualified_name": "demo.app.main", "codegraph_include_neighbors": True},
            "get_code_snippet",
            {"qualified_name": "demo.app.main", "include_neighbors": True},
            {"qualified_name": "demo.app.main", "source": "def main(): pass"},
        ),
        (
            "get_graph_schema",
            {},
            "get_graph_schema",
            {},
            {"node_labels": [{"label": "Function", "count": 1}]},
        ),
        (
            "get_architecture",
            {"codegraph_file": "src", "codegraph_aspects": ["languages", "routes"]},
            "get_architecture",
            {"path": "src", "aspects": ["languages", "routes"]},
            {"languages": [{"name": "Python"}]},
        ),
        (
            "search_code",
            {
                "codegraph_pattern": "TODO",
                "codegraph_file_pattern": "*.py",
                "codegraph_regex": True,
                "codegraph_limit": 4,
            },
            "search_code",
            {
                "pattern": "TODO",
                "file_pattern": "*.py",
                "mode": "compact",
                "regex": True,
                "limit": 4,
            },
            {"matches": [{"file": "app.py"}]},
        ),
        (
            "detect_changes",
            {"codegraph_depth": 4, "codegraph_base_branch": "develop"},
            "detect_changes",
            {"scope": "symbols", "depth": 4, "base_branch": "develop"},
            {"changed_files": ["app.py"], "impacted_symbols": ["main"]},
        ),
        (
            "manage_adr",
            {"codegraph_adr_mode": "get"},
            "manage_adr",
            {"mode": "get"},
            {"content": "# ADR"},
        ),
        (
            "ingest_traces",
            {"codegraph_traces": [{"method": "GET", "url": "/health"}]},
            "ingest_traces",
            {"traces": [{"method": "GET", "url": "/health"}]},
            {"accepted": 1, "mutated": False},
        ),
    ],
)
def test_shared_library_graph_action_routes_native_mcp_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    options: dict[str, object],
    expected_tool: str,
    expected_payload: dict[str, object],
    native_result: dict[str, object],
) -> None:
    project, _ = _project_database(tmp_path)
    calls = {}

    class FakeSharedLibraryBackend:
        def __init__(self, path, *, cache_dir, quiet=True):
            calls["backend"] = {"path": path, "cache_dir": cache_dir, "quiet": quiet}

        def call(self, tool, payload):
            calls["call"] = (tool, payload)
            return native_result

        def close(self):
            calls["closed"] = True

    from thepipe.codegraph import integration

    monkeypatch.setattr(integration, "SharedLibraryBackend", FakeSharedLibraryBackend)

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_library": "/tmp/libthepipe_codegraph.dylib",
            "codegraph_action": action,
            **options,
        },
    )

    payload = json.loads(chunks[0].text)
    assert payload["action"] == action
    assert payload["result"] == native_result
    expected = {"project": project, **expected_payload}
    assert calls["call"] == (expected_tool, expected)
    assert calls["closed"] is True


@pytest.mark.parametrize(
    ("action", "expected_tool", "native_result"),
    [
        ("list_projects", "list_projects", {"projects": [{"name": "demo"}]}),
        ("index_status", "index_status", {"status": "ready"}),
        ("delete_project", "delete_project", {"status": "deleted"}),
    ],
)
def test_project_management_graph_actions_do_not_auto_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    expected_tool: str,
    native_result: dict[str, object],
) -> None:
    calls = []

    class FakeSharedLibraryBackend:
        def __init__(self, path, *, cache_dir, quiet=True):
            pass

        def call(self, tool, payload):
            calls.append((tool, payload))
            return native_result

        def close(self):
            calls.append(("close", {}))

    from thepipe.codegraph import integration

    monkeypatch.setattr(integration, "SharedLibraryBackend", FakeSharedLibraryBackend)

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_library": "/tmp/libthepipe_codegraph.dylib",
            "codegraph_action": action,
            "codegraph_project": "demo",
        },
    )

    payload = json.loads(chunks[0].text)
    assert payload["result"] == native_result
    expected_payload = {} if action == "list_projects" else {"project": "demo"}
    assert calls == [(expected_tool, expected_payload), ("close", {})]


def test_native_query_graph_action_does_not_use_sql_escape_hatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project_database(tmp_path)
    calls = []

    class FakeSharedLibraryBackend:
        def __init__(self, path, *, cache_dir, quiet=True):
            pass

        def call(self, tool, payload):
            calls.append((tool, payload))
            return {"columns": ["n"], "rows": []}

        def close(self):
            pass

    from thepipe.codegraph import integration

    monkeypatch.setattr(integration, "SharedLibraryBackend", FakeSharedLibraryBackend)

    scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_library": "/tmp/libthepipe_codegraph.dylib",
            "codegraph_action": "query_graph",
            "codegraph_cypher": "MATCH (n) RETURN n",
            "codegraph_sql": "SELECT * FROM nodes",
        },
    )

    assert calls == [
        (
            "query_graph",
            {
                "project": native_project_name(tmp_path),
                "query": "MATCH (n) RETURN n",
            },
        )
    ]


def test_scrape_directory_can_traverse_graph_neighbors(tmp_path: Path) -> None:
    project, _ = _project_database(tmp_path)
    with sqlite3.connect(
        tmp_path / ".thepipe" / "codegraph" / "cache" / f"{project}.db"
    ) as connection:
        connection.execute(
            """
            INSERT INTO nodes VALUES (
                2, ?, 'Function', 'helper', ?, 'helper.py', 1, 2, '{}'
            )
            """,
            (project, f"{project}.helper.helper"),
        )
        connection.execute(
            "INSERT INTO file_hashes VALUES (?, 'helper.py', 'def', 1, 20)",
            (project,),
        )
        connection.execute(
            "INSERT INTO edges VALUES (1, ?, 1, 2, 'CALLS', '{}')",
            (project,),
        )

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_action": "neighbors",
            "codegraph_entity": "main",
            "codegraph_direction": "outbound",
        },
    )

    payload = json.loads(chunks[0].text)
    assert [node["name"] for node in payload["result"]["nodes"]] == ["main", "helper"]
    assert payload["result"]["edges"][0]["kind"] == "CALLS"


def test_graph_neighbor_action_applies_confidence_filter(tmp_path: Path) -> None:
    project, db = _project_database(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.executemany(
            """
            INSERT INTO nodes VALUES (
                ?, ?, 'Function', ?, ?, 'helper.py', 1, 2, '{}'
            )
            """,
            [
                (2, project, "certain", f"{project}.helper.certain"),
                (3, project, "uncertain", f"{project}.helper.uncertain"),
            ],
        )
        connection.executemany(
            "INSERT INTO edges VALUES (?, ?, 1, ?, 'CALLS', ?)",
            [
                (1, project, 2, '{"confidence":0.9}'),
                (2, project, 3, '{"confidence":0.2}'),
            ],
        )

    chunks = scrape_directory(
        str(tmp_path),
        options={
            "code_relations": "graph",
            "codegraph_action": "neighbors",
            "codegraph_entity": "main",
            "codegraph_direction": "outbound",
            "codegraph_min_confidence": 0.5,
        },
    )

    payload = json.loads(chunks[0].text)
    assert [node["name"] for node in payload["result"]["nodes"]] == [
        "main",
        "certain",
    ]
    assert payload["result"]["filtered_edges"] == 1
