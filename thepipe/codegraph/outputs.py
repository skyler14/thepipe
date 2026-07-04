from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from typing import Any

from thepipe.core import Chunk

from .database import CodegraphDatabase


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


def build_database_artifacts(
    database: CodegraphDatabase,
    project: str,
    *,
    repo_root: str,
) -> CodegraphArtifacts:
    summary = database.summary(project)
    file_rows = database.file_hashes(project)
    nodes = database.nodes(project)
    edges = database.edges(project)
    file_ids = {
        str(row["rel_path"]): _file_id(str(row["rel_path"])) for row in file_rows
    }
    files = [
        {
            "file_id": file_ids[str(row["rel_path"])],
            "path": row["rel_path"],
            "hash": row["sha256"],
            "mtime_ns": row["mtime_ns"],
            "size": row["size"],
        }
        for row in file_rows
    ]
    entities = [
        {
            "entity_id": f"native:{node.id}",
            "native_id": node.id,
            "file_id": file_ids.get(node.file_path),
            "kind": node.label,
            "name": node.name,
            "qualified_name": node.qualified_name,
            "location": {
                "path": node.file_path,
                "start_line": node.start_line,
                "end_line": node.end_line,
            },
            "attributes": node.properties,
        }
        for node in nodes
    ]
    projected_edges = [
        {
            "edge_id": f"native:{edge.id}",
            "native_id": edge.id,
            "kind": edge.type,
            "from_entity_id": f"native:{edge.source_id}",
            "to_entity_id": f"native:{edge.target_id}",
            "attributes": edge.properties,
        }
        for edge in edges
    ]
    payload = {
        "schema_version": "code-relations/v2",
        "source": "codegraph-sqlite",
        "mode": "graph",
        "repo_root": repo_root,
        "project": project,
        "summary": summary,
        "graph": {
            "canonical": True,
            "schema_fingerprint": database.schema_fingerprint(),
        },
        "files": files,
        "entities": entities,
        "edges": projected_edges,
        "omitted_files": [],
    }
    chunks = _file_chunks(files, entities)
    return CodegraphArtifacts(
        payload=payload,
        chunks=chunks,
        digest=_compact_digest(project, files, entities, projected_edges),
    )


def code_relations_v1(v2: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "code-relations/v1",
        "source": v2.get("source", "codegraph-sqlite"),
        "mode": v2.get("mode", "graph"),
        "repo_root": v2.get("repo_root", ""),
        "summary": v2.get("summary", {}),
        "files": v2.get("files", []),
        "entities": v2.get("entities", []),
        "edges": v2.get("edges", []),
        "omitted_files": v2.get("omitted_files", []),
    }


def _file_id(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"file:{digest}"


def _file_chunks(
    files: list[dict[str, Any]], entities: list[dict[str, Any]]
) -> list[Chunk]:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        file_id = entity.get("file_id")
        if file_id:
            by_file.setdefault(file_id, []).append(entity)

    chunks: list[Chunk] = []
    for file in files:
        lines = [f"# {file['path']}"]
        for entity in by_file.get(file["file_id"], []):
            attributes = entity.get("attributes", {})
            signature = attributes.get("signature") if isinstance(attributes, dict) else None
            location = entity["location"]
            display = signature or f"{entity['kind']} {entity['name']}"
            lines.append(
                f"{display} [{location['start_line']}-{location['end_line']}]"
            )
        chunks.append(
            Chunk(
                path=str(file["path"]),
                text="\n".join(lines),
                meta={
                    "artifact": "codegraph_file_symbols",
                    "schema_version": "code-relations/v2",
                    "file_id": file["file_id"],
                    "source": "codegraph-sqlite",
                },
            )
        )
    return chunks


def _compact_digest(
    project: str,
    files: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> str:
    names = {entity["entity_id"]: entity["name"] for entity in entities}
    lines = [
        f"# {project}",
        f"{len(files)} files | {len(entities)} entities | {len(edges)} edges",
    ]
    for file in files:
        file_entities = [
            entity for entity in entities if entity.get("file_id") == file["file_id"]
        ]
        if not file_entities:
            continue
        symbols = ", ".join(
            f"{entity['kind']} {entity['name']}" for entity in file_entities
        )
        lines.append(f"- {file['path']}: {symbols}")
    if edges:
        lines.append("## Relations")
        for edge in edges:
            source = names.get(edge["from_entity_id"], edge["from_entity_id"])
            target = names.get(edge["to_entity_id"], edge["to_entity_id"])
            lines.append(f"- {edge['kind']}: {source} -> {target}")
    return "\n".join(lines)
