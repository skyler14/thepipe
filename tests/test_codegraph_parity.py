from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from thepipe.analyzer.integration import build_code_relations_json_payload
from thepipe.codegraph.integration import process_codegraph
from thepipe.scraper import scrape_directory


REPO_ROOT = Path(__file__).parents[1]
DIST_CODEGRAPH = REPO_ROOT / "dist" / "codegraph"
LOCAL_SIDECAR = Path("/private/tmp/thepipe-codegraph-e2e-bin/codebase-memory-mcp")
LOCAL_LIBRARY = Path("/private/tmp/thepipe-codegraph-e2e-lib/libthepipe_codegraph.dylib")


@dataclass(frozen=True)
class CodeFixture:
    name: str
    files: dict[str, str]
    expected_codegraph_gap: str | None = None


FIXTURES = [
    CodeFixture(
        name="python_imports_calls_classes",
        files={
            "app.py": (
                "from lib import greet\n\n"
                "class Runner:\n"
                "    def run(self, name: str) -> str:\n"
                "        return greet(name)\n\n"
                "def main(name: str) -> str:\n"
                "    runner = Runner()\n"
                "    return runner.run(name)\n"
            ),
            "lib.py": (
                "def greet(name: str) -> str:\n"
                "    return f'hello {name}'\n"
            ),
        },
    ),
    CodeFixture(
        name="typescript_react_wrappers",
        expected_codegraph_gap=(
            "native codegraph does not yet expose React memo/forwardRef wrapper "
            "component names that old thepipe extracts"
        ),
        files={
            "card.tsx": (
                "import React, { memo } from 'react';\n\n"
                "type Props = { href: string };\n"
                "export const Card = memo(function Card(props: Props) {\n"
                "  return <div>{props.href}</div>;\n"
                "});\n"
                "export const Link = React.forwardRef<HTMLAnchorElement, Props>(\n"
                "  (props, ref) => <a ref={ref} href={props.href} />\n"
                ");\n"
            ),
        },
    ),
    CodeFixture(
        name="rust_impl_methods",
        files={
            "src/lib.rs": (
                "pub struct Greeter;\n\n"
                "impl Greeter {\n"
                "    pub fn greet(&self, name: &str) -> String {\n"
                "        format!(\"hello {name}\")\n"
                "    }\n"
                "}\n\n"
                "pub fn main() -> String {\n"
                "    Greeter.greet(\"world\")\n"
                "}\n"
            ),
        },
    ),
    CodeFixture(
        name="go_package_functions",
        files={
            "main.go": (
                "package main\n\n"
                "func greet(name string) string {\n"
                "    return \"hello \" + name\n"
                "}\n\n"
                "func main() {\n"
                "    _ = greet(\"world\")\n"
                "}\n"
            ),
        },
    ),
    CodeFixture(
        name="ruby_php_mixed",
        files={
            "worker.rb": (
                "class Worker\n"
                "  def perform(job)\n"
                "    job.call\n"
                "  end\n"
                "end\n"
            ),
            "handler.php": (
                "<?php\n"
                "class Handler {\n"
                "  public function handle($request) {\n"
                "    return process_request($request);\n"
                "  }\n"
                "}\n"
                "function process_request($request) { return $request; }\n"
            ),
        },
    ),
]


def _write_fixture(root: Path, fixture: CodeFixture) -> None:
    for relative, content in fixture.files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=thepipe",
            "-c",
            "user.email=tests@thepipe.local",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )


def _original_payload(root: Path) -> dict[str, Any]:
    chunks = scrape_directory(str(root), options={"code_relations": "map"})
    return build_code_relations_json_payload(chunks, "map", str(root))


def _codegraph_payload(root: Path, implementation: str, artifact: Path) -> dict[str, Any]:
    if implementation == "sidecar":
        options = {
            "codegraph_binary": str(artifact),
            "codegraph_index_mode": "fast",
            "codegraph_refresh": True,
        }
    elif implementation == "shared-library":
        options = {
            "codegraph_library": str(artifact),
            "codegraph_index_mode": "fast",
            "codegraph_refresh": True,
        }
    else:
        raise AssertionError(f"unexpected implementation: {implementation}")
    chunks = process_codegraph(root, options=options)
    return chunks[0].meta["code_relations_payload"]


def _artifact_path(implementation: str, tmp_path: Path) -> Path | None:
    env_name = {
        "sidecar": "THEPIPE_CODEGRAPH_E2E_BINARY",
        "shared-library": "THEPIPE_CODEGRAPH_E2E_LIBRARY",
    }[implementation]
    if os.environ.get(env_name):
        return Path(os.environ[env_name])
    local = LOCAL_SIDECAR if implementation == "sidecar" else LOCAL_LIBRARY
    if local.exists():
        return local
    archive = _latest_archive(implementation)
    if archive is None:
        return None
    extract_dir = tmp_path / f"{implementation}-artifact"
    extract_dir.mkdir()
    with tarfile.open(archive, "r:*") as bundle:
        bundle.extractall(extract_dir, filter="data")
    name = (
        "codebase-memory-mcp"
        if implementation == "sidecar"
        else _shared_library_name()
    )
    artifact = extract_dir / name
    if artifact.exists():
        artifact.chmod(0o755)
        return artifact
    return None


