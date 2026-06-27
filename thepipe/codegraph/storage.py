from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_CODEGRAPH_RELATIVE_DIR = Path(".thepipe") / "codegraph"
PROJECT_CACHE_RELATIVE_DIR = PROJECT_CODEGRAPH_RELATIVE_DIR / "cache"
PROJECT_MANIFEST_RELATIVE_PATH = PROJECT_CODEGRAPH_RELATIVE_DIR / "manifest.json"
DEFAULT_GLOBAL_CAP_BYTES = 10 * 1024**3
DEFAULT_REPO_SOFT_CAP_BYTES = 500 * 1024**2


@dataclass(frozen=True)
class CodegraphDeployment:
    repo_root: Path
    db_path: Path
    project_name: str = ""
    backend_kind: str = "sidecar"
    backend_version: str = ""
    schema_fingerprint: str = ""
    artifact_path: Path | None = None
    size_bytes: int = 0
    file_count: int = 0
    entity_count: int = 0
    edge_count: int = 0
    kind: str = "project"


@dataclass(frozen=True)
class SizeAssessment:
    size_bytes: int
    soft_cap_bytes: int
    status: str


@dataclass(frozen=True)
class PruneResult:
    before_bytes: int
    after_bytes: int
    removed: tuple[Path, ...]


def project_cache_dir(repo_root: str | Path) -> Path:
    return Path(repo_root) / PROJECT_CACHE_RELATIVE_DIR


def native_project_name(repo_root: str | Path) -> str:
    """Match cbm_project_name_from_path from the pinned donor source."""
    normalized: list[str] = []
    previous = ""
    for character in str(repo_root).replace("\\", "/"):
        safe = character.isascii() and (
            character.isalnum() or character in "._-"
        )
        value = character if safe else "-"
        if (value == "-" and previous == "-") or (value == "." and previous == "."):
            continue
        normalized.append(value)
        previous = value
    result = "".join(normalized).lstrip(".-").rstrip("-")
    return result or "root"


def project_db_path(repo_root: str | Path, project_name: str | None = None) -> Path:
    root = Path(repo_root)
    name = project_name or native_project_name(root)
    return project_cache_dir(root) / f"{name}.db"


def manifest_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / PROJECT_MANIFEST_RELATIVE_PATH


