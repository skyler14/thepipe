from __future__ import annotations

import json

from thepipe.codegraph.outputs import build_codegraph_artifacts


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
