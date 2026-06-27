from __future__ import annotations

from pathlib import Path
from typing import Any

from .outputs import CodegraphArtifacts, build_codegraph_artifacts
from .storage import project_db_path


class CodegraphClient:
    def __init__(self, backend: Any) -> None:
        self.backend = backend

    def index_repository(self, repo_root: str | Path) -> CodegraphArtifacts:
        root = Path(repo_root)
        native = self.backend.call(
            "index_repository",
            {
                "path": str(root),
                "database_path": str(project_db_path(root)),
            },
        )
        return build_codegraph_artifacts(native, mode="map", repo_root=str(root))
