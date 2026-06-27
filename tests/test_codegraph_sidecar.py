from __future__ import annotations

import json
from pathlib import Path

import pytest

from thepipe.codegraph.sidecar import (
    GRAMMAR_ARTIFACT_POLICY,
    PINNED_UPSTREAM_COMMIT,
    UPSTREAM_REPO,
    SidecarBackend,
    SidecarError,
    sidecar_from_env,
)


def _fake_sidecar(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "codegraph-sidecar"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"{body}\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | 0o111)
    return script


def test_sidecar_unwraps_mcp_text_json(tmp_path: Path) -> None:
    binary = _fake_sidecar(
        tmp_path,
        "assert sys.argv[1:4] == ['cli', '--json', 'index_repository']\n"
        "payload = json.loads(sys.argv[4])\n"
        "print(json.dumps({'content': [{'type': 'text', 'text': json.dumps({'ok': True, 'repo': payload['path']})}]}))",
    )

    result = SidecarBackend(binary).call("index_repository", {"path": "repo"})

    assert result == {"ok": True, "repo": "repo"}


def test_sidecar_errors_when_binary_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing-codegraph"

    with pytest.raises(SidecarError, match="not found"):
        SidecarBackend(missing).call("search_graph", {"query": "main"})


def test_sidecar_errors_on_mcp_error_envelope(tmp_path: Path) -> None:
    binary = _fake_sidecar(
        tmp_path,
        "print(json.dumps({'isError': True, 'content': [{'type': 'text', 'text': 'bad query'}]}))",
    )

    with pytest.raises(SidecarError, match="bad query"):
        SidecarBackend(binary).call("query_graph", {"query": "MATCH bad"})


def test_native_policy_keeps_raw_generated_grammars_out_of_the_python_repo() -> None:
    assert "compiled" in GRAMMAR_ARTIFACT_POLICY
    assert "raw generated grammar" in GRAMMAR_ARTIFACT_POLICY
    assert UPSTREAM_REPO.startswith("https://")
    assert len(PINNED_UPSTREAM_COMMIT) == 40


def test_native_source_lock_matches_python_provenance_constants() -> None:
    lock_path = Path(__file__).parents[1] / "native" / "codegraph" / "upstream.lock"
    lock = dict(
        line.split("=", 1)
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )

    assert lock["UPSTREAM_REPO"] == UPSTREAM_REPO
    assert lock["UPSTREAM_COMMIT"] == PINNED_UPSTREAM_COMMIT


def test_sidecar_from_env_is_opt_in(tmp_path: Path) -> None:
    binary = _fake_sidecar(tmp_path, "print(json.dumps({'content': []}))")

    assert sidecar_from_env({}) is None
    assert sidecar_from_env({"THEPIPE_CODEGRAPH_BINARY": str(binary)}).binary == binary


def test_sidecar_passes_isolated_cache_directory(tmp_path: Path) -> None:
    binary = _fake_sidecar(
        tmp_path,
        "import os\n"
        "print(json.dumps({'content': [{'type': 'text', 'text': json.dumps("
        "{'cache': os.environ.get('CBM_CACHE_DIR')})}]}))",
    )
    cache_dir = tmp_path / "repo-cache"

    result = SidecarBackend(binary, cache_dir=cache_dir).call("list_projects")

    assert result == {"cache": str(cache_dir)}
    assert cache_dir.is_dir()


def test_sidecar_times_out_instead_of_hanging(tmp_path: Path) -> None:
    binary = _fake_sidecar(tmp_path, "import time\ntime.sleep(1)")

    with pytest.raises(SidecarError, match="timed out"):
        SidecarBackend(binary, timeout=0.01).call("index_status", {"project": "demo"})


def test_sidecar_reports_malformed_mcp_envelope(tmp_path: Path) -> None:
    binary = _fake_sidecar(tmp_path, "print(json.dumps(['not', 'an', 'object']))")

    with pytest.raises(SidecarError, match="envelope"):
        SidecarBackend(binary).call("list_projects")


def test_sidecar_can_enforce_runtime_version(tmp_path: Path) -> None:
    binary = _fake_sidecar(
        tmp_path,
        "if '--version' in sys.argv:\n"
        "    print('codebase-memory-mcp 0.10.0')\n"
        "else:\n"
        "    print(json.dumps({'content': []}))",
    )
    backend = SidecarBackend(binary)

    assert backend.version() == "0.10.0"
    backend.require_version("0.10.0")

    with pytest.raises(SidecarError, match="requires.*0.9.0"):
        backend.require_version("0.9.0")


def test_sidecar_rejects_non_executable_file(tmp_path: Path) -> None:
    binary = tmp_path / "codegraph-sidecar"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(SidecarError, match="not executable"):
        SidecarBackend(binary).call("list_projects")
