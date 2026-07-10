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
    assert data["sources"][0]["tables"] == [{"name": "users", "columns": ["id", "name"]}]


def test_database_graph_adds_repo_local_git_exclude(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git" / "info").mkdir(parents=True)
    monkeypatch.chdir(repo)
    db_path = tmp_path / "demo.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER)")
    conn.commit()
    conn.close()

    process_database(f"sqlite:///{db_path}", query="SELECT id FROM users", options={"database_graph": "auto"})

    exclude = repo / ".git" / "info" / "exclude"
    assert ".thepipe/database/" in exclude.read_text()


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


def test_ledger_projects_sources_tables_columns_and_relationships(tmp_path):
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))
    ledger.record_sources(
        "revenue",
        [
            {"source_id": "crm", "kind": "sqlite", "tables": [{"name": "customers", "columns": ["customer_id"]}]},
            {"source_id": "warehouse", "tables": [{"name": "orders", "columns": ["customer_id", "total"]}]},
        ],
    )

    graph = ledger.to_property_graph()

    assert graph["schema_version"] == "thepipe-property-graph/v1"
    assert {tuple(node["labels"]) for node in graph["nodes"]} >= {("Source",), ("Table",), ("Column",), ("DatasetGroup",)}
    assert {edge["type"] for edge in graph["edges"]} >= {"HAS_SOURCE", "HAS_TABLE", "HAS_COLUMN", "CROSS_SOURCE_JOIN"}


def test_ledger_cypher_supports_source_table_column_traversal(tmp_path):
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))
    ledger.record_sources(
        "revenue",
        [{"source_id": "crm", "tables": [{"name": "customers", "columns": ["customer_id", "email"]}]}],
    )

    rows = ledger.query('MATCH (s:Source)-[:HAS_TABLE]->(t:Table)-[:HAS_COLUMN]->(c:Column) WHERE c.name CONTAINS "email" RETURN s.source_id, t.name, c.name LIMIT 5')

    assert rows == [{"s.source_id": "crm", "t.name": "customers", "c.name": "email"}]


def test_ledger_cypher_supports_insight_property_returns_and_boolean_where(tmp_path):
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))
    insight = ledger.pin_insight("users table is small")

    rows = ledger.query("MATCH (i:Insight) WHERE i.pinned = true RETURN i.summary, i.insight_id LIMIT 10")

    assert rows == [{"i.summary": "users table is small", "i.insight_id": insight["insight_id"]}]


def test_ledger_cypher_supports_join_candidate_relationship_return(tmp_path):
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))
    ledger.record_sources(
        "revenue",
        [
            {"source_id": "crm", "tables": [{"name": "customers", "columns": ["customer_id"]}]},
            {"source_id": "warehouse", "tables": [{"name": "orders", "columns": ["customer_id"]}]},
        ],
    )

    rows = ledger.query("MATCH (a:Column)-[j:CROSS_SOURCE_JOIN]->(b:Column) RETURN a.qualified_name, j.confidence, b.qualified_name LIMIT 5")

    assert rows == [
        {
            "a.qualified_name": "crm.customers.customer_id",
            "j.confidence": 0.8,
            "b.qualified_name": "warehouse.orders.customer_id",
        }
    ]


def test_ledger_cypher_rejects_unsupported_queries(tmp_path):
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))

    try:
        ledger.query("CREATE (n:Source {name: 'bad'})")
    except ValueError as exc:
        assert "unsupported database graph query" in str(exc)
    else:
        raise AssertionError("unsupported Cypher should fail explicitly")


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


def test_pinned_insights_survive_operation_purge_and_revoke(tmp_path):
    ledger = DatabaseGraphLedger(str(tmp_path / "graph.json"))
    ledger.record_operation(
        {
            "kind": "profile",
            "status": "ok",
            "source_fingerprint": "s",
            "query_fingerprint": "q",
            "query": "SELECT count(*) FROM users",
            "result_fingerprint": "r",
            "result_json": "[]",
            "db_type": "sqlite",
        }
    )
    insight = ledger.pin_insight("users table is small", evidence_operation_id=ledger.data["operations"][0]["operation_id"])

    ledger.purge_unpinned_operations()
    assert ledger.data["operations"] == []
    assert ledger.query("MATCH (i:Insight) RETURN i LIMIT 5")[0]["summary"] == "users table is small"

    ledger.revoke_insight(insight["insight_id"])
    assert ledger.query("MATCH (i:Insight) RETURN i LIMIT 5") == []


def test_process_database_graph_mode_queries_ledger_without_db_connect(tmp_path, monkeypatch):
    graph_path = tmp_path / "graph.json"
    ledger = DatabaseGraphLedger(str(graph_path))
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

    def fail_connect(*args, **kwargs):
        raise AssertionError("graph query should not connect to DB")

    monkeypatch.setattr("thepipe.database_utils.DatabaseManager", fail_connect)
    chunks = process_database(
        "unused",
        mode="graph",
        options={"database_graph_path": str(graph_path), "database_graph_query": "MATCH (op:Operation) RETURN op LIMIT 5"},
    )

    assert chunks[0].path == "database://graph/query"
    assert "SELECT 1" in chunks[0].text


def test_process_database_graph_mode_supports_cypher_traversal(tmp_path, monkeypatch):
    graph_path = tmp_path / "graph.json"
    ledger = DatabaseGraphLedger(str(graph_path))
    ledger.record_sources(
        "revenue",
        [{"source_id": "crm", "tables": [{"name": "customers", "columns": ["customer_id", "email"]}]}],
    )

    def fail_connect(*args, **kwargs):
        raise AssertionError("graph query should not connect to DB")

    monkeypatch.setattr("thepipe.database_utils.DatabaseManager", fail_connect)
    chunks = process_database(
        "unused",
        mode="graph",
        options={
            "database_graph_path": str(graph_path),
            "database_graph_query": 'MATCH (s:Source)-[:HAS_TABLE]->(t:Table)-[:HAS_COLUMN]->(c:Column) WHERE c.name CONTAINS "email" RETURN s.source_id, t.name, c.name LIMIT 5',
        },
    )

    payload = json.loads(chunks[0].text)
    assert payload == [{"s.source_id": "crm", "t.name": "customers", "c.name": "email"}]


def test_tool_schema_exposes_database_graph_mode():
    from thepipe.tool_schema import get_claude_tools

    scrape_database = next(tool for tool in get_claude_tools() if tool["name"] == "thepipe_scrape_database")

    assert "graph" in scrape_database["input_schema"]["properties"]["mode"]["enum"]
    assert "database_graph_query" in scrape_database["input_schema"]["properties"]
