from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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
            "operations": [],
            "join_candidates": [],
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
