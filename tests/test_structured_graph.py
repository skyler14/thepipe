import json
import zipfile

from thepipe.structured_graph import extract_citation_anchors, extract_record_shapes
from thepipe.database_graph import DatabaseGraphLedger
from thepipe.database_utils import process_database


def test_extract_json_record_shapes_and_anchors(tmp_path):
    path = tmp_path / "customers.json"
    path.write_text(json.dumps({"customers": [{"id": 1, "email": "a@example.com"}]}))

    shapes = extract_record_shapes(path)
    anchors = extract_citation_anchors(path)

    assert shapes == [{"path": "$.customers[]", "fields": ["email", "id"], "kind": "json-array"}]
    assert {anchor["path"] for anchor in anchors} >= {"$.customers", "$.customers[0].email"}


def test_extract_xml_record_shapes_and_anchors(tmp_path):
    path = tmp_path / "customers.xml"
    path.write_text("<root><customer id='1'><email>a@example.com</email></customer></root>")

    shapes = extract_record_shapes(path)
    anchors = extract_citation_anchors(path)

    assert shapes == [{"path": "/root/customer", "fields": ["@id", "email"], "kind": "xml-element"}]
    assert {anchor["path"] for anchor in anchors} >= {"/root/customer", "/root/customer/email"}


def test_extract_zip_member_anchors(tmp_path):
    path = tmp_path / "bundle.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", "<w:document />")
        zf.writestr("docProps/core.xml", "<cp:coreProperties />")

    anchors = extract_citation_anchors(path)

    assert anchors == [
        {"kind": "zip-member", "path": "docProps/core.xml"},
        {"kind": "zip-member", "path": "word/document.xml"},
    ]


def test_ledger_records_structured_source(tmp_path):
    path = tmp_path / "customers.json"
    path.write_text(json.dumps({"customers": [{"id": 1, "email": "a@example.com"}]}))
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))

    ledger.record_structured_source(path)

    rows = ledger.query("MATCH (a:CitationAnchor) RETURN a LIMIT 10")
    shapes = ledger.query("MATCH (s:RecordShape) RETURN s LIMIT 10")
    assert {row["path"] for row in rows} >= {"$.customers", "$.customers[0].email"}
    assert shapes == [{"source_id": str(path), "path": "$.customers[]", "fields": ["email", "id"], "kind": "json-array"}]


def test_ledger_queries_structured_source_properties(tmp_path):
    path = tmp_path / "customers.json"
    path.write_text(json.dumps({"customers": [{"id": 1, "email": "a@example.com"}]}))
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))
    ledger.record_structured_source(path)

    rows = ledger.query('MATCH (s:RecordShape) WHERE s.path CONTAINS "customers" RETURN s.source_id, s.path, s.kind LIMIT 5')

    assert rows == [{"s.source_id": str(path), "s.path": "$.customers[]", "s.kind": "json-array"}]


def test_process_database_graph_mode_records_structured_sources(tmp_path):
    source = tmp_path / "customers.json"
    source.write_text(json.dumps({"customers": [{"id": 1}]}))
    graph = tmp_path / "graph.json"

    chunks = process_database(
        "unused",
        mode="graph",
        options={
            "database_graph_path": str(graph),
            "database_graph_structured_sources": [str(source)],
            "database_graph_query": "MATCH (s:RecordShape) RETURN s LIMIT 5",
        },
    )

    assert chunks[0].path == "database://graph/query"
    assert "$.customers[]" in chunks[0].text
