from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Iterable, Sequence

from .database import CodegraphDatabase, EdgeRecord, NodeRecord
from .storage import discover_project_deployment


class CodegraphAccessError(RuntimeError):
    pass


class CodegraphGraph:
    """Read-only convenience API for an installed repo-local codegraph DB."""

    def __init__(self, database: CodegraphDatabase, project: str) -> None:
        self.database = database
        self.project = project

    @classmethod
    def open_repo(cls, repo_root: str | Path) -> CodegraphGraph:
        deployment = discover_project_deployment(Path(repo_root).resolve())
        if deployment is None:
            raise CodegraphAccessError(f"no codegraph deployment found for {repo_root}")
        database = CodegraphDatabase(deployment.db_path)
        try:
            database.validate_schema()
        except Exception:
            database.close()
            raise
        return cls(database, deployment.project_name)

    def close(self) -> None:
        self.database.close()

    def __enter__(self) -> CodegraphGraph:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def summary(self) -> dict[str, Any]:
        return self.database.summary(self.project)

    def files(self) -> list[dict[str, Any]]:
        return self.database.file_hashes(self.project)

    def entities(self) -> list[dict[str, Any]]:
        return [_node_dict(node) for node in self.database.nodes(self.project)]

    def edges(self) -> list[dict[str, Any]]:
        return [_edge_dict(edge) for edge in self.database.edges(self.project)]

    def find_entities(
        self,
        *,
        query: str | None = None,
        kind: str | None = None,
        file_path: str | None = None,
        qualified_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query_lower = query.lower() if query else None
        results: list[dict[str, Any]] = []
        for node in self.database.nodes(self.project):
            if kind and node.label != kind:
                continue
            if file_path and file_path not in node.file_path:
                continue
            if qualified_name and qualified_name not in node.qualified_name:
                continue
            if query_lower and not _node_matches(node, query_lower):
                continue
            results.append(_node_dict(node))
            if len(results) >= limit:
                break
        return results

    def neighbors(
        self,
        entity: int | str,
        *,
        direction: str = "both",
        depth: int = 1,
        edge_types: Sequence[str] | None = None,
        min_confidence: float | None = None,
        max_transit_degree: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        if direction not in {"inbound", "outbound", "both"}:
            raise ValueError("direction must be inbound, outbound, or both")
        if depth < 0:
            raise ValueError("depth must be non-negative")
        if min_confidence is not None and not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if max_transit_degree is not None and max_transit_degree < 1:
            raise ValueError("max_transit_degree must be positive")
        if limit < 1:
            raise ValueError("limit must be positive")

        nodes = {node.id: node for node in self.database.nodes(self.project)}
        start = _resolve_entity(entity, nodes.values())
        allowed_types = set(edge_types or [])
        all_edges = [
            edge
            for edge in self.database.edges(self.project)
            if not allowed_types or edge.type in allowed_types
        ]
        by_source: dict[int, list[EdgeRecord]] = {}
        by_target: dict[int, list[EdgeRecord]] = {}
        for edge in all_edges:
            by_source.setdefault(edge.source_id, []).append(edge)
            by_target.setdefault(edge.target_id, []).append(edge)

        seen_nodes = {start.id}
        node_hops = {start.id: 0}
        seen_edges: dict[int, EdgeRecord] = {}
        filtered_edge_ids: set[int] = set()
        pruned_hubs: dict[int, dict[str, Any]] = {}
        queue: deque[tuple[int, int]] = deque([(start.id, 0)])
        while queue and len(seen_edges) < limit:
            node_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for edge in _candidate_edges(
                node_id,
                direction=direction,
                by_source=by_source,
                by_target=by_target,
            ):
                if _below_confidence(edge, min_confidence):
                    filtered_edge_ids.add(edge.id)
                    continue
                if edge.id not in seen_edges:
                    seen_edges[edge.id] = edge
                next_id = edge.target_id if edge.source_id == node_id else edge.source_id
                if next_id not in seen_nodes and next_id in nodes:
                    seen_nodes.add(next_id)
                    next_hop = current_depth + 1
                    node_hops[next_id] = next_hop
                    transit_degree = _candidate_degree(
                        next_id,
                        direction=direction,
                        by_source=by_source,
                        by_target=by_target,
                        min_confidence=min_confidence,
                    )
                    if (
                        max_transit_degree is not None
                        and next_hop < depth
                        and transit_degree > max_transit_degree
                    ):
                        node = nodes[next_id]
                        pruned_hubs[next_id] = {
                            "entity_id": f"native:{node.id}",
                            "name": node.name,
                            "qualified_name": node.qualified_name,
                            "degree": transit_degree,
                            "hop": next_hop,
                        }
                    else:
                        queue.append((next_id, next_hop))
                if len(seen_edges) >= limit:
                    break

        return {
            "project": self.project,
            "start": _node_dict(start),
            "nodes": [
                {**_node_dict(nodes[node_id]), "hop": node_hops[node_id]}
                for node_id in sorted(seen_nodes)
            ],
            "edges": [
                _edge_dict(edge)
                for edge in sorted(seen_edges.values(), key=lambda item: item.id)
            ],
            "depth": depth,
            "direction": direction,
            "filtered_edges": len(filtered_edge_ids),
            "pruned_hubs": [
                pruned_hubs[node_id] for node_id in sorted(pruned_hubs)
            ],
        }

    def query_sql(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        max_rows: int = 200,
    ) -> dict[str, Any]:
        return {
            "project": self.project,
            "rows": self.database.query_rows(sql, params, max_rows=max_rows),
            "max_rows": max_rows,
        }


def _node_matches(node: NodeRecord, query_lower: str) -> bool:
    return (
        query_lower in node.name.lower()
        or query_lower in node.qualified_name.lower()
        or query_lower in node.file_path.lower()
    )


def _resolve_entity(entity: int | str, nodes: Iterable[NodeRecord]) -> NodeRecord:
    entity_text = str(entity)
    if entity_text.startswith("native:"):
        entity_text = entity_text.split(":", 1)[1]
    candidates = list(nodes)
    if entity_text.isdigit():
        for node in candidates:
            if node.id == int(entity_text):
                return node
    for node in candidates:
        if entity_text == node.qualified_name:
            return node
    name_matches = [node for node in candidates if entity_text == node.name]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        qualified_names = ", ".join(
            sorted(node.qualified_name for node in name_matches)
        )
        raise CodegraphAccessError(
            f"ambiguous entity name {entity_text!r}; use a qualified name: "
            f"{qualified_names}"
        )
    raise KeyError(f"entity not found: {entity}")


def _candidate_edges(
    node_id: int,
    *,
    direction: str,
    by_source: dict[int, list[EdgeRecord]],
    by_target: dict[int, list[EdgeRecord]],
) -> Iterable[EdgeRecord]:
    if direction in {"outbound", "both"}:
        yield from by_source.get(node_id, [])
    if direction in {"inbound", "both"}:
        yield from by_target.get(node_id, [])


def _candidate_degree(
    node_id: int,
    *,
    direction: str,
    by_source: dict[int, list[EdgeRecord]],
    by_target: dict[int, list[EdgeRecord]],
    min_confidence: float | None,
) -> int:
    return len(
        {
            edge.id
            for edge in _candidate_edges(
                node_id,
                direction=direction,
                by_source=by_source,
                by_target=by_target,
            )
            if not _below_confidence(edge, min_confidence)
        }
    )


def _below_confidence(edge: EdgeRecord, min_confidence: float | None) -> bool:
    confidence = edge.properties.get("confidence")
    return (
        min_confidence is not None
        and isinstance(confidence, (int, float))
        and confidence < min_confidence
    )


def _node_dict(node: NodeRecord) -> dict[str, Any]:
    return {
        "entity_id": f"native:{node.id}",
        "native_id": node.id,
        "kind": node.label,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "path": node.file_path,
        "location": {
            "start_line": node.start_line,
            "end_line": node.end_line,
        },
        "attributes": node.properties,
    }


def _edge_dict(edge: EdgeRecord) -> dict[str, Any]:
    return {
        "edge_id": f"native:{edge.id}",
        "native_id": edge.id,
        "kind": edge.type,
        "from_entity_id": f"native:{edge.source_id}",
        "to_entity_id": f"native:{edge.target_id}",
        "attributes": edge.properties,
    }
