from __future__ import annotations

import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List
from xml.etree import ElementTree


def extract_record_shapes(path: str | Path) -> List[Dict[str, Any]]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        return _json_shapes(json.loads(source.read_text(encoding="utf-8")))
    if suffix == ".xml":
        return _xml_shapes(ElementTree.parse(source).getroot())
    return []


def extract_citation_anchors(path: str | Path) -> List[Dict[str, str]]:
    source = Path(path)
    suffix = source.suffix.lower()
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            return [{"kind": "zip-member", "path": name} for name in sorted(zf.namelist()) if not name.endswith("/")]
    if suffix == ".json":
        return _json_anchors(json.loads(source.read_text(encoding="utf-8")))
    if suffix == ".xml":
        return _xml_anchors(ElementTree.parse(source).getroot())
    return []


def _json_shapes(value: Any, path: str = "$.") -> List[Dict[str, Any]]:
    shapes: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = "$" if path == "$" else path.rstrip(".")
            shapes.extend(_json_shapes(child, f"{child_path}.{key}"))
    elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        fields = sorted({key for item in value for key in item.keys()})
        shapes.append({"path": f"{path}[]", "fields": fields, "kind": "json-array"})
    return shapes


def _json_anchors(value: Any, path: str = "$") -> List[Dict[str, str]]:
    anchors: List[Dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            anchors.append({"kind": "json-path", "path": child_path})
            anchors.extend(_json_anchors(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            anchors.extend(_json_anchors(child, f"{path}[{index}]"))
    return anchors


def _xml_shapes(root: ElementTree.Element) -> List[Dict[str, Any]]:
    by_path: Dict[str, List[ElementTree.Element]] = defaultdict(list)

    def walk(node: ElementTree.Element, path: str) -> None:
        by_path[path].append(node)
        for child in list(node):
            walk(child, f"{path}/{_strip_ns(child.tag)}")

    root_path = f"/{_strip_ns(root.tag)}"
    walk(root, root_path)
    counts = Counter({path: len(nodes) for path, nodes in by_path.items()})
    shapes: List[Dict[str, Any]] = []
    for path, count in sorted(counts.items()):
        if count < 1 or path == root_path:
            continue
        nodes = by_path[path]
        fields = sorted(
            {f"@{key}" for node in nodes for key in node.attrib.keys()}
            | {_strip_ns(child.tag) for node in nodes for child in list(node)}
        )
        if fields:
            shapes.append({"path": path, "fields": fields, "kind": "xml-element"})
    return shapes[:1]


def _xml_anchors(root: ElementTree.Element) -> List[Dict[str, str]]:
    anchors: List[Dict[str, str]] = []

    def walk(node: ElementTree.Element, path: str) -> None:
        anchors.append({"kind": "xml-path", "path": path})
        for child in list(node):
            walk(child, f"{path}/{_strip_ns(child.tag)}")

    walk(root, f"/{_strip_ns(root.tag)}")
    return anchors


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
