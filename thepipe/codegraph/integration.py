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
    CodegraphDeployment,
    MasterRegistry,
    discover_project_deployment,
    native_project_name,
    project_cache_dir,
)

LOCAL_GRAPH_ACTIONS = frozenset(
    {"summary", "files", "entities", "edges", "neighbors", "sql"}
)
NATIVE_GRAPH_ACTIONS = frozenset(
    {
        "index_repository",
        "index",
        "search_graph",
        "query_graph",
        "trace_path",
        "get_code_snippet",
        "get_graph_schema",
        "get_architecture",
        "search_code",
        "list_projects",
        "index_status",
        "delete_project",
        "detect_changes",
        "manage_adr",
        "ingest_traces",
    }
)
PROJECT_MANAGEMENT_ACTIONS = frozenset(
    {"list_projects", "index_status", "delete_project"}
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
                return [_action_chunk(root, options, client=client)]
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
    action = _action(options)
    if action == "emit":
        return True
    if action in PROJECT_MANAGEMENT_ACTIONS or action in {"index_repository", "index"}:
        return False
    return discover_project_deployment(root) is None


def _action_chunk(
    root: Path, options: dict[str, Any], *, client: CodegraphClient | None = None
) -> Chunk:
    payload = _run_graph_action(root, options, client=client)
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


def _run_graph_action(
    root: Path, options: dict[str, Any], *, client: CodegraphClient | None = None
) -> dict[str, Any]:
    action = _action(options)
    if action in NATIVE_GRAPH_ACTIONS:
        if client is None:
            raise RuntimeError(
                f"codegraph_action='{action}' requires codegraph_binary, "
                "codegraph_library, codegraph_archive, or codegraph_library_archive"
            )
        result = _run_native_graph_action(root, options, client)
        if _compact_actions(options):
            result = _compact_result(result)
        return {
            "schema_version": "thepipe-codegraph-action/v1",
            "source": "codegraph-native",
            "repo_root": str(root),
            "project": _project_for_action(root, options),
            "action": "index_repository" if action == "index" else action,
            "result": result,
        }
    if action not in LOCAL_GRAPH_ACTIONS:
        raise RuntimeError(f"unsupported codegraph_action: {action}")
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
            min_confidence = options.get("codegraph_min_confidence", 0.5)
            max_transit_degree = options.get("codegraph_max_transit_degree", 25)
            result = graph.neighbors(
                entity,
                direction=str(options.get("codegraph_direction", "both")),
                depth=int(options.get("codegraph_depth", 1)),
                edge_types=edge_types,
                min_confidence=(
                    None if min_confidence is None else float(min_confidence)
                ),
                max_transit_degree=(
                    None
                    if max_transit_degree is None
                    else int(max_transit_degree)
                ),
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


def _run_native_graph_action(
    root: Path, options: dict[str, Any], client: CodegraphClient
) -> Any:
    action = _action(options)
    project = _project_for_action(root, options)
    if action in {"index_repository", "index"}:
        artifacts = client.index_repository(
            root,
            mode=str(options.get("codegraph_index_mode", "fast")),
            persistence=bool(options.get("codegraph_persistence", False)),
            target_projects=_as_optional_list(options.get("codegraph_target_projects")),
        )
        return artifacts.payload
    if action == "list_projects":
        return client.list_projects()
    if action == "index_status":
        return client.index_status(_require_project(project, action))
    if action == "delete_project":
        return client.delete_project(_require_project(project, action))
    if action == "search_graph":
        return client.search_graph(
            _require_project(project, action),
            query=_optional_str(options.get("codegraph_query")),
            label=_optional_str(options.get("codegraph_kind")),
            name_pattern=_optional_str(options.get("codegraph_name_pattern")),
            qn_pattern=_optional_str(options.get("codegraph_qn_pattern")),
            file_pattern=_optional_str(
                options.get("codegraph_file_pattern", options.get("codegraph_file"))
            ),
            relationship=_optional_str(options.get("codegraph_relationship")),
            semantic_query=_as_optional_list(options.get("codegraph_semantic_query")),
            limit=int(options.get("codegraph_limit", 200)),
            offset=int(options.get("codegraph_offset", 0)),
            min_degree=_optional_int(options.get("codegraph_min_degree")),
            max_degree=_optional_int(options.get("codegraph_max_degree")),
            exclude_entry_points=_optional_bool(
                options.get("codegraph_exclude_entry_points")
            ),
            include_connected=_optional_bool(options.get("codegraph_include_connected")),
        )
    if action == "query_graph":
        query = options.get("codegraph_cypher", options.get("codegraph_query"))
        if not query:
            raise RuntimeError("codegraph_action='query_graph' requires codegraph_cypher")
        return client.query_graph(
            _require_project(project, action),
            str(query),
            max_rows=_optional_int(options.get("codegraph_limit")),
        )
    if action == "trace_path":
        entity = options.get("codegraph_entity", options.get("codegraph_function"))
        if not entity:
            raise RuntimeError("codegraph_action='trace_path' requires codegraph_entity")
        return client.trace_path(
            _require_project(project, action),
            str(entity),
            direction=str(options.get("codegraph_direction", "both")),
            depth=int(options.get("codegraph_depth", 3)),
            mode=str(options.get("codegraph_trace_mode", "calls")),
            edge_types=_as_optional_list(options.get("codegraph_edge_types")),
            risk_labels=bool(options.get("codegraph_risk_labels", False)),
            include_tests=bool(options.get("codegraph_include_tests", False)),
            parameter_name=_optional_str(options.get("codegraph_parameter_name")),
        )
    if action == "get_code_snippet":
        qualified_name = options.get(
            "codegraph_qualified_name", options.get("codegraph_entity")
        )
        if not qualified_name:
            raise RuntimeError(
                "codegraph_action='get_code_snippet' requires codegraph_qualified_name"
            )
        return client.get_code_snippet(
            _require_project(project, action),
            str(qualified_name),
            include_neighbors=bool(options.get("codegraph_include_neighbors", False)),
        )
    if action == "get_graph_schema":
        return client.get_graph_schema(_require_project(project, action))
    if action == "get_architecture":
        return client.get_architecture(
            _require_project(project, action),
            path=_optional_str(options.get("codegraph_path", options.get("codegraph_file"))),
            aspects=_as_optional_list(options.get("codegraph_aspects")),
        )
    if action == "search_code":
        pattern = options.get("codegraph_pattern", options.get("codegraph_query"))
        if not pattern:
            raise RuntimeError("codegraph_action='search_code' requires codegraph_pattern")
        return client.search_code(
            _require_project(project, action),
            str(pattern),
            file_pattern=_optional_str(options.get("codegraph_file_pattern")),
            path_filter=_optional_str(options.get("codegraph_path_filter")),
            mode=str(options.get("codegraph_search_mode", "compact")),
            context=_optional_int(options.get("codegraph_context")),
            regex=bool(options.get("codegraph_regex", False)),
            limit=int(options.get("codegraph_limit", 10)),
        )
    if action == "detect_changes":
        return client.detect_changes(
            _require_project(project, action),
            scope=str(options.get("codegraph_scope", "symbols")),
            depth=int(options.get("codegraph_depth", 2)),
            base_branch=str(options.get("codegraph_base_branch", "main")),
            since=_optional_str(options.get("codegraph_since")),
        )
    if action == "manage_adr":
        return client.manage_adr(
            _require_project(project, action),
            mode=str(options.get("codegraph_adr_mode", options.get("codegraph_mode", "get"))),
            content=_optional_str(options.get("codegraph_content")),
            sections=_as_optional_list(options.get("codegraph_sections")),
        )
    if action == "ingest_traces":
        traces = options.get("codegraph_traces")
        if traces is None:
            raise RuntimeError("codegraph_action='ingest_traces' requires codegraph_traces")
        if not isinstance(traces, list):
            raise RuntimeError("codegraph_traces must be a list of trace objects")
        return client.ingest_traces(_require_project(project, action), traces)
    raise RuntimeError(f"unsupported codegraph_action: {action}")


def _project_for_action(root: Path, options: dict[str, Any]) -> str:
    explicit = options.get("codegraph_project")
    if explicit:
        return str(explicit)
    deployment: CodegraphDeployment | None = discover_project_deployment(root)
    if deployment is not None and deployment.project_name:
        return deployment.project_name
    return native_project_name(root)


def _require_project(project: str, action: str) -> str:
    if not project:
        raise RuntimeError(f"codegraph_action='{action}' requires codegraph_project")
    return project


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _as_optional_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


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
