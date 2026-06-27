from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from thepipe.core import Chunk


@dataclass(frozen=True)
class CodegraphArtifacts:
    payload: dict[str, Any]
    chunks: list[Chunk]


def build_codegraph_artifacts(
    native: dict[str, Any],
    *,
    mode: str,
    repo_root: str,
) -> CodegraphArtifacts:
    payload = {
        "schema_version": "code-relations/v1",
        "source": "codegraph-sidecar",
        "mode": mode,
        "repo_root": repo_root,
        "summary": native.get("summary", {}),
        "files": native.get("files", []),
        "entities": native.get("entities", []),
        "edges": native.get("edges", []),
        "native": native,
    }
    return CodegraphArtifacts(
        payload=payload,
        chunks=[
            Chunk(
                path="codegraph.json",
                text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
                meta={
                    "artifact": "codegraph_relations",
                    "schema_version": "code-relations/v1",
                    "source": "codegraph-sidecar",
                },
            )
        ],
    )
