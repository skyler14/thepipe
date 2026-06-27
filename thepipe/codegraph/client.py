from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, Sequence

from .database import CodegraphDatabase
from .outputs import (
    CodegraphArtifacts,
    build_codegraph_artifacts,
    build_database_artifacts,
)
from .storage import (
    CodegraphDeployment,
    MasterRegistry,
    database_size,
    discover_project_deployment,
    ensure_git_excluded,
    write_manifest,
)

INDEX_MODES = frozenset({"full", "moderate", "fast", "cross-repo-intelligence"})


class CodeGraphBackend(Protocol):
    def call(self, tool: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def _payload(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class CodegraphClient:
    """Stable Python facade over the codebase-memory MCP tool contract."""

    def __init__(
        self,
        backend: CodeGraphBackend,
        *,
        registry: MasterRegistry | None = None,
        git_exclude: bool = True,
    ) -> None:
        self.backend = backend
        self.registry = registry or MasterRegistry()
        self.git_exclude = git_exclude

    def index_repository(
        self,
        repo_root: str | Path,
        *,
        mode: str = "full",
        persistence: bool = False,
        target_projects: Sequence[str] | None = None,
    ) -> CodegraphArtifacts:
        if mode not in INDEX_MODES:
            raise ValueError(f"unsupported index mode: {mode}")
        if mode == "cross-repo-intelligence" and not target_projects:
            raise ValueError("target_projects is required for cross-repo-intelligence mode")

        root = Path(repo_root).resolve()
        native = self.backend.call(
            "index_repository",
            _payload(
                repo_path=str(root),
                mode=mode,
                persistence=persistence,
                target_projects=list(target_projects) if target_projects is not None else None,
            ),
        )
        self._record_local_deployment(root, native)
        return build_codegraph_artifacts(native, mode="map", repo_root=str(root))

    def list_projects(self) -> dict[str, Any]:
        return self.backend.call("list_projects", {})

    def index_status(self, project: str) -> dict[str, Any]:
        return self.backend.call("index_status", {"project": project})

    def delete_project(self, project: str) -> dict[str, Any]:
        return self.backend.call("delete_project", {"project": project})

    def search_graph(
        self,
        project: str,
        *,
        query: str | None = None,
        label: str | None = None,
        name_pattern: str | None = None,
        qn_pattern: str | None = None,
        file_pattern: str | None = None,
        relationship: str | None = None,
        semantic_query: Sequence[str] | None = None,
        limit: int = 200,
        offset: int = 0,
        min_degree: int | None = None,
        max_degree: int | None = None,
        exclude_entry_points: bool | None = None,
        include_connected: bool | None = None,
    ) -> dict[str, Any]:
        if isinstance(semantic_query, (str, bytes)):
            raise TypeError("semantic_query must be a sequence of strings")
        return self.backend.call(
            "search_graph",
            _payload(
                project=project,
                query=query,
                label=label,
                name_pattern=name_pattern,
                qn_pattern=qn_pattern,
                file_pattern=file_pattern,
                relationship=relationship,
                semantic_query=list(semantic_query) if semantic_query is not None else None,
                limit=limit,
                offset=offset,
                min_degree=min_degree,
                max_degree=max_degree,
                exclude_entry_points=exclude_entry_points,
                include_connected=include_connected,
            ),
        )

    def query_graph(
        self, project: str, query: str, *, max_rows: int | None = None
    ) -> dict[str, Any]:
        return self.backend.call(
            "query_graph",
            _payload(project=project, query=query, max_rows=max_rows),
        )

    def trace_path(
        self,
        project: str,
        function_name: str,
        *,
        direction: str = "both",
        depth: int = 3,
        mode: str = "calls",
        edge_types: Sequence[str] | None = None,
        risk_labels: bool = False,
        include_tests: bool = False,
        parameter_name: str | None = None,
    ) -> dict[str, Any]:
        return self.backend.call(
            "trace_path",
            _payload(
                project=project,
                function_name=function_name,
                direction=direction,
                depth=depth,
                mode=mode,
                edge_types=list(edge_types) if edge_types is not None else None,
                risk_labels=risk_labels,
                include_tests=include_tests,
                parameter_name=parameter_name,
            ),
        )

    def get_code_snippet(
        self, project: str, qualified_name: str, *, include_neighbors: bool = False
    ) -> dict[str, Any]:
        return self.backend.call(
            "get_code_snippet",
            {
                "project": project,
                "qualified_name": qualified_name,
                "include_neighbors": include_neighbors,
            },
        )

    def get_graph_schema(self, project: str) -> dict[str, Any]:
        return self.backend.call("get_graph_schema", {"project": project})

    def get_architecture(
        self,
        project: str,
        *,
        path: str | None = None,
        aspects: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self.backend.call(
            "get_architecture",
            _payload(
                project=project,
                path=path,
                aspects=list(aspects) if aspects is not None else None,
            ),
        )

    def search_code(
        self,
        project: str,
        pattern: str,
        *,
        file_pattern: str | None = None,
        path_filter: str | None = None,
        mode: str = "compact",
        context: int | None = None,
        regex: bool = False,
        limit: int = 10,
    ) -> dict[str, Any]:
        return self.backend.call(
            "search_code",
            _payload(
                project=project,
                pattern=pattern,
                file_pattern=file_pattern,
                path_filter=path_filter,
                mode=mode,
                context=context,
                regex=regex,
                limit=limit,
            ),
        )

    def detect_changes(
        self,
        project: str,
        *,
        scope: str = "symbols",
        depth: int = 2,
        base_branch: str = "main",
        since: str | None = None,
    ) -> dict[str, Any]:
        return self.backend.call(
            "detect_changes",
            _payload(
                project=project,
                scope=scope,
                depth=depth,
                base_branch=base_branch,
                since=since,
            ),
        )

    def manage_adr(
        self,
        project: str,
        *,
        mode: str = "get",
        content: str | None = None,
        sections: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return self.backend.call(
            "manage_adr",
            _payload(
                project=project,
                mode=mode,
                content=content,
                sections=list(sections) if sections is not None else None,
            ),
        )

    def ingest_traces(
        self, project: str, traces: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        return self.backend.call(
            "ingest_traces",
            {"project": project, "traces": list(traces)},
        )

    def load_artifacts(self, repo_root: str | Path) -> CodegraphArtifacts:
        root = Path(repo_root).resolve()
        deployment = discover_project_deployment(root)
        if deployment is None:
            raise FileNotFoundError(f"no codegraph deployment found for {root}")
        with CodegraphDatabase(deployment.db_path) as database:
            database.validate_schema()
            return build_database_artifacts(
                database,
                deployment.project_name,
                repo_root=str(root),
            )

    def _record_local_deployment(
        self, repo_root: Path, native: dict[str, Any]
    ) -> None:
        cache_dir = getattr(self.backend, "cache_dir", None)
        project = native.get("project")
        if cache_dir is None or not isinstance(project, str):
            return
        db_path = Path(cache_dir) / f"{project}.db"
        if not db_path.is_file():
            return
        with CodegraphDatabase(db_path) as database:
            schema_fingerprint = database.validate_schema()
            database_summary = database.summary(project)
        artifact = repo_root / ".codebase-memory" / "graph.db.zst"
        deployment = CodegraphDeployment(
            repo_root=repo_root,
            db_path=db_path,
            project_name=project,
            backend_kind="sidecar",
            backend_version=str(native.get("backend_version", "")),
            schema_fingerprint=schema_fingerprint,
            artifact_path=artifact if artifact.is_file() else None,
            size_bytes=database_size(db_path),
            file_count=int(database_summary["files"]),
            entity_count=int(native.get("nodes", 0)),
            edge_count=int(native.get("edges", 0)),
        )
        if self.git_exclude:
            ensure_git_excluded(repo_root)
        write_manifest(deployment)
        self.registry.upsert(deployment, status=str(native.get("status", "ready")))
