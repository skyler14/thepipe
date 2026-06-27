from __future__ import annotations

from pathlib import Path

from thepipe.codegraph.storage import (
    PROJECT_DB_RELATIVE_PATH,
    discover_project_deployment,
    master_registry_path,
    project_db_path,
    recommended_gitignore_entries,
)


def test_project_db_path_is_hidden_and_git_ignorable(tmp_path: Path) -> None:
    assert project_db_path(tmp_path) == tmp_path / PROJECT_DB_RELATIVE_PATH
    assert ".thepipe/codegraph/*.sqlite*" in recommended_gitignore_entries()


def test_discover_project_deployment_is_agent_detectable(tmp_path: Path) -> None:
    db = project_db_path(tmp_path)
    db.parent.mkdir(parents=True)
    db.write_bytes(b"SQLite format 3\0")

    deployment = discover_project_deployment(tmp_path)

    assert deployment is not None
    assert deployment.repo_root == tmp_path
    assert deployment.db_path == db
    assert deployment.kind == "project"


def test_discover_project_deployment_returns_none_when_absent(tmp_path: Path) -> None:
    assert discover_project_deployment(tmp_path) is None


def test_master_registry_path_is_outside_repos_by_default(tmp_path: Path) -> None:
    assert master_registry_path(home=tmp_path) == tmp_path / ".thepipe" / "codegraph" / "registry.json"
