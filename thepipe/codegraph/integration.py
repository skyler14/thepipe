from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from thepipe.core import Chunk

from .access import CodegraphGraph
from .artifacts import install_shared_library_archive, install_sidecar_archive
from .client import CodeGraphBackend, CodegraphClient
from .database import CodegraphDatabase
from .outputs import CodegraphArtifacts, build_database_artifacts
from .sharedlib import SharedLibraryBackend
from .sidecar import PINNED_RUNTIME_VERSION, SidecarBackend
from .storage import (
    MasterRegistry,
    discover_project_deployment,
    project_cache_dir,
)


def process_codegraph(
    repo_root: str | Path,
    *,
    options: dict[str, Any],
    backend: CodeGraphBackend | None = None,
) -> list[Chunk]:
    """Run or load graph-native code relations through the normal Chunk API."""
    root = Path(repo_root).resolve()
    owned_backend = False
    if backend is None:
        backend = _backend_from_options(root, options)
        owned_backend = backend is not None

    registry_path = options.get("codegraph_registry")
    registry = MasterRegistry(registry_path) if registry_path else MasterRegistry()
    try:
        if backend is not None:
            client = CodegraphClient(
                backend,
                registry=registry,
                git_exclude=bool(options.get("codegraph_git_exclude", True)),
            )
            if _should_refresh(root, options):
                client.index_repository(
                    root,
                    mode=str(options.get("codegraph_index_mode", "fast")),
                    persistence=bool(options.get("codegraph_persistence", False)),
                )
            if _action(options) != "emit":
                return [_action_chunk(root, options)]
            artifacts = client.load_artifacts(root)
        else:
            if _action(options) != "emit":
                return [_action_chunk(root, options)]
            artifacts = _load_detected_artifacts(root)
    finally:
        if owned_backend:
            close = getattr(backend, "close", None)
            if close:
                close()
    return _chunks_with_summary(artifacts)


def _action(options: dict[str, Any]) -> str:
    return str(options.get("codegraph_action", "emit"))


def _should_refresh(root: Path, options: dict[str, Any]) -> bool:
    if "codegraph_refresh" in options:
        return bool(options["codegraph_refresh"])
    if _action(options) == "emit":
        return True
    return discover_project_deployment(root) is None


def _action_chunk(root: Path, options: dict[str, Any]) -> Chunk:
    payload = _run_graph_action(root, options)
    return Chunk(
        path="codegraph-action.json",
        text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
        meta={
            "artifact": "codegraph_action",
            "schema_version": "thepipe-codegraph-action/v1",
            "source": "codegraph-sqlite",
            "action": payload["action"],
        },
    )


def _run_graph_action(root: Path, options: dict[str, Any]) -> dict[str, Any]:
    action = _action(options)
    with CodegraphGraph.open_repo(root) as graph:
        if action == "summary":
            result: Any = graph.summary()
        elif action == "files":
            result = graph.files()[: int(options.get("codegraph_limit", 200))]
        elif action == "entities":
            result = graph.find_entities(
                query=options.get("codegraph_query"),
                kind=options.get("codegraph_kind"),
                file_path=options.get("codegraph_file"),
                qualified_name=options.get("codegraph_qualified_name"),
                limit=int(options.get("codegraph_limit", 50)),
            )
        elif action == "edges":
            result = graph.edges()[: int(options.get("codegraph_limit", 200))]
        elif action == "neighbors":
            entity = options.get("codegraph_entity")
            if entity is None:
                raise RuntimeError(
                    "codegraph_action='neighbors' requires codegraph_entity"
                )
            edge_types = options.get("codegraph_edge_types")
            if isinstance(edge_types, str):
                edge_types = [edge_types]
            result = graph.neighbors(
                entity,
                direction=str(options.get("codegraph_direction", "both")),
                depth=int(options.get("codegraph_depth", 1)),
                edge_types=edge_types,
                limit=int(options.get("codegraph_limit", 200)),
            )
        elif action == "sql":
            sql = options.get("codegraph_sql")
            if not sql:
                raise RuntimeError("codegraph_action='sql' requires codegraph_sql")
            result = graph.query_sql(
                str(sql),
                options.get("codegraph_sql_params"),
                max_rows=int(options.get("codegraph_limit", 200)),
            )
        else:
            raise RuntimeError(f"unsupported codegraph_action: {action}")
    if _compact_actions(options):
        result = _compact_result(result)
    return {
        "schema_version": "thepipe-codegraph-action/v1",
        "source": "codegraph-sqlite",
        "repo_root": str(root),
        "project": graph.project,
        "action": action,
        "result": result,
    }


