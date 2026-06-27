from __future__ import annotations

from pathlib import Path

from thepipe.codegraph.client import CodegraphClient
from thepipe.codegraph.storage import project_db_path


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def call(self, tool: str, payload: dict[str, str]) -> dict[str, object]:
        self.calls.append((tool, payload))
        return {"summary": {"indexed": True}, "files": [], "entities": [], "edges": []}


def test_index_repository_calls_sidecar_with_project_db_and_returns_artifacts(tmp_path: Path) -> None:
    backend = FakeBackend()
    client = CodegraphClient(backend)

    artifacts = client.index_repository(tmp_path)

    assert backend.calls == [
        (
            "index_repository",
            {
                "path": str(tmp_path),
                "database_path": str(project_db_path(tmp_path)),
            },
        )
    ]
    assert artifacts.payload["summary"] == {"indexed": True}
    assert artifacts.payload["mode"] == "map"
