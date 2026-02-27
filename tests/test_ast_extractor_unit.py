"""
Unit tests for AST extractor helper behavior that does not require tree-sitter.
"""

from dataclasses import dataclass

import thepipe.analyzer.ast_extractor as ast_module
from thepipe.analyzer.ast_extractor import ASTExtractor


@dataclass
class _FakeNode:
    start_byte: int
    end_byte: int
    start_point: tuple[int, int]
    end_point: tuple[int, int]
    type: str
    parent: "_FakeNode | None" = None
    is_named: bool = True


def test_build_name_capture_maps_prefers_shorter_name_capture():
    source = "Ns::Type::doSomething"
    short_start = source.index("doSomething")
    short_end = short_start + len("doSomething")

    func = _FakeNode(
        start_byte=0,
        end_byte=len(source),
        start_point=(0, 0),
        end_point=(0, len(source)),
        type="function_definition",
    )
    qualified = _FakeNode(
        start_byte=0,
        end_byte=len(source),
        start_point=(0, 0),
        end_point=(0, len(source)),
        type="qualified_identifier",
        parent=func,
    )
    short = _FakeNode(
        start_byte=short_start,
        end_byte=short_end,
        start_point=(0, short_start),
        end_point=(0, short_end),
        type="identifier",
        parent=func,
    )

    direct, _ = ASTExtractor._build_name_capture_maps(
        captures=[(qualified, "name"), (short, "name")],
        source=source,
    )

    assert direct[ASTExtractor._node_key(func)] == "doSomething"


def test_query_captures_supports_legacy_list_api():
    node = _FakeNode(
        start_byte=0,
        end_byte=3,
        start_point=(0, 0),
        end_point=(0, 3),
        type="identifier",
    )

    class _LegacyQuery:
        def captures(self, _root):
            return [(node, "name")]

    captures = ASTExtractor._query_captures(_LegacyQuery(), object())
    assert captures == [(node, "name")]


def test_query_captures_supports_query_cursor_dict_api(monkeypatch):
    func = _FakeNode(
        start_byte=0,
        end_byte=10,
        start_point=(0, 0),
        end_point=(0, 10),
        type="function_definition",
    )
    name = _FakeNode(
        start_byte=4,
        end_byte=8,
        start_point=(0, 4),
        end_point=(0, 8),
        type="identifier",
        parent=func,
    )

    class _Cursor:
        def __init__(self, _query):
            pass

        def captures(self, _root):
            return {"func": [func], "name": [name]}

    class _NewQuery:
        pass

    monkeypatch.setattr(ast_module, "QueryCursor", _Cursor)
    captures = ASTExtractor._query_captures(_NewQuery(), object())

    assert captures == [(func, "func"), (name, "name")]
