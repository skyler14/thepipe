from __future__ import annotations

import json

from thepipe.codegraph.outputs import (
    build_codegraph_artifacts,
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

    assert artifacts.payload["schema_version"] == "code-relations/v2"
    assert artifacts.payload["source"] == "codegraph-native"
    assert artifacts.payload["native"] == native
    assert artifacts.chunks[0].path == "codegraph.json"
    assert artifacts.chunks[0].meta == {
        "artifact": "codegraph_relations",
        "schema_version": "code-relations/v2",
        "source": "codegraph-native",
    }
    assert json.loads(artifacts.chunks[0].text)["native"] == native


def test_v1_compatibility_projection_drops_v2_graph_metadata_only() -> None:
    v2 = {
        "schema_version": "code-relations/v2",
        "source": "codegraph-native",
        "mode": "graph",
        "repo_root": "/repo",
        "summary": {"nodes": 2},
        "graph": {"canonical": True},
        "files": [{"path": "src/app.py"}],
        "entities": [{"name": "main"}],
        "edges": [{"kind": "CALLS"}],
        "omitted_files": [],
    }

    v1 = code_relations_v1(v2)

    assert v1["schema_version"] == "code-relations/v1"
    assert v1["files"] == v2["files"]
    assert v1["entities"] == v2["entities"]
    assert v1["edges"] == v2["edges"]
    assert "graph" not in v1
