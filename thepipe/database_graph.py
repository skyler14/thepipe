from __future__ import annotations

import hashlib
import json
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
        group = {"name": group_name, "sources": source_ids}
        groups = self.data.setdefault("dataset_groups", [])
        if group not in groups:
            groups.append(group)
        known_sources = self.data.setdefault("sources", [])
        for source in sources:
            if source not in known_sources:
                known_sources.append(source)
        for candidate in find_join_candidates(sources):
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

    def query(self, cypher: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        text = cypher.lower()
        if limit is None:
            limit = _extract_limit(cypher)
        if "operation" in text:
            rows = list(self.data.get("operations", []))
        elif "cross_source_join" in text or "joincandidate" in text:
            rows = list(self.data.get("join_candidates", []))
        elif "datasetgroup" in text:
            rows = list(self.data.get("dataset_groups", []))
        elif "citationanchor" in text:
            rows = list(self.data.get("citation_anchors", []))
        elif "recordshape" in text:
            rows = list(self.data.get("record_shapes", []))
        else:
            rows = []
        return rows[:limit] if limit is not None else rows

    def save(self) -> None:
        if not self.persist or not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")


def default_graph_path(source: Any) -> str:
    root = Path.cwd() / ".thepipe" / "database" / "graph"
    return str(root / (fingerprint(str(source))[:16] + ".json"))


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


def dataframe_records_json(result: Any) -> Optional[str]:
    if hasattr(result, "to_json"):
        return result.to_json(orient="records", indent=2)
    return None


def find_join_candidates(sources: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, List[str]] = {}
    for source in sources:
        source_id = source.get("source_id", "source")
        for table in source.get("tables", []):
            table_name = table.get("name", "table")
            for column in table.get("columns", []):
                seen.setdefault(str(column), []).append(f"{source_id}.{table_name}.{column}")

    candidates: List[Dict[str, Any]] = []
    for column, refs in sorted(seen.items()):
        if len(refs) < 2:
            continue
        for index, left in enumerate(refs):
            for right in refs[index + 1:]:
                left_source = left.split(".", 1)[0]
                right_source = right.split(".", 1)[0]
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
