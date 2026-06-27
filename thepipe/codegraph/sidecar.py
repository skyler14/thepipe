from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

UPSTREAM_REPO = "https://github.com/DeusData/codebase-memory-mcp"
GRAMMAR_ARTIFACT_POLICY = (
    "Use compiled grammar artifacts in releases; do not vendor raw generated "
    "grammar C source into the Python repo."
)


class SidecarError(RuntimeError):
    pass


class SidecarBackend:
    def __init__(self, binary: str | Path) -> None:
        self.binary = Path(binary)

    def call(self, tool: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.binary.exists():
            raise SidecarError(f"codegraph sidecar not found: {self.binary}")

        proc = subprocess.run(
            [str(self.binary), "cli", "--json", tool, json.dumps(payload or {})],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode:
            raise SidecarError((proc.stderr or proc.stdout or "codegraph sidecar failed").strip())

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise SidecarError("codegraph sidecar returned invalid JSON") from exc

        text = _first_text(envelope)
        if envelope.get("isError"):
            raise SidecarError(text or "codegraph sidecar returned an error")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}


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
