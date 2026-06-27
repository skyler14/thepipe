from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from thepipe.codegraph.artifacts import ArtifactError, install_archive, sha256_file


def _binary_archive(tmp_path: Path, body: bytes) -> Path:
    archive = tmp_path / "codegraph.tar.gz"
    info = tarfile.TarInfo("codebase-memory-mcp")
    info.mode = 0o755
    info.size = len(body)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.addfile(info, io.BytesIO(body))
    return archive


def test_install_archive_verifies_hash_and_installs_only_binary(tmp_path: Path) -> None:
    archive = _binary_archive(
        tmp_path,
        b"#!/bin/sh\nprintf 'codebase-memory-mcp 0.10.0\\n'\n",
    )
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    destination = tmp_path / "bin" / "codebase-memory-mcp"

    installed = install_archive(
        archive,
        destination,
        expected_sha256=expected,
        required_version="0.10.0",
    )

    assert installed == destination
    assert installed.stat().st_mode & 0o111
    assert sha256_file(archive) == expected
    assert list(destination.parent.iterdir()) == [destination]


def test_install_archive_rejects_checksum_mismatch_without_partial_install(
    tmp_path: Path,
) -> None:
    archive = _binary_archive(tmp_path, b"#!/bin/sh\nexit 0\n")
    destination = tmp_path / "bin" / "codebase-memory-mcp"

    with pytest.raises(ArtifactError, match="checksum"):
        install_archive(
            archive,
            destination,
            expected_sha256="0" * 64,
        )

    assert not destination.exists()


def test_install_archive_requires_expected_binary_member(tmp_path: Path) -> None:
    archive = tmp_path / "wrong.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("../../unexpected")
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))

    with pytest.raises(ArtifactError, match="does not contain"):
        install_archive(
            archive,
            tmp_path / "bin" / "codebase-memory-mcp",
            expected_sha256=sha256_file(archive),
        )
