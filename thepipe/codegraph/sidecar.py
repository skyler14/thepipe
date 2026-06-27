from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

UPSTREAM_REPO = "https://github.com/DeusData/codebase-memory-mcp"
PINNED_UPSTREAM_COMMIT = "b075f0506ce4286219edd1bc3dccb196f2ed7cb0"
PINNED_RUNTIME_VERSION = "0.10.0"
GRAMMAR_ARTIFACT_POLICY = (
    "Use compiled grammar artifacts in releases; do not vendor raw generated "
    "grammar C source into the Python repo."
)


class SidecarError(RuntimeError):
    pass


class SidecarBackend:
    kind = "sidecar"

    def __init__(
        self,
        binary: str | Path,
        *,
        cache_dir: str | Path | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.binary = Path(binary)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.timeout = timeout
        self._version: str | None = None

    def call(self, tool: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._check_binary()
        env = os.environ.copy()
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            env["CBM_CACHE_DIR"] = str(self.cache_dir)

        try:
            proc = subprocess.run(
                [str(self.binary), "cli", "--json", tool, json.dumps(payload or {})],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise SidecarError(
                f"codegraph sidecar timed out after {self.timeout:g}s while running {tool}"
            ) from exc
        except OSError as exc:
            raise SidecarError(f"could not run codegraph sidecar: {exc}") from exc

        if proc.returncode:
            detail = (proc.stderr or proc.stdout or "codegraph sidecar failed").strip()
            raise SidecarError(detail[:4096])

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise SidecarError("codegraph sidecar returned invalid JSON") from exc
        if not isinstance(envelope, dict):
            raise SidecarError("codegraph sidecar returned a malformed MCP envelope")

        text = _first_text(envelope)
        if envelope.get("isError"):
            if tool == "delete_project":
                try:
                    delete_result = json.loads(text)
                except json.JSONDecodeError:
                    delete_result = None
                if (
                    isinstance(delete_result, dict)
                    and delete_result.get("status") == "not_found"
                ):
                    return delete_result
            raise SidecarError(text or "codegraph sidecar returned an error")

        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        if isinstance(decoded, dict):
            return decoded
        return {"value": decoded}

    def version(self) -> str:
        if self._version is not None:
            return self._version
        self._check_binary()
        try:
            proc = subprocess.run(
                [str(self.binary), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=min(self.timeout, 10.0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SidecarError(f"could not read codegraph sidecar version: {exc}") from exc
        if proc.returncode:
            raise SidecarError(
                (proc.stderr or proc.stdout or "version check failed").strip()[:4096]
            )
        output = proc.stdout.strip()
        if not output:
            raise SidecarError("codegraph sidecar returned an empty version")
        self._version = output.rsplit(" ", 1)[-1]
        return self._version

    def require_version(self, expected: str) -> None:
        actual = self.version()
        if actual != expected:
            raise SidecarError(
                f"codegraph sidecar requires version {expected}; found {actual}"
            )

    def _check_binary(self) -> None:
        if not self.binary.exists():
            raise SidecarError(f"codegraph sidecar not found: {self.binary}")
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise SidecarError(f"codegraph sidecar is not executable: {self.binary}")


def _first_text(envelope: dict[str, Any]) -> str:
    for item in envelope.get("content", []):
        if item.get("type") == "text":
            return str(item.get("text", ""))
    return ""


def sidecar_from_env(env: dict[str, str] | None = None) -> SidecarBackend | None:
    value = (env or os.environ).get("THEPIPE_CODEGRAPH_BINARY")
    if not value:
        return None
    return SidecarBackend(value)
