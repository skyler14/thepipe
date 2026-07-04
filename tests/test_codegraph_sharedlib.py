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
    def __init__(
        self,
        *,
        status: int = 0,
        response: object = None,
        optional_symbols: bool = True,
    ) -> None:
        self.status = status
        self.response = response if response is not None else {"ok": True}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.store_calls: list[tuple[int, str, dict[str, object]]] = []
        self.cypher_calls: list[tuple[int, dict[str, object]]] = []
        self.call_contexts: list[int] = []
        self.freed: list[int] = []
        self.closed: list[int] = []
        self.store_closed: list[int] = []
        self.quiet_settings: list[tuple[int, int]] = []
        self.cache_dirs: list[str] = []
        self.store_paths: list[str] = []
        self.next_context = 1234
        self.next_store = 4321
        self._buffers: list[ctypes.Array] = []
        self.tp_context_new = FakeFunction(self._new)
        self.tp_context_call = FakeFunction(self._call)
        self.tp_context_free = FakeFunction(self._close)
        self.tp_version = FakeFunction(lambda: b"0.10.0")
        self.tp_string_free = FakeFunction(self._free)
        if optional_symbols:
            self.tp_abi_version = FakeFunction(lambda: b"thepipe-codegraph/1")
            self.tp_context_set_quiet = FakeFunction(self._set_quiet)
            self.tp_store_open_query = FakeFunction(self._store_open_query)
            self.tp_store_call = FakeFunction(self._store_call)
            self.tp_store_close = FakeFunction(self._store_close)
            self.tp_cypher_query = FakeFunction(self._cypher_query)

    def _new(self, cache_dir):
        self.cache_dirs.append(ctypes.string_at(cache_dir).decode())
        context = self.next_context
        self.next_context += 1
        return context

    def _call(self, context, tool, payload, output) -> int:
        self.call_contexts.append(context)
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

    def _set_quiet(self, context, quiet) -> int:
        self.quiet_settings.append((context, quiet))
        return 0

    def _write_output(self, output, response: object) -> None:
        encoded = response if isinstance(response, bytes) else json.dumps(response).encode()
        buffer = ctypes.create_string_buffer(encoded)
        self._buffers.append(buffer)
        output._obj.value = ctypes.addressof(buffer)

    def _store_open_query(self, db_path):
        self.store_paths.append(ctypes.string_at(db_path).decode())
        store = self.next_store
        self.next_store += 1
        return store

    def _store_call(self, store, action, payload, output) -> int:
        self.store_calls.append(
            (
                store,
                ctypes.string_at(action).decode(),
                json.loads(ctypes.string_at(payload)),
            )
        )
        self._write_output(output, self.response)
        return self.status

    def _store_close(self, store) -> None:
        self.store_closed.append(store)

    def _cypher_query(self, store, payload, output) -> int:
        self.cypher_calls.append((store, json.loads(ctypes.string_at(payload))))
        self._write_output(output, self.response)
        return self.status


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
    assert backend.abi_version() == "thepipe-codegraph/1"
    assert library.quiet_settings == [(1234, 1)]
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


def test_shared_library_reuses_context_for_repeated_calls() -> None:
    library = FakeLibrary(response={"ok": True})
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)

    assert backend.call("list_projects", {}) == {"ok": True}
    assert backend.call("index_status", {"project": "demo"}) == {"ok": True}

    assert library.call_contexts == [1234, 1234]
    assert len(library.freed) == 2


def test_shared_library_contexts_are_separate_per_backend() -> None:
    library = FakeLibrary(response={"ok": True})
    first = SharedLibraryBackend(cache_dir="/repo/one", library=library)
    second = SharedLibraryBackend(cache_dir="/repo/two", library=library)

    first.call("list_projects", {})
    second.call("list_projects", {})

    assert library.cache_dirs == ["/repo/one", "/repo/two"]
    assert library.call_contexts == [1234, 1235]
    first.close()
    second.close()
    assert library.closed == [1234, 1235]


def test_shared_library_rejects_calls_after_close() -> None:
    backend = SharedLibraryBackend(
        cache_dir="/repo/cache",
        library=FakeLibrary(response={"ok": True}),
    )
    backend.close()

    with pytest.raises(SharedLibraryError, match="closed"):
        backend.call("list_projects", {})


