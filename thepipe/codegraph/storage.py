from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_DB_RELATIVE_PATH = Path(".thepipe") / "codegraph" / "codegraph.sqlite"


@dataclass(frozen=True)
class CodegraphDeployment:
    repo_root: Path
    db_path: Path
    kind: str = "project"


def project_db_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / PROJECT_DB_RELATIVE_PATH


def master_registry_path(*, home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".thepipe" / "codegraph" / "registry.json"


def recommended_gitignore_entries() -> list[str]:
    return [
        ".thepipe/codegraph/*.sqlite*",
        ".thepipe/codegraph/*.db*",
        ".thepipe/codegraph/tmp/",
    ]


def discover_project_deployment(repo_root: str | Path) -> CodegraphDeployment | None:
    root = Path(repo_root)
    db_path = project_db_path(root)
    if not db_path.exists():
        return None
    return CodegraphDeployment(repo_root=root, db_path=db_path)