def _compact_actions(options: dict[str, Any]) -> bool:
    if "codegraph_verbose" in options:
        return not bool(options["codegraph_verbose"])
    if "codegraph_compact" in options:
        return bool(options["codegraph_compact"])
    return True


def _compact_result(value: Any) -> Any:
    if isinstance(value, list):
        return [_compact_result(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _compact_result(item)
            for key, item in value.items()
            if key != "attributes"
        }
    return value


def _backend_from_options(
    root: Path, options: dict[str, Any]
) -> CodeGraphBackend | None:
    library = options.get("codegraph_library") or os.environ.get(
        "THEPIPE_CODEGRAPH_LIBRARY"
    )
    library_archive = options.get("codegraph_library_archive") or os.environ.get(
        "THEPIPE_CODEGRAPH_LIBRARY_ARCHIVE"
    )
    library_sha256 = options.get("codegraph_library_sha256") or os.environ.get(
        "THEPIPE_CODEGRAPH_LIBRARY_SHA256"
    )
    binary = options.get("codegraph_binary") or os.environ.get(
        "THEPIPE_CODEGRAPH_BINARY"
    )
    archive = options.get("codegraph_archive") or os.environ.get(
        "THEPIPE_CODEGRAPH_ARCHIVE"
    )
    archive_sha256 = options.get("codegraph_sha256") or os.environ.get(
        "THEPIPE_CODEGRAPH_SHA256"
    )
    cache_dir = project_cache_dir(root)
    quiet = bool(options.get("codegraph_quiet", True))
    if library:
        return SharedLibraryBackend(library, cache_dir=cache_dir, quiet=quiet)
    if library_archive:
        if not library_sha256:
            raise RuntimeError(
                "codegraph_library_archive requires codegraph_library_sha256"
            )
        required_version = str(
            options.get("codegraph_required_version")
            or os.environ.get("THEPIPE_CODEGRAPH_REQUIRED_VERSION")
            or PINNED_RUNTIME_VERSION
        )
        installed = install_shared_library_archive(
            library_archive,
            expected_sha256=str(library_sha256),
            required_version=required_version,
            install_dir=options.get("codegraph_install_dir")
            or os.environ.get("THEPIPE_CODEGRAPH_INSTALL_DIR"),
            library_name=options.get("codegraph_library_name"),
        )
        backend = SharedLibraryBackend(installed, cache_dir=cache_dir, quiet=quiet)
        actual_version = backend.version()
        if actual_version != required_version:
            backend.close()
            raise RuntimeError(
                f"codegraph shared library requires version {required_version}; "
                f"found {actual_version}"
            )
        return backend
    if binary:
        return SidecarBackend(
            binary,
            cache_dir=cache_dir,
            timeout=float(options.get("codegraph_timeout", 300)),
        )
    if archive:
        if not archive_sha256:
            raise RuntimeError("codegraph_archive requires codegraph_sha256")
        required_version = str(
            options.get("codegraph_required_version")
            or os.environ.get("THEPIPE_CODEGRAPH_REQUIRED_VERSION")
            or PINNED_RUNTIME_VERSION
        )
        installed = install_sidecar_archive(
            archive,
            expected_sha256=str(archive_sha256),
            required_version=required_version,
            install_dir=options.get("codegraph_install_dir")
            or os.environ.get("THEPIPE_CODEGRAPH_INSTALL_DIR"),
            binary_name=str(options.get("codegraph_binary_name", "codebase-memory-mcp")),
        )
        return SidecarBackend(
            installed,
            cache_dir=cache_dir,
            timeout=float(options.get("codegraph_timeout", 300)),
        )
    return None


def _load_detected_artifacts(root: Path) -> CodegraphArtifacts:
    deployment = discover_project_deployment(root)
    if deployment is None:
        raise RuntimeError(
            "graph mode needs an existing deployment or explicit "
            "codegraph_binary/codegraph_library/codegraph_archive/"
            "codegraph_library_archive"
        )
    with CodegraphDatabase(deployment.db_path) as database:
        database.validate_schema()
        return build_database_artifacts(
            database,
            deployment.project_name,
            repo_root=str(root),
        )


def _chunks_with_summary(artifacts: CodegraphArtifacts) -> list[Chunk]:
    summary = Chunk(
        path="__summary__",
        text=artifacts.digest,
        meta={
            "artifact": "codegraph_summary",
            "schema_version": artifacts.payload["schema_version"],
            "source": artifacts.payload["source"],
            "code_relations_payload": artifacts.payload,
        },
    )
    return [summary, *artifacts.chunks]
