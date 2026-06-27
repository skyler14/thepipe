from __future__ import annotations

import sqlite3
from pathlib import Path

from thepipe.codegraph.storage import (
    DEFAULT_GLOBAL_CAP_BYTES,
    DEFAULT_REPO_SOFT_CAP_BYTES,
    CodegraphDeployment,
    MasterRegistry,
    assess_database_size,
    database_size,
    discover_project_deployment,
    manifest_path,
    master_registry_path,
    native_project_name,
    project_cache_dir,
    project_db_path,
    prune_global_cache,
    read_manifest,
    recommended_gitignore_entries,
    write_manifest,
)


def test_project_storage_is_hidden_and_git_ignorable(tmp_path: Path) -> None:
    assert project_cache_dir(tmp_path) == tmp_path / ".thepipe" / "codegraph" / "cache"
    assert project_db_path(tmp_path) == (
        project_cache_dir(tmp_path) / f"{native_project_name(tmp_path)}.db"
    )
    assert ".thepipe/codegraph/cache/" in recommended_gitignore_entries()
    assert DEFAULT_GLOBAL_CAP_BYTES == 10 * 1024**3
    assert DEFAULT_REPO_SOFT_CAP_BYTES == 500 * 1024**2


def test_native_project_name_matches_donor_path_normalization() -> None:
    assert native_project_name("/Users/skyler/My Project") == "Users-skyler-My-Project"
    assert native_project_name("/") == "root"
    assert native_project_name("/tmp/a...b//@c") == "tmp-a.b-c"


def test_manifest_round_trips_deployment_metadata(tmp_path: Path) -> None:
    deployment = CodegraphDeployment(
        repo_root=tmp_path,
        db_path=project_db_path(tmp_path),
        project_name="demo",
        backend_kind="sidecar",
        backend_version="0.10.0",
        schema_fingerprint="abc123",
        entity_count=7,
        edge_count=4,
    )

    write_manifest(deployment)

    assert manifest_path(tmp_path).is_file()
    assert read_manifest(tmp_path) == deployment


def test_discover_project_deployment_uses_manifest_and_requires_database(
    tmp_path: Path,
) -> None:
    db = project_db_path(tmp_path)
    db.parent.mkdir(parents=True)
    db.write_bytes(b"SQLite format 3\0")
    expected = CodegraphDeployment(
        repo_root=tmp_path,
        db_path=db,
        project_name="demo",
        backend_kind="sidecar",
    )
    write_manifest(expected)

    assert discover_project_deployment(tmp_path) == expected

    db.unlink()
    assert discover_project_deployment(tmp_path) is None


def test_master_registry_is_pointer_only_sqlite_database(tmp_path: Path) -> None:
    path = master_registry_path(home=tmp_path)
    deployment = CodegraphDeployment(
        repo_root=tmp_path / "repo",
        db_path=tmp_path / "repo" / ".thepipe" / "codegraph" / "cache" / "repo.db",
        project_name="repo",
        backend_kind="sidecar",
        backend_version="0.10.0",
        size_bytes=1234,
        entity_count=10,
        edge_count=8,
    )
    registry = MasterRegistry(path)

    registry.upsert(deployment, last_head="deadbeef", status="ready")

    assert registry.list() == [deployment]
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"repos"}


def test_database_size_includes_wal_and_shm(tmp_path: Path) -> None:
    db = tmp_path / "demo.db"
    db.write_bytes(b"x" * 10)
    Path(f"{db}-wal").write_bytes(b"x" * 20)
    Path(f"{db}-shm").write_bytes(b"x" * 30)

    assert database_size(db) == 60
    assert assess_database_size(db, soft_cap_bytes=59).status == "oversize"
    assert assess_database_size(db, soft_cap_bytes=60).status == "ok"


def test_prune_global_cache_removes_oldest_database_family_only(tmp_path: Path) -> None:
    old = tmp_path / "old.db"
    old.write_bytes(b"x" * 80)
    Path(f"{old}-wal").write_bytes(b"x" * 20)
    new = tmp_path / "new.db"
    new.write_bytes(b"x" * 80)
    old.touch()
    new.touch()
    old_mtime = new.stat().st_mtime - 100
    old.chmod(0o600)
    import os

    os.utime(old, (old_mtime, old_mtime))

    result = prune_global_cache(tmp_path, cap_bytes=100)

    assert result.before_bytes == 180
    assert result.after_bytes == 80
    assert result.removed == (old,)
    assert not old.exists()
    assert not Path(f"{old}-wal").exists()
    assert new.exists()


def test_master_registry_path_lives_in_global_cache(tmp_path: Path) -> None:
    assert master_registry_path(home=tmp_path) == tmp_path / ".cache" / "thepipe" / "master.sqlite"