def test_shared_library_can_disable_quiet_mode() -> None:
    library = FakeLibrary(response={"ok": True})
    backend = SharedLibraryBackend(
        cache_dir="/repo/cache",
        library=library,
        quiet=False,
    )

    assert library.quiet_settings == [(1234, 0)]
    backend.close()


def test_shared_library_accepts_older_library_without_optional_symbols() -> None:
    library = FakeLibrary(response={"ok": True}, optional_symbols=False)
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)

    assert backend.abi_version() is None
    assert backend.call("list_projects", {}) == {"ok": True}


def test_shared_library_reports_direct_store_api_availability() -> None:
    assert SharedLibraryBackend(
        cache_dir="/repo/cache",
        library=FakeLibrary(optional_symbols=True),
    ).has_direct_store_api() is True
    assert SharedLibraryBackend(
        cache_dir="/repo/cache",
        library=FakeLibrary(optional_symbols=False),
    ).has_direct_store_api() is False


def test_direct_store_action_uses_read_only_store_handle_and_frees_output() -> None:
    library = FakeLibrary(response={"project": "demo", "nodes": 2})
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)

    with backend.open_store("/repo/.thepipe/codegraph/cache/demo.db") as store:
        result = store.summary("demo")

    assert result == {"project": "demo", "nodes": 2}
    assert library.store_paths == ["/repo/.thepipe/codegraph/cache/demo.db"]
    assert library.store_calls == [
        (4321, "index_status", {"project": "demo"})
    ]
    assert library.store_closed == [4321]
    assert len(library.freed) == 1


def test_direct_store_exposes_search_neighbors_schema_and_architecture() -> None:
    library = FakeLibrary(response={"ok": True})
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)
    store = backend.open_store("/repo/demo.db")

    assert store.search("demo", query="main", label="Function", limit=5) == {"ok": True}
    assert store.neighbors(
        "demo",
        entity="demo.app.main",
        direction="outbound",
        depth=2,
        edge_types=["CALLS"],
        limit=10,
    ) == {"ok": True}
    assert store.schema("demo") == {"ok": True}
    assert store.architecture("demo", path="src", aspects=["routes"]) == {"ok": True}

    assert library.store_calls == [
        (
            4321,
            "search_graph",
            {"project": "demo", "query": "main", "label": "Function", "limit": 5},
        ),
        (
            4321,
            "trace_path",
            {
                "project": "demo",
                "function": "demo.app.main",
                "direction": "outbound",
                "depth": 2,
                "edge_types": ["CALLS"],
                "limit": 10,
            },
        ),
        (4321, "get_graph_schema", {"project": "demo"}),
        (
            4321,
            "get_architecture",
            {"project": "demo", "path": "src", "aspects": ["routes"]},
        ),
    ]
    store.close()


def test_direct_store_cypher_uses_graph_query_symbol() -> None:
    library = FakeLibrary(response={"columns": ["n"], "rows": []})
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)

    with backend.open_store("/repo/demo.db") as store:
        result = store.cypher("demo", "MATCH (n) RETURN n", max_rows=20)

    assert result == {"columns": ["n"], "rows": []}
    assert library.cypher_calls == [
        (
            4321,
            {"project": "demo", "query": "MATCH (n) RETURN n", "max_rows": 20},
        )
    ]
    assert len(library.freed) == 1


def test_direct_store_requires_new_abi_symbols() -> None:
    backend = SharedLibraryBackend(
        cache_dir="/repo/cache",
        library=FakeLibrary(optional_symbols=False),
    )

    with pytest.raises(SharedLibraryError, match="direct store ABI"):
        backend.open_store("/repo/demo.db")


def test_direct_store_surfaces_native_error_and_closes_handle() -> None:
    library = FakeLibrary(status=9, response={"error": "bad graph"})
    backend = SharedLibraryBackend(cache_dir="/repo/cache", library=library)

    with pytest.raises(SharedLibraryError, match="bad graph"):
        with backend.open_store("/repo/demo.db") as store:
            store.summary("demo")

    assert library.store_closed == [4321]
    assert len(library.freed) == 1
