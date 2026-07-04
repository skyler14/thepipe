from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from .sidecar import SidecarBackend, SidecarError


class ArtifactError(RuntimeError):
    pass


def default_binary_name() -> str:
    return "codebase-memory-mcp.exe" if os.name == "nt" else "codebase-memory-mcp"


def default_binary_path(
    *,
    version: str,
    install_dir: str | Path | None = None,
    binary_name: str | None = None,
) -> Path:
    """Return the default sidecar install path for a pinned runtime."""
    root = (
        Path(install_dir)
        if install_dir is not None
        else Path.home() / ".cache" / "thepipe" / "bin"
    )
    name = binary_name or default_binary_name()
    path = Path(name)
    return root / f"{path.stem}-{version}{path.suffix}"


def default_library_name() -> str:
    if sys.platform == "darwin":
        return "libthepipe_codegraph.dylib"
    if os.name == "nt":
        return "thepipe_codegraph.dll"
    return "libthepipe_codegraph.so"


def default_library_path(
    *,
    version: str,
    install_dir: str | Path | None = None,
    library_name: str | None = None,
) -> Path:
    """Return the default shared-library install path for a pinned runtime."""
    root = (
        Path(install_dir)
        if install_dir is not None
        else Path.home() / ".cache" / "thepipe" / "lib"
    )
    name = library_name or default_library_name()
    path = Path(name)
    return root / f"{path.stem}-{version}{path.suffix}"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install_archive(
    archive: str | Path,
    destination: str | Path,
    *,
    expected_sha256: str,
    required_version: str | None = None,
    binary_name: str = "codebase-memory-mcp",
) -> Path:
    """Verify an upstream release archive and atomically install its binary."""
    archive_path = Path(archive)
    destination_path = Path(destination)
    actual = sha256_file(archive_path)
    if actual.lower() != expected_sha256.lower():
        raise ArtifactError(
            f"codegraph artifact checksum mismatch: expected {expected_sha256}, found {actual}"
        )

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        dir=destination_path.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        if tarfile.is_tarfile(archive_path):
            _extract_tar_binary(archive_path, temporary, binary_name)
        elif zipfile.is_zipfile(archive_path):
            _extract_zip_binary(archive_path, temporary, binary_name)
        else:
            raise ArtifactError(f"unsupported codegraph archive: {archive_path}")

        temporary.chmod(0o755)
        if required_version is not None:
            try:
                SidecarBackend(temporary).require_version(required_version)
            except SidecarError as exc:
                raise ArtifactError(str(exc)) from exc
        os.replace(temporary, destination_path)
    finally:
        temporary.unlink(missing_ok=True)
    return destination_path


def install_sidecar_archive(
    archive: str | Path,
    *,
    expected_sha256: str,
    required_version: str,
    install_dir: str | Path | None = None,
    binary_name: str | None = None,
) -> Path:
    """Install a pinned sidecar archive into the default thepipe cache."""
    member_name = binary_name or default_binary_name()
    return install_archive(
        archive,
        default_binary_path(
            version=required_version,
            install_dir=install_dir,
            binary_name=member_name,
        ),
        expected_sha256=expected_sha256,
        required_version=required_version,
        binary_name=member_name,
    )


def install_shared_library_archive(
    archive: str | Path,
    *,
    expected_sha256: str,
    required_version: str,
    install_dir: str | Path | None = None,
    library_name: str | None = None,
) -> Path:
    """Install a pinned shared-library archive into the default thepipe cache."""
    member_name = library_name or default_library_name()
    return install_archive(
        archive,
        default_library_path(
            version=required_version,
            install_dir=install_dir,
            library_name=member_name,
        ),
        expected_sha256=expected_sha256,
        required_version=None,
        binary_name=member_name,
    )


def _extract_tar_binary(archive: Path, destination: Path, binary_name: str) -> None:
    with tarfile.open(archive, "r:*") as bundle:
        member = next(
            (
                item
                for item in bundle.getmembers()
                if item.isfile() and Path(item.name).name == binary_name
            ),
            None,
        )
        if member is None:
            raise ArtifactError(f"archive does not contain {binary_name}")
        source = bundle.extractfile(member)
        if source is None:
            raise ArtifactError(f"could not read {binary_name} from archive")
        with source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)


def _extract_zip_binary(archive: Path, destination: Path, binary_name: str) -> None:
    with zipfile.ZipFile(archive) as bundle:
        member = next(
            (
                item
                for item in bundle.infolist()
                if not item.is_dir() and Path(item.filename).name == binary_name
            ),
            None,
        )
        if member is None:
            raise ArtifactError(f"archive does not contain {binary_name}")
        with bundle.open(member) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)
