from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from thepipe.core import Chunk


@dataclass(frozen=True)
class CodegraphArtifacts:
    payload: dict[str, Any]
    chunks: list[Chunk]
    digest: str = ""


def build_codegraph_artifacts(
    native: dict[str, Any],
    *,
    mode: str,
    repo_root: str,
) -> CodegraphArtifacts:
    payload = {
        "schema_version": "code-relations/v2",
        "source": "codegraph-native",
        "mode": mode,
        "repo_root": repo_root,
        "project": native.get("project"),
        "summary": native.get("summary", {}),
        "files": native.get("files", []),
        "entities": native.get("entities", []),
        "edges": native.get("edges", []),
        "graph": native.get("graph", {}),
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
                    "schema_version": "code-relations/v2",
                    "source": "codegraph-native",
                },
            )
        ],
    )


def code_relations_v1(v2: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "code-relations/v1",
        "source": v2.get("source", "codegraph-native"),
        "mode": v2.get("mode", "graph"),
        "repo_root": v2.get("repo_root", ""),
        "summary": v2.get("summary", {}),
        "files": v2.get("files", []),
        "entities": v2.get("entities", []),
        "edges": v2.get("edges", []),
        "omitted_files": v2.get("omitted_files", []),
    }
