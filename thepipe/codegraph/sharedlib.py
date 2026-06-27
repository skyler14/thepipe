from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any


class SharedLibraryError(RuntimeError):
    pass


class SharedLibraryBackend:
    """ctypes adapter for the stable coarse-grained codegraph JSON ABI."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        cache_dir: str | Path,
        library: Any | None = None,
    ) -> None:
        if library is None:
            if path is None:
                raise ValueError("path or library is required")
            try:
                library = ctypes.CDLL(str(path))
            except OSError as exc:
                raise SharedLibraryError(f"could not load codegraph library: {exc}") from exc
        self.library = library
        self.cache_dir = Path(cache_dir)
        self._configure_abi()
        self._context = self.library.tp_context_new(
            str(self.cache_dir).encode("utf-8")
        )
        if not self._context:
            raise SharedLibraryError("codegraph library could not create a context")

    def _configure_abi(self) -> None:
        try:
            self.library.tp_context_new.argtypes = [ctypes.c_char_p]
            self.library.tp_context_new.restype = ctypes.c_void_p
            self.library.tp_context_call.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            self.library.tp_context_call.restype = ctypes.c_int
            self.library.tp_context_free.argtypes = [ctypes.c_void_p]
            self.library.tp_context_free.restype = None
            self.library.tp_version.argtypes = []
            self.library.tp_version.restype = ctypes.c_char_p
            self.library.tp_string_free.argtypes = [ctypes.c_void_p]
            self.library.tp_string_free.restype = None
        except AttributeError as exc:
            raise SharedLibraryError(f"codegraph library is missing ABI symbol: {exc}") from exc

    def call(self, tool: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = json.dumps(
            payload or {},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        output = ctypes.c_void_p()
        if not self._context:
            raise SharedLibraryError("codegraph library context is closed")
        status = self.library.tp_context_call(
            self._context,
            tool.encode("utf-8"),
            request,
            ctypes.byref(output),
        )
        if not output.value:
            raise SharedLibraryError(
                f"codegraph library returned status {status} without output"
            )
        try:
            raw = ctypes.string_at(output.value).decode("utf-8")
        finally:
            self.library.tp_string_free(output)
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SharedLibraryError("codegraph library returned invalid JSON") from exc
        if status:
            if isinstance(decoded, dict):
                detail = decoded.get("error") or decoded.get("message")
            else:
                detail = None
            raise SharedLibraryError(
                str(detail or f"codegraph library call failed with status {status}")
            )
        if isinstance(decoded, dict):
            return _unwrap_mcp(decoded)
        return {"value": decoded}

    def version(self) -> str:
        raw = self.library.tp_version()
        if not raw:
            raise SharedLibraryError("codegraph library returned an empty version")
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    def close(self) -> None:
        if self._context:
            self.library.tp_context_free(self._context)
            self._context = None

    def __enter__(self) -> SharedLibraryBackend:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _unwrap_mcp(envelope: dict[str, Any]) -> dict[str, Any]:
    if "content" not in envelope:
        return envelope
    text = ""
    for item in envelope.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text", ""))
            break
    if envelope.get("isError"):
        raise SharedLibraryError(text or "codegraph library returned an MCP error")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}
    return decoded if isinstance(decoded, dict) else {"value": decoded}
