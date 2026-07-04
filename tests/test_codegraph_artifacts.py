from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from thepipe.codegraph.artifacts import (
    ArtifactError,
    default_binary_name,
    default_binary_path,
    default_library_path,
    install_archive,
    install_shared_library_archive,
    install_sidecar_archive,
    sha256_file,
)


def _binary_archive(tmp_path: Path, body: bytes) -> Path:
    archive = tmp_path / "codegraph.tar.gz"
    info = tarfile.TarInfo("codebase-memory-mcp")
    info.mode = 0o755
    info.size = len(body)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.addfile(info, io.BytesIO(body))
    return archive


def _named_archive(tmp_path: Path, name: str, body: bytes) -> Path:
    archive = tmp_path / f"{name}.tar.gz"
    info = tarfile.TarInfo(name)
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


def test_install_sidecar_archive_uses_versioned_cache_path(tmp_path: Path) -> None:
    archive = _binary_archive(
        tmp_path,
        b"#!/bin/sh\nprintf 'codebase-memory-mcp 0.10.0\\n'\n",
    )
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()

    installed = install_sidecar_archive(
        archive,
        expected_sha256=expected,
        required_version="0.10.0",
        install_dir=tmp_path / "cache-bin",
    )

    assert installed == tmp_path / "cache-bin" / "codebase-memory-mcp-0.10.0"
    assert installed.is_file()
    assert default_binary_path(version="0.10.0", install_dir=tmp_path) == (
        tmp_path / "codebase-memory-mcp-0.10.0"
    )


def test_default_binary_path_preserves_windows_exe_suffix(tmp_path: Path) -> None:
    assert default_binary_path(
        version="0.10.0",
        install_dir=tmp_path,
        binary_name="codebase-memory-mcp.exe",
    ) == tmp_path / "codebase-memory-mcp-0.10.0.exe"
    assert default_binary_name() in {
        "codebase-memory-mcp",
        "codebase-memory-mcp.exe",
    }


def test_install_shared_library_archive_uses_versioned_cache_path(tmp_path: Path) -> None:
    archive = _named_archive(tmp_path, "libthepipe_codegraph.dylib", b"dylib")
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()

    installed = install_shared_library_archive(
        archive,
        expected_sha256=expected,
        required_version="0.10.0",
        install_dir=tmp_path / "cache-lib",
        library_name="libthepipe_codegraph.dylib",
    )

    assert installed == tmp_path / "cache-lib" / "libthepipe_codegraph-0.10.0.dylib"
    assert installed.read_bytes() == b"dylib"
    assert default_library_path(
        version="0.10.0",
        install_dir=tmp_path,
        library_name="libthepipe_codegraph.dylib",
    ) == tmp_path / "libthepipe_codegraph-0.10.0.dylib"
