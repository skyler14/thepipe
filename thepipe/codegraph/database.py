from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

REQUIRED_COLUMNS = {
    "projects": {"name", "indexed_at", "root_path"},
    "file_hashes": {"project", "rel_path", "sha256", "mtime_ns", "size"},
    "nodes": {
        "id",
        "project",
        "label",
        "name",
        "qualified_name",
        "file_path",
        "start_line",
        "end_line",
        "properties",
    },
    "edges": {"id", "project", "source_id", "target_id", "type", "properties"},
    "project_summaries": {
        "project",
        "summary",
        "source_hash",
        "created_at",
        "updated_at",
    },
}


class SchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeRecord:
    id: int
    project: str
    label: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    properties: dict[str, Any]


@dataclass(frozen=True)
class EdgeRecord:
    id: int
    project: str
    source_id: int
    target_id: int
    type: str
    properties: dict[str, Any]


class CodegraphDatabase:
    """Read-only adapter over the pinned donor SQLite schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        uri = f"file:{quote(str(self.path.resolve()), safe='/')}?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only = ON")

    def __enter__(self) -> CodegraphDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def check_integrity(self) -> bool:
        row = self.connection.execute("PRAGMA quick_check").fetchone()
        return row is not None and row[0] == "ok"

    def validate_schema(self) -> str:
        missing: list[str] = []
        for table, required in REQUIRED_COLUMNS.items():
            columns = {
                row["name"]
                for row in self.connection.execute(
                    f"PRAGMA table_xinfo({_quote_identifier(table)})"
                )
            }
            absent = sorted(required - columns)
            if absent:
                missing.append(f"{table}({', '.join(absent)})")
        if missing:
            raise SchemaError("codegraph database missing schema: " + "; ".join(missing))
        return self.schema_fingerprint()

    def schema_fingerprint(self) -> str:
        rows = self.connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
            ORDER BY type, name
            """
        )
        canonical = "\n".join(
            f"{row['type']}:{row['name']}:{' '.join(row['sql'].split())}"
            for row in rows
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def summary(self, project: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT name, indexed_at, root_path FROM projects WHERE name = ?",
            (project,),
        ).fetchone()
        if row is None:
            raise KeyError(project)
        return {
            "project": row["name"],
            "root_path": row["root_path"],
            "indexed_at": row["indexed_at"],
            "files": self._count("file_hashes", project),
            "nodes": self._count("nodes", project),
            "edges": self._count("edges", project),
        }

    def nodes(self, project: str) -> list[NodeRecord]:
        rows = self.connection.execute(
            """
            SELECT id, project, label, name, qualified_name, file_path,
                   start_line, end_line, properties
            FROM nodes WHERE project = ? ORDER BY id
            """,
            (project,),
        )
        return [
            NodeRecord(
                id=row["id"],
                project=row["project"],
                label=row["label"],
                name=row["name"],
                qualified_name=row["qualified_name"],
                file_path=row["file_path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                properties=_json_object(row["properties"]),
            )
            for row in rows
        ]

    def edges(self, project: str) -> list[EdgeRecord]:
        rows = self.connection.execute(
            """
            SELECT id, project, source_id, target_id, type, properties
            FROM edges WHERE project = ? ORDER BY id
            """,
            (project,),
        )
        return [
            EdgeRecord(
                id=row["id"],
                project=row["project"],
                source_id=row["source_id"],
                target_id=row["target_id"],
                type=row["type"],
                properties=_json_object(row["properties"]),
            )
            for row in rows
        ]

    def file_hashes(self, project: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT rel_path, sha256, mtime_ns, size
                FROM file_hashes WHERE project = ? ORDER BY rel_path
                """,
                (project,),
            )
        ]

    def query_rows(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        max_rows: int = 200,
    ) -> list[dict[str, Any]]:
        """Run a bounded read-only SQL query against the graph database."""
        statement = sql.strip()
        if not statement:
            raise ValueError("sql is required")
        first = statement.split(None, 1)[0].lower()
        if first not in {"select", "with", "pragma"}:
            raise ValueError("only read-only SELECT/WITH/PRAGMA queries are supported")
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        rows = self.connection.execute(statement, tuple(params or ()))
        return [dict(row) for row in rows.fetchmany(max_rows)]

    def _count(self, table: str, project: str) -> int:
        row = self.connection.execute(
            f"SELECT count(*) FROM {_quote_identifier(table)} WHERE project = ?",
            (project,),
        ).fetchone()
        return int(row[0])


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {"raw": value}
    return decoded if isinstance(decoded, dict) else {"value": decoded}


def _quote_identifier(value: str) -> str:
    if value not in REQUIRED_COLUMNS:
        raise ValueError(f"unsupported table: {value}")
    return f'"{value}"'
