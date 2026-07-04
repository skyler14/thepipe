from __future__ import annotations

import json

from thepipe.codegraph.database import EdgeRecord, NodeRecord
from thepipe.codegraph.outputs import (
    build_codegraph_artifacts,
    build_database_artifacts,
    code_relations_v1,
)


def test_build_codegraph_artifacts_preserves_structured_payload_and_chunk_view() -> None:
    native = {
        "summary": {"nodes": 2, "edges": 1},
        "files": [{"path": "src/app.py"}],
        "entities": [{"kind": "Function", "name": "main"}],
        "edges": [{"kind": "CALLS", "from": "main", "to": "helper"}],
    }

    artifacts = build_codegraph_artifacts(native, mode="map", repo_root="/repo")

    assert artifacts.payload["schema_version"] == "code-relations/v1"
    assert artifacts.payload["source"] == "codegraph-sidecar"
    assert artifacts.payload["native"] == native
    assert artifacts.chunks[0].path == "codegraph.json"
    assert artifacts.chunks[0].meta == {
        "artifact": "codegraph_relations",
        "schema_version": "code-relations/v1",
        "source": "codegraph-sidecar",
    }
    assert json.loads(artifacts.chunks[0].text)["native"] == native


class FakeDatabase:
    def summary(self, project: str) -> dict[str, object]:
        return {
            "project": project,
            "root_path": "/repo",
            "indexed_at": "2026-01-01",
            "files": 2,
            "nodes": 2,
            "edges": 1,
        }

    def file_hashes(self, project: str) -> list[dict[str, object]]:
        return [
            {"rel_path": "src/app.py", "sha256": "aaa", "mtime_ns": 1, "size": 30},
            {"rel_path": "src/lib.py", "sha256": "bbb", "mtime_ns": 2, "size": 20},
        ]

    def nodes(self, project: str) -> list[NodeRecord]:
        return [
            NodeRecord(
                1,
                project,
                "Function",
                "main",
                "demo.src.app.main",
                "src/app.py",
                1,
                3,
                {"signature": "def main()"},
            ),
            NodeRecord(
                2,
                project,
                "Function",
                "helper",
                "demo.src.lib.helper",
                "src/lib.py",
                1,
                2,
                {},
            ),
        ]

    def edges(self, project: str) -> list[EdgeRecord]:
        return [EdgeRecord(1, project, 1, 2, "CALLS", {"line": 2})]

    def schema_fingerprint(self) -> str:
        return "schema123"


def test_database_projection_emits_v2_digest_and_compact_file_chunks() -> None:
    artifacts = build_database_artifacts(FakeDatabase(), "demo", repo_root="/repo")

    assert artifacts.payload["schema_version"] == "code-relations/v2"
    assert artifacts.payload["graph"]["schema_fingerprint"] == "schema123"
    assert artifacts.payload["summary"]["nodes"] == 2
    assert artifacts.payload["files"][0] == {
        "file_id": artifacts.payload["files"][0]["file_id"],
        "path": "src/app.py",
        "hash": "aaa",
        "mtime_ns": 1,
        "size": 30,
    }
    assert artifacts.payload["entities"][0]["entity_id"] == "native:1"
    assert artifacts.payload["edges"][0]["from_entity_id"] == "native:1"
    assert artifacts.payload["edges"][0]["to_entity_id"] == "native:2"
    assert [chunk.path for chunk in artifacts.chunks] == ["src/app.py", "src/lib.py"]
    assert "def main()" in artifacts.chunks[0].text
    assert "CALLS: main -> helper" in artifacts.digest


def test_v1_compatibility_projection_drops_v2_graph_metadata_only() -> None:
    v2 = build_database_artifacts(FakeDatabase(), "demo", repo_root="/repo").payload

    v1 = code_relations_v1(v2)

    assert v1["schema_version"] == "code-relations/v1"
    assert v1["files"] == v2["files"]
    assert v1["entities"] == v2["entities"]
    assert v1["edges"] == v2["edges"]
    assert "graph" not in v1
