from __future__ import annotations
from pathlib import Path

import pytest

from thepipe.codegraph.client import CodegraphClient
from thepipe.codegraph.storage import (
    MasterRegistry,
    native_project_name,
    project_cache_dir,
    project_db_path,
    read_manifest,
    write_manifest,
    CodegraphDeployment,
)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, tool: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((tool, payload))
        if tool == "index_repository":
            return {
                "project": native_project_name(Path(str(payload["repo_path"]))),
                "indexed": True,
            }
        if tool == "query_graph":
            return {"columns": [], "rows": []}
        return {"summary": {"indexed": True}, "files": [], "entities": [], "edges": []}


def test_index_repository_uses_native_schema_and_returns_artifacts(tmp_path: Path) -> None:
    backend = FakeBackend()
    client = CodegraphClient(backend)

    artifacts = client.index_repository(
        tmp_path,
        mode="moderate",
        persistence=True,
        target_projects=["shared-api"],
    )

    assert backend.calls[:1] == [
        (
            "index_repository",
            {
                "repo_path": str(tmp_path),
                "mode": "moderate",
                "persistence": True,
                "target_projects": ["shared-api"],
            },
        )
    ]
    assert [tool for tool, _ in backend.calls[1:]] == [
        "query_graph",
        "query_graph",
        "query_graph",
    ]
    assert artifacts.payload["summary"]["indexed"] is True
    assert artifacts.payload["mode"] == "map"


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "tool", "payload"),
    [
        ("list_projects", (), {}, "list_projects", {}),
        ("index_status", ("demo",), {}, "index_status", {"project": "demo"}),
        ("delete_project", ("demo",), {}, "delete_project", {"project": "demo"}),
        (
            "search_graph",
            ("demo",),
            {"query": "settings", "label": "Function", "limit": 20, "offset": 20},
            "search_graph",
            {
                "project": "demo",
                "query": "settings",
                "label": "Function",
                "limit": 20,
                "offset": 20,
            },
        ),
        (
            "query_graph",
            ("demo", "MATCH (n) RETURN n"),
            {"max_rows": 50},
            "query_graph",
            {"project": "demo", "query": "MATCH (n) RETURN n", "max_rows": 50},
        ),
        (
            "trace_path",
            ("demo", "main"),
            {"direction": "outbound", "depth": 4, "risk_labels": True},
            "trace_path",
            {
                "project": "demo",
                "function_name": "main",
                "direction": "outbound",
                "depth": 4,
                "mode": "calls",
                "risk_labels": True,
                "include_tests": False,
            },
        ),
        (
            "get_code_snippet",
            ("demo", "demo.src.main"),
            {"include_neighbors": True},
            "get_code_snippet",
            {
                "project": "demo",
                "qualified_name": "demo.src.main",
                "include_neighbors": True,
            },
        ),
        (
            "get_graph_schema",
            ("demo",),
            {},
            "get_graph_schema",
            {"project": "demo"},
        ),
        (
            "get_architecture",
            ("demo",),
            {"path": "src", "aspects": ["languages", "routes"]},
            "get_architecture",
            {"project": "demo", "path": "src", "aspects": ["languages", "routes"]},
        ),
        (
            "search_code",
            ("demo", "TODO"),
            {"file_pattern": "*.py", "regex": True},
            "search_code",
            {
                "project": "demo",
                "pattern": "TODO",
                "file_pattern": "*.py",
                "mode": "compact",
                "regex": True,
                "limit": 10,
            },
        ),
        (
            "detect_changes",
            ("demo",),
            {"since": "HEAD~2"},
            "detect_changes",
            {
                "project": "demo",
                "scope": "symbols",
                "depth": 2,
                "base_branch": "main",
                "since": "HEAD~2",
            },
        ),
        (
            "manage_adr",
            ("demo",),
            {"mode": "update", "content": "# Decision"},
            "manage_adr",
            {"project": "demo", "mode": "update", "content": "# Decision"},
        ),
        (
            "ingest_traces",
            ("demo", [{"url": "/health"}]),
            {},
            "ingest_traces",
            {"project": "demo", "traces": [{"url": "/health"}]},
        ),
    ],
)
def test_client_maps_public_methods_to_native_tools(
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    tool: str,
    payload: dict[str, object],
) -> None:
    backend = FakeBackend()
    client = CodegraphClient(backend)

    result = getattr(client, method)(*args, **kwargs)

    if method == "query_graph":
        assert result == {"columns": [], "rows": []}
    else:
        assert result["summary"] == {"indexed": True}
    assert backend.calls == [(tool, payload)]


def test_search_graph_rejects_scalar_semantic_query() -> None:
    client = CodegraphClient(FakeBackend())

    with pytest.raises(TypeError, match="semantic_query"):
        client.search_graph("demo", semantic_query="publish")


def test_index_repository_rejects_unknown_mode(tmp_path: Path) -> None:
    client = CodegraphClient(FakeBackend())

    with pytest.raises(ValueError, match="mode"):
        client.index_repository(tmp_path, mode="turbo")


def test_successful_local_index_records_manifest_and_master_pointer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    project = native_project_name(repo.resolve())

    class IndexingBackend(FakeBackend):
        cache_dir = project_cache_dir(repo)

        def version(self) -> str:
            return "0.10.0"

        def call(self, tool: str, payload: dict[str, object]) -> dict[str, object]:
            if tool == "index_repository":
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                (self.cache_dir / f"{project}.db").touch()
                return {
                    "project": project,
                    "status": "indexed",
                    "nodes": 12,
                    "edges": 9,
                    "schema_fingerprint": "a" * 64,
                }
            if tool == "index_status":
                return {"project": project, "nodes": 12, "edges": 9}
            if tool == "query_graph":
                return {"columns": [], "rows": []}
            raise AssertionError(f"unexpected tool: {tool}")

    registry = MasterRegistry(tmp_path / "master.sqlite")
    client = CodegraphClient(IndexingBackend(), registry=registry)

    client.index_repository(repo)

    deployment = read_manifest(repo)
    assert deployment is not None
    assert deployment.db_path == project_db_path(repo, project)
    assert deployment.entity_count == 12
    assert deployment.edge_count == 9
    assert deployment.backend_version == "0.10.0"
    assert deployment.schema_fingerprint == "a" * 64
    assert registry.list() == [deployment]

    artifacts = client.load_artifacts(repo)
    assert artifacts.payload["schema_version"] == "code-relations/v2"
    assert artifacts.payload["project"] == project


def test_delete_project_removes_stale_manifest_and_master_pointer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    db = project_db_path(repo, "demo")
    db.parent.mkdir(parents=True)
    db.write_bytes(b"database")
    deployment = CodegraphDeployment(
        repo_root=repo,
        db_path=db,
        project_name="demo",
    )
    write_manifest(deployment)
    registry = MasterRegistry(tmp_path / "master.sqlite")
    registry.upsert(deployment)

    class DeleteBackend(FakeBackend):
        def call(self, tool: str, payload: dict[str, object]) -> dict[str, object]:
            db.unlink()
            return {"project": "demo", "status": "deleted"}

    result = CodegraphClient(DeleteBackend(), registry=registry).delete_project("demo")

    assert result["status"] == "deleted"
    assert read_manifest(repo) is None
    assert registry.list() == []
