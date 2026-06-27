from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from thepipe.codegraph import (
    CodegraphClient,
    MasterRegistry,
    SidecarBackend,
    project_cache_dir,
)


@pytest.mark.skipif(
    not os.environ.get("THEPIPE_CODEGRAPH_E2E_BINARY"),
    reason="set THEPIPE_CODEGRAPH_E2E_BINARY to run the real sidecar contract",
)
def test_real_sidecar_full_read_surface(tmp_path: Path) -> None:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from lib import greet\n\n"
        "def main(name: str) -> str:\n"
        "    return greet(name)\n",
        encoding="utf-8",
    )
    (repo / "lib.py").write_text(
        "def greet(name: str) -> str:\n"
        "    return f'hello {name}'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "app.py", "lib.py"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=thepipe",
            "-c",
            "user.email=tests@thepipe.local",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )

    backend = SidecarBackend(
        os.environ["THEPIPE_CODEGRAPH_E2E_BINARY"],
        cache_dir=project_cache_dir(repo),
        timeout=300,
    )
    client = CodegraphClient(
        backend,
        registry=MasterRegistry(tmp_path / "master.sqlite"),
    )
    indexed = client.index_repository(repo, mode="fast").payload["native"]
    project = indexed["project"]

    assert indexed["status"] == "indexed"
    assert client.index_status(project)["status"] == "ready"
    assert client.get_graph_schema(project)["node_labels"]
    assert client.search_graph(project, name_pattern=".*main.*")["results"]
    assert client.query_graph(
        project, "MATCH (n:Function) RETURN n.name LIMIT 10"
    )["rows"]
    assert client.trace_path(project, "main", direction="outbound")["callees"]
    assert client.get_code_snippet(
        project, f"{project}.app.main"
    )["source"].startswith("def main")
    assert client.get_architecture(project)["total_nodes"] > 0
    assert client.search_code(project, "greet")["total_results"] > 0
    assert client.detect_changes(project)["changed_files"] == []
    assert client.manage_adr(project)["status"] == "no_adr"
    assert client.ingest_traces(project, [{"url": "/health"}])["status"] == "accepted"
    assert client.list_projects()["projects"][0]["name"] == project

    artifacts = client.load_artifacts(repo)
    assert artifacts.payload["schema_version"] == "code-relations/v2"
    assert len(artifacts.payload["entities"]) == indexed["nodes"]
    assert len(artifacts.payload["edges"]) == indexed["edges"]
    assert len(artifacts.chunks) == 2
