from __future__ import annotations

import json
from pathlib import Path

import pytest

from thepipe.codegraph.sidecar import (
    GRAMMAR_ARTIFACT_POLICY,
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


def test_sidecar_from_env_is_opt_in(tmp_path: Path) -> None:
    binary = _fake_sidecar(tmp_path, "print(json.dumps({'content': []}))")

    assert sidecar_from_env({}) is None
    assert sidecar_from_env({"THEPIPE_CODEGRAPH_BINARY": str(binary)}).binary == binary