def _latest_archive(implementation: str) -> Path | None:
    if implementation == "sidecar":
        pattern = "codebase-memory-mcp-*.tar.gz"
    else:
        pattern = "libthepipe-codegraph-*.tar.gz"
    archives = sorted(DIST_CODEGRAPH.glob(pattern), key=lambda path: path.stat().st_mtime)
    return archives[-1] if archives else None


def _shared_library_name() -> str:
    if os.name == "nt":
        return "thepipe_codegraph.dll"
    if os.uname().sysname == "Darwin":
        return "libthepipe_codegraph.dylib"
    return "libthepipe_codegraph.so"


def _file_paths(payload: dict[str, Any]) -> set[str]:
    return {
        str(file["path"])
        for file in payload.get("files", [])
        if isinstance(file, dict) and file.get("path")
    }


def _symbol_names(payload: dict[str, Any]) -> set[str]:
    ignored = {"", "top", "__init__", "__main__"}
    names: set[str] = set()
    for entity in payload.get("entities", []):
        if not isinstance(entity, dict):
            continue
        kind = str(entity.get("kind", "")).lower()
        if kind not in {"class", "function", "method", "interface", "enum", "type"}:
            continue
        name = str(entity.get("name", ""))
        if _is_comparable_symbol_name(name, ignored):
            names.add(name)
    return names


def _is_comparable_symbol_name(name: str, ignored: set[str]) -> bool:
    if name in ignored or name.startswith("<"):
        return False
    if " " in name:
        return False
    if len(name) == 1 and name.islower():
        return False
    if name in {"job"}:
        return False
    return True


def _call_edges(payload: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(edge.get("from_entity_id")), str(edge.get("to_entity_id")))
        for edge in payload.get("edges", [])
        if isinstance(edge, dict) and str(edge.get("kind", "")).lower() in {"calls", "call"}
    }


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture.name)
def test_original_code_relations_v1_baseline_is_non_empty(
    tmp_path: Path,
    fixture: CodeFixture,
) -> None:
    repo = tmp_path / fixture.name
    repo.mkdir()
    _write_fixture(repo, fixture)

    payload = _original_payload(repo)

    assert payload["schema_version"] == "code-relations/v1"
    assert set(fixture.files).issubset(_file_paths(payload))
    assert _symbol_names(payload)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture.name)
@pytest.mark.parametrize("implementation", ["sidecar", "shared-library"])
def test_codegraph_backends_cover_original_code_relations_symbols(
    tmp_path: Path,
    fixture: CodeFixture,
    implementation: str,
) -> None:
    artifact = _artifact_path(implementation, tmp_path)
    if artifact is None:
        pytest.skip(f"{implementation} artifact is not available")
    if implementation == "sidecar" and not shutil.which(str(artifact)) and not artifact.exists():
        pytest.skip("sidecar artifact is not executable")

    repo = tmp_path / fixture.name
    repo.mkdir()
    _write_fixture(repo, fixture)
    original = _original_payload(repo)
    native = _codegraph_payload(repo, implementation, artifact)

    assert native["schema_version"] == "code-relations/v2"
    assert set(fixture.files).issubset(_file_paths(native))
    if fixture.expected_codegraph_gap:
        pytest.xfail(fixture.expected_codegraph_gap)
    assert _symbol_names(original).issubset(_symbol_names(native))
    assert len(native.get("entities", [])) >= len(_symbol_names(original))
    assert "graph" in native


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture.name)
def test_sidecar_and_shared_library_emit_equivalent_codegraph_contract(
    tmp_path: Path,
    fixture: CodeFixture,
) -> None:
    sidecar = _artifact_path("sidecar", tmp_path)
    shared = _artifact_path("shared-library", tmp_path)
    if sidecar is None or shared is None:
        pytest.skip("both sidecar and shared-library artifacts are required")

    repo = tmp_path / fixture.name
    repo.mkdir()
    _write_fixture(repo, fixture)

    sidecar_payload = _codegraph_payload(repo, "sidecar", sidecar)
    shared_payload = _codegraph_payload(repo, "shared-library", shared)

    assert _file_paths(sidecar_payload) == _file_paths(shared_payload)
    assert _symbol_names(sidecar_payload) == _symbol_names(shared_payload)
    assert len(_call_edges(shared_payload)) >= len(_call_edges(sidecar_payload))
