from __future__ import annotations

import ctypes
import json

import pytest

from thepipe.codegraph.sharedlib import SharedLibraryBackend, SharedLibraryError


class FakeFunction:
    def __init__(self, function):
        self.function = function
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.function(*args)


class FakeLibrary:
    def __init__(self, *, status: int = 0, response: object = None) -> None:
        self.status = status
        self.response = response if response is not None else {"ok": True}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.freed: list[int] = []
        self.closed: list[int] = []
        self.cache_dirs: list[str] = []
        self._buffers: list[ctypes.Array] = []
        self.tp_context_new = FakeFunction(self._new)
        self.tp_context_call = FakeFunction(self._call)
        self.tp_context_free = FakeFunction(self._close)
        self.tp_version = FakeFunction(lambda: b"0.10.0")
        self.tp_string_free = FakeFunction(self._free)

    def _new(self, cache_dir):
        self.cache_dirs.append(ctypes.string_at(cache_dir).decode())
        return 1234

    def _call(self, context, tool, payload, output) -> int:
        assert context == 1234
        self.calls.append(
            (
                ctypes.string_at(tool).decode(),
                json.loads(ctypes.string_at(payload)),
            )
        )
        encoded = (
            self.response
            if isinstance(self.response, bytes)
            else json.dumps(self.response).encode()
        )
        buffer = ctypes.create_string_buffer(encoded)
        self._buffers.append(buffer)
        output._obj.value = ctypes.addressof(buffer)
        return self.status

    def _close(self, context) -> None:
        self.closed.append(context)

    def _free(self, pointer) -> None:
        self.freed.append(pointer.value if hasattr(pointer, "value") else int(pointer))


def test_shared_library_uses_same_backend_contract_and_frees_output() -> None:
    library = FakeLibrary(response={"results": [{"name": "main"}]})
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)

    result = backend.call("search_graph", {"project": "demo", "query": "main"})

    assert result == {"results": [{"name": "main"}]}
    assert library.calls == [
        ("search_graph", {"project": "demo", "query": "main"})
    ]
    assert len(library.freed) == 1
    assert library.cache_dirs == ["/repo/cache"]
    assert backend.version() == "0.10.0"
    backend.close()
    assert library.closed == [1234]


def test_shared_library_surfaces_native_error_and_still_frees_output() -> None:
    library = FakeLibrary(status=7, response={"error": "bad query"})
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)

    with pytest.raises(SharedLibraryError, match="bad query"):
        backend.call("query_graph", {"project": "demo", "query": "bad"})

    assert len(library.freed) == 1


def test_shared_library_rejects_invalid_json() -> None:
    backend = SharedLibraryBackend(
        cache_dir="/repo/cache",
        library=FakeLibrary(response=b"not-json"),
    )

    with pytest.raises(SharedLibraryError, match="invalid JSON"):
        backend.call("list_projects", {})


def test_shared_library_preserves_delete_not_found_as_a_result() -> None:
    response = {
        "isError": True,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"project": "missing", "status": "not_found"}
                ),
            }
        ],
    }
    backend = SharedLibraryBackend(
        cache_dir="/repo/cache",
        library=FakeLibrary(response=response),
    )

    assert backend.call("delete_project", {"project": "missing"}) == {
        "project": "missing",
        "status": "not_found",
    }
