import json
import sqlite3

from thepipe.database_graph import DatabaseGraphLedger, find_join_candidates
from thepipe.database_utils import process_database


def test_database_graph_persists_operations_by_default(tmp_path):
    db_path = tmp_path / "demo.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Ada')")
    conn.commit()
    conn.close()

    graph_path = tmp_path / "graph.json"
    process_database(
        f"sqlite:///{db_path}",
        query="SELECT name FROM users",
        options={"database_graph": "auto", "database_graph_path": str(graph_path)},
    )

    data = json.loads(graph_path.read_text())
    assert data["operations"][0]["kind"] == "query"
    assert data["operations"][0]["query"] == "SELECT name FROM users"
    assert data["operations"][0]["result_fingerprint"]


def test_database_graph_memory_bypass_does_not_write_file(tmp_path):
    db_path = tmp_path / "demo.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER)")
    conn.commit()
    conn.close()

    graph_path = tmp_path / "graph.json"
    process_database(
        f"sqlite:///{db_path}",
        query="SELECT id FROM users",
        options={
            "database_graph": "auto",
            "database_graph_path": str(graph_path),
            "database_graph_persist": False,
        },
    )

    assert not graph_path.exists()


def test_database_graph_reuses_fresh_query_operation(tmp_path, monkeypatch):
    db_path = tmp_path / "demo.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Ada')")
    conn.commit()
    conn.close()

    graph_path = tmp_path / "graph.json"
    options = {"database_graph": "auto", "database_graph_path": str(graph_path)}
    process_database(f"sqlite:///{db_path}", query="SELECT name FROM users", options=options)

    def fail_query(*args, **kwargs):
        raise AssertionError("query should have reused graph operation")

    monkeypatch.setattr("thepipe.jupysql_middleware.Database.query", fail_query)
    chunks = process_database(f"sqlite:///{db_path}", query="SELECT name FROM users", options=options)

    query_chunk = next(chunk for chunk in chunks if chunk.path.endswith("/query"))
    assert "Ada" in query_chunk.text
    assert "Reused cached database graph operation" in query_chunk.text


def test_find_join_candidates_across_sources():
    candidates = find_join_candidates(
        [
            {
                "source_id": "crm",
                "tables": [{"name": "customers", "columns": ["customer_id", "email"]}],
            },
            {
                "source_id": "warehouse",
                "tables": [{"name": "orders", "columns": ["order_id", "customer_id"]}],
            },
        ]
    )

    assert candidates == [
        {
            "left": "crm.customers.customer_id",
            "right": "warehouse.orders.customer_id",
            "column": "customer_id",
            "confidence": 0.8,
            "evidence": "matching column name across sources",
        }
    ]


def test_ledger_records_dataset_group_join_candidates(tmp_path):
    graph_path = tmp_path / "graph.json"
    ledger = DatabaseGraphLedger(str(graph_path))

    ledger.record_sources(
        "revenue",
        [
            {"source_id": "crm", "tables": [{"name": "customers", "columns": ["customer_id"]}]},
            {"source_id": "warehouse", "tables": [{"name": "orders", "columns": ["customer_id"]}]},
        ],
    )

    data = json.loads(graph_path.read_text())
    assert data["dataset_groups"] == [{"name": "revenue", "sources": ["crm", "warehouse"]}]
    assert data["join_candidates"][0]["left"] == "crm.customers.customer_id"
    assert data["join_candidates"][0]["right"] == "warehouse.orders.customer_id"


def test_ledger_query_supports_operation_and_join_candidate_reads(tmp_path):
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))
    ledger.record_operation(
        {
            "kind": "query",
            "status": "ok",
            "source_fingerprint": "s",
            "query_fingerprint": "q",
            "query": "SELECT 1",
            "result_fingerprint": "r",
            "result_json": "[]",
            "db_type": "sqlite",
        }
    )
    ledger.record_sources(
        "revenue",
        [
            {"source_id": "crm", "tables": [{"name": "customers", "columns": ["customer_id"]}]},
            {"source_id": "warehouse", "tables": [{"name": "orders", "columns": ["customer_id"]}]},
        ],
    )

    operations = ledger.query("MATCH (op:Operation) RETURN op LIMIT 5")
    joins = ledger.query("MATCH (a:Column)-[j:CROSS_SOURCE_JOIN]->(b:Column) RETURN j LIMIT 5")

    assert operations[0]["query"] == "SELECT 1"
    assert joins[0]["column"] == "customer_id"


def test_process_database_records_option_sources(tmp_path):
    db_path = tmp_path / "demo.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (customer_id INTEGER)")
    conn.commit()
    conn.close()
    graph_path = tmp_path / "graph.json"

    process_database(
        f"sqlite:///{db_path}",
        query="SELECT customer_id FROM users",
        options={
            "database_graph": "auto",
            "database_graph_path": str(graph_path),
            "database_graph_group": "revenue",
            "database_graph_sources": [
                {"source_id": "crm", "tables": [{"name": "customers", "columns": ["customer_id"]}]},
                {"source_id": "warehouse", "tables": [{"name": "orders", "columns": ["customer_id"]}]},
            ],
        },
    )

    data = json.loads(graph_path.read_text())
    assert data["dataset_groups"][0]["name"] == "revenue"
    assert data["join_candidates"][0]["column"] == "customer_id"