def master_registry_path(*, home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".cache" / "thepipe" / "master.sqlite"


def recommended_gitignore_entries() -> list[str]:
    return [
        ".thepipe/codegraph/cache/",
        ".thepipe/codegraph/*.sqlite*",
        ".thepipe/codegraph/*.db*",
        ".thepipe/codegraph/tmp/",
    ]


def ensure_git_excluded(repo_root: str | Path) -> bool:
    """Add the local graph cache to .git/info/exclude without touching tracked files."""
    root = Path(repo_root)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = root / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    entry = ".thepipe/codegraph/cache/"
    if entry in existing.splitlines():
        return True
    with exclude.open("a", encoding="utf-8") as output:
        if existing and not existing.endswith("\n"):
            output.write("\n")
        output.write(f"{entry}\n")
    return True


def write_manifest(deployment: CodegraphDeployment) -> Path:
    path = manifest_path(deployment.repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _deployment_json(deployment)
    data["schema_version"] = "thepipe-codegraph-manifest/v1"
    fd, temporary_name = tempfile.mkstemp(prefix=".manifest.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return path


def read_manifest(repo_root: str | Path) -> CodegraphDeployment | None:
    root = Path(repo_root)
    path = manifest_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("schema_version") != "thepipe-codegraph-manifest/v1":
        return None
    db_path = _resolve_stored_path(root, data.get("db_path"))
    if db_path is None:
        return None
    artifact_path = _resolve_stored_path(root, data.get("artifact_path"))
    return CodegraphDeployment(
        repo_root=root,
        db_path=db_path,
        project_name=str(data.get("project_name", "")),
        backend_kind=str(data.get("backend_kind", "sidecar")),
        backend_version=str(data.get("backend_version", "")),
        schema_fingerprint=str(data.get("schema_fingerprint", "")),
        artifact_path=artifact_path,
        size_bytes=int(data.get("size_bytes", 0)),
        file_count=int(data.get("file_count", 0)),
        entity_count=int(data.get("entity_count", 0)),
        edge_count=int(data.get("edge_count", 0)),
        kind=str(data.get("kind", "project")),
    )


def discover_project_deployment(repo_root: str | Path) -> CodegraphDeployment | None:
    root = Path(repo_root)
    deployment = read_manifest(root)
    if deployment is not None:
        return deployment if deployment.db_path.is_file() else None
    legacy = project_db_path(root)
    if not legacy.is_file():
        return None
    return CodegraphDeployment(
        repo_root=root,
        db_path=legacy,
        project_name=root.name,
        size_bytes=database_size(legacy),
    )


def database_size(db_path: str | Path) -> int:
    path = Path(db_path)
    return sum(
        candidate.stat().st_size
        for candidate in _database_family(path)
        if candidate.is_file()
    )


def assess_database_size(
    db_path: str | Path,
    *,
    soft_cap_bytes: int = DEFAULT_REPO_SOFT_CAP_BYTES,
) -> SizeAssessment:
    size = database_size(db_path)
    return SizeAssessment(
        size_bytes=size,
        soft_cap_bytes=soft_cap_bytes,
        status="oversize" if size > soft_cap_bytes else "ok",
    )


def prune_global_cache(
    cache_dir: str | Path,
    *,
    cap_bytes: int = DEFAULT_GLOBAL_CAP_BYTES,
    protected: Iterable[str | Path] = (),
) -> PruneResult:
    """Prune database families by LRU. Call only for explicitly global caches."""
    directory = Path(cache_dir)
    protected_paths = {Path(item).resolve() for item in protected}
    databases = [path for path in directory.glob("*.db") if path.is_file()]
    before = sum(database_size(path) for path in databases)
    current = before
    removed: list[Path] = []
    for database in sorted(databases, key=lambda path: path.stat().st_mtime):
        if current <= cap_bytes:
            break
        if database.resolve() in protected_paths:
            continue
        family_size = database_size(database)
        for member in _database_family(database):
            member.unlink(missing_ok=True)
        current -= family_size
        removed.append(database)
    return PruneResult(before_bytes=before, after_bytes=current, removed=tuple(removed))


class MasterRegistry:
    """Pointer-only system registry. Graph nodes and edges stay in project DBs."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else master_registry_path()

    def upsert(
        self,
        deployment: CodegraphDeployment,
        *,
        last_head: str = "",
        status: str = "ready",
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO repos (
                    root_path, db_path, artifact_path, project_name, backend_kind,
                    backend_version, schema_fingerprint, last_seen, last_head,
                    size_bytes, file_count, entity_count, edge_count, status, kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(root_path) DO UPDATE SET
                    db_path=excluded.db_path,
                    artifact_path=excluded.artifact_path,
                    project_name=excluded.project_name,
                    backend_kind=excluded.backend_kind,
                    backend_version=excluded.backend_version,
                    schema_fingerprint=excluded.schema_fingerprint,
                    last_seen=excluded.last_seen,
                    last_head=excluded.last_head,
                    size_bytes=excluded.size_bytes,
                    file_count=excluded.file_count,
                    entity_count=excluded.entity_count,
                    edge_count=excluded.edge_count,
                    status=excluded.status,
                    kind=excluded.kind
                """,
                (
                    str(deployment.repo_root),
                    str(deployment.db_path),
                    str(deployment.artifact_path) if deployment.artifact_path else None,
                    deployment.project_name,
                    deployment.backend_kind,
                    deployment.backend_version,
                    deployment.schema_fingerprint,
                    datetime.now(timezone.utc).isoformat(),
                    last_head,
                    deployment.size_bytes,
                    deployment.file_count,
                    deployment.entity_count,
                    deployment.edge_count,
                    status,
                    deployment.kind,
                ),
            )

    def list(self) -> list[CodegraphDeployment]:
        if not self.path.is_file():
            return []
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT root_path, db_path, project_name, backend_kind,
                       backend_version, schema_fingerprint, artifact_path,
                       size_bytes, file_count, entity_count, edge_count, kind
                FROM repos ORDER BY last_seen DESC
                """
            ).fetchall()
        return [
            CodegraphDeployment(
                repo_root=Path(row[0]),
                db_path=Path(row[1]),
                project_name=row[2],
                backend_kind=row[3],
                backend_version=row[4],
                schema_fingerprint=row[5],
                artifact_path=Path(row[6]) if row[6] else None,
                size_bytes=row[7],
                file_count=row[8],
                entity_count=row[9],
                edge_count=row[10],
                kind=row[11],
            )
            for row in rows
        ]

    def remove(self, repo_root: str | Path) -> bool:
        if not self.path.is_file():
            return False
        with sqlite3.connect(self.path) as connection:
            self._ensure_schema(connection)
            cursor = connection.execute(
                "DELETE FROM repos WHERE root_path = ?",
                (str(repo_root),),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS repos (
                root_path TEXT PRIMARY KEY,
                db_path TEXT NOT NULL,
                artifact_path TEXT,
                project_name TEXT NOT NULL,
                backend_kind TEXT NOT NULL,
                backend_version TEXT NOT NULL,
                schema_fingerprint TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_head TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                entity_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                kind TEXT NOT NULL
            )
            """
        )


def _deployment_json(deployment: CodegraphDeployment) -> dict[str, Any]:
    data = asdict(deployment)
    data.pop("repo_root")
    data["db_path"] = _portable_path(deployment.repo_root, deployment.db_path)
    data["artifact_path"] = (
        _portable_path(deployment.repo_root, deployment.artifact_path)
        if deployment.artifact_path is not None
        else None
    )
    return data


def _portable_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _resolve_stored_path(root: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def _database_family(path: Path) -> tuple[Path, Path, Path]:
    return path, Path(f"{path}-wal"), Path(f"{path}-shm")
