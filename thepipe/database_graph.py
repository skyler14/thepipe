from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .structured_graph import extract_citation_anchors, extract_record_shapes


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


class DatabaseGraphLedger:
    def __init__(self, path: Optional[str] = None, *, persist: bool = True):
        self.path = Path(path) if path else None
        self.persist = persist and self.path is not None
        self.data: Dict[str, Any] = {
            "schema_version": "database-graph-ledger/v1",
            "dataset_groups": [],
            "sources": [],
            "operations": [],
            "insights": [],
            "join_candidates": [],
            "citation_anchors": [],
            "record_shapes": [],
        }
        if self.persist and self.path and self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def find_operation(self, *, source_fingerprint: str, query_fingerprint: str) -> Optional[Dict[str, Any]]:
        for operation in reversed(self.data.get("operations", [])):
            if (
                operation.get("source_fingerprint") == source_fingerprint
                and operation.get("query_fingerprint") == query_fingerprint
                and operation.get("status") == "ok"
            ):
                return operation
        return None

    def record_operation(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        operation = dict(operation)
        operation.setdefault("operation_id", "op:" + fingerprint(operation)[:16])
        operation.setdefault("captured_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.data.setdefault("operations", []).append(operation)
        self.save()
        return operation

    def record_sources(self, group_name: str, sources: List[Dict[str, Any]]) -> None:
        source_ids = [str(source.get("source_id", "source")) for source in sources]
        groups = self.data.setdefault("dataset_groups", [])
        group = next((existing for existing in groups if existing.get("name") == group_name), None)
        if group is None:
            group = {"name": group_name, "sources": []}
            groups.append(group)
        for source_id in source_ids:
            if source_id not in group.setdefault("sources", []):
                group["sources"].append(source_id)
        known_sources = self.data.setdefault("sources", [])
        for source in sources:
            existing_index = next(
                (
                    index
                    for index, known in enumerate(known_sources)
                    if str(known.get("source_id", "source")) == str(source.get("source_id", "source"))
                ),
                None,
            )
            if existing_index is None:
                known_sources.append(source)
            else:
                known_sources[existing_index] = source
        group_sources = [
            source
            for source in known_sources
            if str(source.get("source_id", "source")) in set(group.get("sources", []))
        ]
        for candidate in find_join_candidates(group_sources):
            if candidate not in self.data.setdefault("join_candidates", []):
                self.data["join_candidates"].append(candidate)
        self.save()

    def record_structured_source(self, path: str | Path) -> None:
        source_id = str(path)
        for anchor in extract_citation_anchors(path):
            row = {"source_id": source_id, **anchor}
            if row not in self.data.setdefault("citation_anchors", []):
                self.data["citation_anchors"].append(row)
        for shape in extract_record_shapes(path):
            row = {"source_id": source_id, **shape}
            if row not in self.data.setdefault("record_shapes", []):
                self.data["record_shapes"].append(row)
        self.save()

    def pin_insight(self, summary: str, *, evidence_operation_id: Optional[str] = None) -> Dict[str, Any]:
        insight = {
            "insight_id": "insight:" + fingerprint({"summary": summary, "evidence": evidence_operation_id})[:16],
            "summary": summary,
            "pinned": True,
            "revoked": False,
            "evidence_operation_id": evidence_operation_id,
        }
        insights = self.data.setdefault("insights", [])
        for existing in insights:
            if existing["insight_id"] == insight["insight_id"]:
                return existing
        insights.append(insight)
        self.save()
        return insight

    def revoke_insight(self, insight_id: str) -> None:
        for insight in self.data.setdefault("insights", []):
            if insight.get("insight_id") == insight_id:
                insight["revoked"] = True
        self.save()

    def purge_unpinned_operations(self) -> None:
        pinned_evidence = {
            insight.get("evidence_operation_id")
            for insight in self.data.get("insights", [])
            if insight.get("pinned") and not insight.get("revoked")
        }
        for operation in self.data.get("operations", []):
            if operation.get("operation_id") in pinned_evidence:
                self.data.setdefault("omissions", []).append(
                    {
                        "kind": "purged-operation",
                        "operation_id": operation.get("operation_id"),
                        "reason": "operation detail purged; pinned insight retained",
                    }
                )
        self.data["operations"] = [
            operation for operation in self.data.get("operations", []) if operation.get("pinned")
        ]
        self.save()

    def to_property_graph(self) -> Dict[str, Any]:
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: Dict[str, Dict[str, Any]] = {}

        def add_node(node_id: str, labels: List[str], properties: Dict[str, Any]) -> None:
            nodes.setdefault(
                node_id,
                {"id": node_id, "labels": labels, "properties": dict(properties)},
            )

        def add_edge(edge_id: str, edge_type: str, from_id: str, to_id: str, properties: Optional[Dict[str, Any]] = None) -> None:
            edges.setdefault(
                edge_id,
                {
                    "id": edge_id,
                    "type": edge_type,
                    "from": from_id,
                    "to": to_id,
                    "properties": dict(properties or {}),
                },
            )

        for group in self.data.get("dataset_groups", []):
            group_id = f"dataset_group:{group.get('name', 'default')}"
            add_node(group_id, ["DatasetGroup"], dict(group))
            for source_id in group.get("sources", []):
                source_node_id = f"source:{source_id}"
                add_node(source_node_id, ["Source"], {"source_id": source_id})
                add_edge(f"{group_id}->HAS_SOURCE->{source_node_id}", "HAS_SOURCE", group_id, source_node_id)

        for source in self.data.get("sources", []):
            source_id = str(source.get("source_id", source.get("display_name", "source")))
            source_node_id = f"source:{source_id}"
            source_props = {key: value for key, value in source.items() if key != "tables"}
            source_props.setdefault("source_id", source_id)
            add_node(source_node_id, ["Source"], source_props)
            for table in source.get("tables", []):
                table_name = str(table.get("name", "table"))
                table_node_id = f"table:{source_id}.{table_name}"
                add_node(
                    table_node_id,
                    ["Table"],
                    {
                        "source_id": source_id,
                        "name": table_name,
                        "qualified_name": f"{source_id}.{table_name}",
                    },
                )
                add_edge(f"{source_node_id}->HAS_TABLE->{table_node_id}", "HAS_TABLE", source_node_id, table_node_id)
                for column in table.get("columns", []):
                    column_name = str(column)
                    column_node_id = f"column:{source_id}.{table_name}.{column_name}"
                    add_node(
                        column_node_id,
                        ["Column"],
                        {
                            "source_id": source_id,
                            "table": table_name,
                            "name": column_name,
                            "qualified_name": f"{source_id}.{table_name}.{column_name}",
                        },
                    )
                    add_edge(f"{table_node_id}->HAS_COLUMN->{column_node_id}", "HAS_COLUMN", table_node_id, column_node_id)

        for operation in self.data.get("operations", []):
            operation_id = str(operation.get("operation_id", "operation:" + fingerprint(operation)[:16]))
            add_node(operation_id, ["Operation"], dict(operation))

        for insight in self.data.get("insights", []):
            if insight.get("revoked"):
                continue
            insight_id = str(insight.get("insight_id", "insight:" + fingerprint(insight)[:16]))
            add_node(insight_id, ["Insight"], dict(insight))
            evidence_id = insight.get("evidence_operation_id")
            if evidence_id:
                add_edge(f"{insight_id}->USED_AS_EVIDENCE->{evidence_id}", "USED_AS_EVIDENCE", insight_id, str(evidence_id))

        for anchor in self.data.get("citation_anchors", []):
            anchor_id = "citation_anchor:" + fingerprint(anchor)[:16]
            add_node(anchor_id, ["CitationAnchor"], dict(anchor))

        for shape in self.data.get("record_shapes", []):
            shape_id = "record_shape:" + fingerprint(shape)[:16]
            add_node(shape_id, ["RecordShape"], dict(shape))

        for candidate in self.data.get("join_candidates", []):
            left_id = "column:" + str(candidate.get("left"))
            right_id = "column:" + str(candidate.get("right"))
            if left_id not in nodes:
                add_node(left_id, ["Column"], _column_properties(str(candidate.get("left", ""))))
            if right_id not in nodes:
                add_node(right_id, ["Column"], _column_properties(str(candidate.get("right", ""))))
            edge_id = "join_candidate:" + fingerprint(candidate)[:16]
            add_edge(edge_id, "CROSS_SOURCE_JOIN", left_id, right_id, dict(candidate))

        return {
            "schema_version": "thepipe-property-graph/v1",
            "graph_id": "database-graph:" + fingerprint(self.data)[:16],
            "source": self.data.get("schema_version", "database-graph-ledger/v1"),
            "nodes": sorted(nodes.values(), key=lambda node: node["id"]),
            "edges": sorted(edges.values(), key=lambda edge: edge["id"]),
        }

    def query(self, cypher: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return _query_property_graph(self.to_property_graph(), cypher, limit=limit)

    def save(self) -> None:
        if not self.persist or not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        _ensure_git_exclude()


def default_graph_path(source: Any) -> str:
    root = Path.cwd() / ".thepipe" / "database" / "graph"
    return str(root / (fingerprint(str(source))[:16] + ".json"))


def _ensure_git_exclude() -> None:
    exclude = Path.cwd() / ".git" / "info" / "exclude"
    if not exclude.parent.exists():
        return
    entry = ".thepipe/database/"
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if entry in current.splitlines():
        return
    suffix = "" if current.endswith("\n") or not current else "\n"
    exclude.write_text(current + suffix + entry + "\n", encoding="utf-8")


def graph_enabled(options: Dict[str, Any]) -> bool:
    return bool(options.get("database_graph") in {"auto", "snapshot", "query", "graph"})


def ledger_from_options(source: Any, options: Dict[str, Any]) -> Optional[DatabaseGraphLedger]:
    if not graph_enabled(options):
        return None
    persist = bool(options.get("database_graph_persist", True))
    store = str(options.get("database_graph_store", "repo")).lower()
    if store == "memory":
        persist = False
    return DatabaseGraphLedger(options.get("database_graph_path") or default_graph_path(source), persist=persist)


def _extract_limit(cypher: str) -> Optional[int]:
    parts = cypher.lower().rsplit("limit", 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[1].strip().split()[0])
    except (IndexError, ValueError):
        return None


_NODE_RE = re.compile(r"\(([A-Za-z_]\w*)\s*(?::\s*([A-Za-z_]\w*))?\)")
_REL_RE = re.compile(r"-\[([A-Za-z_]\w*)?\s*(?::\s*([A-Za-z_]\w*))?\]->")


def _column_properties(qualified_name: str) -> Dict[str, Any]:
    parts = qualified_name.split(".")
    props: Dict[str, Any] = {"qualified_name": qualified_name}
    if len(parts) >= 3:
        props.update({"source_id": parts[0], "table": parts[1], "name": parts[-1]})
    elif parts:
        props["name"] = parts[-1]
    return props


def _query_property_graph(graph: Dict[str, Any], cypher: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    parsed = _parse_read_cypher(cypher)
    rows = _match_pattern(graph, parsed["pattern"])
    where_clause = parsed.get("where")
    if where_clause:
        rows = [row for row in rows if _evaluate_where(row, where_clause)]
    projected = [_project_row(row, parsed["return_items"]) for row in rows]
    effective_limit = limit if limit is not None else parsed.get("limit")
    return projected[:effective_limit] if effective_limit is not None else projected


def _parse_read_cypher(cypher: str) -> Dict[str, Any]:
    text = cypher.strip().rstrip(";")
    if not text.lower().startswith("match "):
        raise ValueError(f"unsupported database graph query: {cypher}")
    match = re.match(
        r"(?is)^match\s+(?P<pattern>.*?)\s+return\s+(?P<returns>.*?)(?:\s+limit\s+(?P<limit>\d+))?$",
        text,
    )
    if not match:
        raise ValueError(f"unsupported database graph query: {cypher}")

    pattern = match.group("pattern").strip()
    where_clause = None
    where_match = re.match(r"(?is)^(?P<pattern>.*?)\s+where\s+(?P<where>.*)$", pattern)
    if where_match:
        pattern = where_match.group("pattern").strip()
        where_clause = where_match.group("where").strip()

    return_items = [item.strip() for item in match.group("returns").split(",") if item.strip()]
    if not return_items:
        raise ValueError(f"unsupported database graph query: {cypher}")

    return {
        "pattern": pattern,
        "where": where_clause,
        "return_items": return_items,
        "limit": int(match.group("limit")) if match.group("limit") else None,
    }


def _match_pattern(graph: Dict[str, Any], pattern: str) -> List[Dict[str, Dict[str, Any]]]:
    node_tokens = list(_NODE_RE.finditer(pattern))
    rel_tokens = list(_REL_RE.finditer(pattern))
    if not node_tokens or len(rel_tokens) not in {0, len(node_tokens) - 1}:
        raise ValueError(f"unsupported database graph query pattern: {pattern}")

    node_specs = [(token.group(1), token.group(2)) for token in node_tokens]
    rel_specs = [(token.group(1), token.group(2)) for token in rel_tokens]
    nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
    candidate_nodes = [
        [node for node in graph.get("nodes", []) if label is None or label in node.get("labels", [])]
        for _, label in node_specs
    ]
    rows: List[Dict[str, Dict[str, Any]]] = []

    def walk(index: int, bindings: Dict[str, Dict[str, Any]]) -> None:
        if index == len(node_specs):
            rows.append(dict(bindings))
            return

        alias, _label = node_specs[index]
        for node in candidate_nodes[index]:
            if alias in bindings and bindings[alias]["id"] != node["id"]:
                continue
            if index > 0:
                prev_alias, _ = node_specs[index - 1]
                rel_alias, rel_type = rel_specs[index - 1]
                prev_node = bindings[prev_alias]
                matching_edges = [
                    edge
                    for edge in graph.get("edges", [])
                    if edge.get("from") == prev_node["id"]
                    and edge.get("to") == node["id"]
                    and (rel_type is None or edge.get("type") == rel_type)
                ]
                if not matching_edges:
                    continue
                for edge in matching_edges:
                    next_bindings = dict(bindings)
                    next_bindings[alias] = node
                    if rel_alias:
                        next_bindings[rel_alias] = _edge_as_binding(edge)
                    walk(index + 1, next_bindings)
            else:
                next_bindings = dict(bindings)
                next_bindings[alias] = node
                walk(index + 1, next_bindings)

    walk(0, {})
    return rows


def _edge_as_binding(edge: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": edge["id"],
        "labels": [edge["type"]],
        "properties": {"type": edge["type"], **edge.get("properties", {})},
    }


def _evaluate_where(row: Dict[str, Dict[str, Any]], clause: str) -> bool:
    contains = re.match(r'(?is)^([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s+contains\s+["\'](.*?)["\']$', clause.strip())
    if contains:
        value = _binding_property(row, contains.group(1), contains.group(2))
        return contains.group(3) in str(value or "")

    equals = re.match(r'(?is)^([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*(true|false|[0-9.]+|["\'].*?["\'])$', clause.strip())
    if equals:
        left = _binding_property(row, equals.group(1), equals.group(2))
        right = _parse_literal(equals.group(3))
        return left == right

    comparison = re.match(r'(?is)^([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*(>=|<=|>|<)\s*([0-9.]+)$', clause.strip())
    if comparison:
        left = _binding_property(row, comparison.group(1), comparison.group(2))
        right = float(comparison.group(4))
        try:
            left_number = float(left)
        except (TypeError, ValueError):
            return False
        op = comparison.group(3)
        return {
            ">=": left_number >= right,
            "<=": left_number <= right,
            ">": left_number > right,
            "<": left_number < right,
        }[op]

    raise ValueError(f"unsupported database graph query where clause: {clause}")


def _parse_literal(value: str) -> Any:
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        return value[1:-1]
    if "." in value:
        return float(value)
    return int(value)


def _project_row(row: Dict[str, Dict[str, Any]], return_items: List[str]) -> Dict[str, Any]:
    if len(return_items) == 1:
        item = return_items[0]
        if re.match(r"^[A-Za-z_]\w*$", item):
            return dict(row[item].get("properties", {}))

    projected: Dict[str, Any] = {}
    for item in return_items:
        alias_match = re.match(r"^([A-Za-z_]\w*)$", item)
        prop_match = re.match(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$", item)
        if alias_match:
            alias = alias_match.group(1)
            projected[alias] = dict(row[alias].get("properties", {}))
        elif prop_match:
            alias, prop = prop_match.groups()
            projected[item] = _binding_property(row, alias, prop)
        else:
            raise ValueError(f"unsupported database graph return item: {item}")
    return projected


def _binding_property(row: Dict[str, Dict[str, Any]], alias: str, prop: str) -> Any:
    if alias not in row:
        raise ValueError(f"unknown database graph alias: {alias}")
    return row[alias].get("properties", {}).get(prop)


def dataframe_records_json(result: Any) -> Optional[str]:
    if hasattr(result, "to_json"):
        return result.to_json(orient="records", indent=2)
    return None


def find_join_candidates(sources: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, List[tuple[str, str]]] = {}
    for source in sources:
        source_id = str(source.get("source_id", "source"))
        for table in source.get("tables", []):
            table_name = table.get("name", "table")
            for column in table.get("columns", []):
                seen.setdefault(str(column), []).append((source_id, f"{source_id}.{table_name}.{column}"))

    candidates: List[Dict[str, Any]] = []
    for column, refs in sorted(seen.items()):
        if len(refs) < 2:
            continue
        for index, (left_source, left) in enumerate(refs):
            for right_source, right in refs[index + 1:]:
                if left_source == right_source:
                    continue
                candidates.append(
                    {
                        "left": left,
                        "right": right,
                        "column": column,
                        "confidence": 0.8,
                        "evidence": "matching column name across sources",
                    }
                )
    return candidates
