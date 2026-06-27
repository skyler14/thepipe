from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from thepipe.core import Chunk

from .artifacts import install_sidecar_archive
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
            if options.get("codegraph_refresh", True):
                client.index_repository(
                    root,
                    mode=str(options.get("codegraph_index_mode", "fast")),
                    persistence=bool(options.get("codegraph_persistence", False)),
                )
            artifacts = client.load_artifacts(root)
        else:
            artifacts = _load_detected_artifacts(root)
    finally:
        if owned_backend:
            close = getattr(backend, "close", None)
            if close:
                close()
    return _chunks_with_summary(artifacts)


def _backend_from_options(
    root: Path, options: dict[str, Any]
) -> CodeGraphBackend | None:
    library = options.get("codegraph_library") or os.environ.get(
        "THEPIPE_CODEGRAPH_LIBRARY"
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
    if library:
        return SharedLibraryBackend(library, cache_dir=cache_dir)
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
            "codegraph_binary/codegraph_library/codegraph_archive"
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
