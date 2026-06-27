"""
Tests for database_utils.py - database format detection, JDBC URL conversion, and query execution.
"""

import os
import sys
import tempfile
import unittest
import importlib.util
import gzip
import sqlite3
import types
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

JUPYSQL_AVAILABLE = importlib.util.find_spec("jupysql") is not None


class _SQLiteOdbcCursor:
    def __init__(self, connection):
        self._connection = connection
        self._cursor = connection.cursor()
        self.description = None

    def execute(self, query, params=None):
        if params is None:
            self._cursor.execute(query)
        elif isinstance(params, tuple):
            self._cursor.execute(query, params)
        else:
            self._cursor.execute(query, tuple(params))
        self.description = self._cursor.description
        return self

    def fetchall(self):
        return self._cursor.fetchall()

    def tables(self):
        rows = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [
            types.SimpleNamespace(table_name=row[0], table_type="TABLE")
            for row in rows
        ]

    def columns(self, table):
        rows = self._connection.execute(
            f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")'
        ).fetchall()
        return [
            types.SimpleNamespace(
                column_name=row[1],
                type_name=row[2],
                nullable=not bool(row[3]),
                column_def=row[4],
            )
            for row in rows
        ]

    def close(self):
        self._cursor.close()


class _SQLiteOdbcConnection:
    def __init__(self, path):
        self._connection = sqlite3.connect(path)

    def cursor(self):
        return _SQLiteOdbcCursor(self._connection)

    def commit(self):
        return self._connection.commit()

    def close(self):
        return self._connection.close()


class TestDatabaseFormats(unittest.TestCase):
    """Tests for database format detection and JDBC URL conversion."""
    
    def test_jdbc_mysql_conversion(self):
        """JDBC MySQL URLs should convert to mysql+pymysql://"""
        from thepipe.database_utils import DatabaseManager
        
        manager = DatabaseManager.__new__(DatabaseManager)
        manager.connection_info = "jdbc:mysql://host:3306/db"
        
        result = manager._convert_jdbc_url(manager.connection_info)
        
        self.assertTrue(result.startswith('mysql+pymysql://'))
        self.assertIn('host:3306', result)
    
    def test_jdbc_postgresql_conversion(self):
        """JDBC PostgreSQL URLs should convert to postgresql://"""
        from thepipe.database_utils import DatabaseManager
        
        manager = DatabaseManager.__new__(DatabaseManager)
        
        result = manager._convert_jdbc_url("jdbc:postgresql://host:5432/db")
        
        self.assertTrue(result.startswith('postgresql://'))
    
    def test_jdbc_sqlite_conversion(self):
        """JDBC SQLite URLs should convert to sqlite:///"""
        from thepipe.database_utils import DatabaseManager
        
        manager = DatabaseManager.__new__(DatabaseManager)
        
        result = manager._convert_jdbc_url("jdbc:sqlite:/path/to/db.sqlite")
        
        self.assertTrue(result.startswith('sqlite:///'))
    
    def test_detect_database_type_mysql(self):
        """Should detect mysql type from various URL formats."""
        from thepipe.database_utils import DatabaseManager
        
        manager = DatabaseManager.__new__(DatabaseManager)
        manager.connection_info = None
        
        test_cases = [
            ("mysql://host/db", "mysql"),
            ("mysql+pymysql://host/db", "mysql"),
            ("mariadb://host/db", "mysql"),
        ]
        
        for url, expected in test_cases:
            manager.connection_info = url
            result = manager._detect_database_type(url)
            self.assertEqual(result, expected, f"Failed for {url}")
    
    def test_detect_database_type_duckdb(self):
        """Should detect duckdb type from URL and file extension."""
        from thepipe.database_utils import DatabaseManager
        
        manager = DatabaseManager.__new__(DatabaseManager)
        
        test_cases = [
            ("duckdb:///path/to/db", "duckdb"),
            ("/path/to/file.duckdb", "duckdb"),
            ("/path/to/file.db", "duckdb"),
        ]
        
        for url, expected in test_cases:
            result = manager._detect_database_type(url)
            self.assertEqual(result, expected, f"Failed for {url}")

    def test_detect_database_type_odbc(self):
        """Should detect raw ODBC URLs."""
        from thepipe.database_utils import DatabaseManager

        manager = DatabaseManager.__new__(DatabaseManager)

        result = manager._detect_database_type("odbc://?connect=DRIVER%3DSQLite3")
        self.assertEqual(result, "odbc")
    
    def test_detect_database_type_data_formats(self):
        """Should detect ORC, Feather, JSONL formats."""
        from thepipe.database_utils import DatabaseManager
        
        manager = DatabaseManager.__new__(DatabaseManager)
        
        test_cases = [
            ("/data/file.orc", "orc"),
            ("/data/file.feather", "feather"),
            ("/data/file.arrow", "feather"),
            ("/data/file.ipc", "feather"),
            ("/data/file.jsonl", "jsonl"),
            ("/data/file.ndjson", "jsonl"),
        ]
        
        for path, expected in test_cases:
            result = manager._detect_database_type(path)
            self.assertEqual(result, expected, f"Failed for {path}")

    def test_detect_database_type_compressed_duckdb_formats(self):
        """Should detect compressed and tabular DuckDB-readable formats."""
        from thepipe.database_utils import DatabaseManager

        manager = DatabaseManager.__new__(DatabaseManager)

        test_cases = [
            ("/data/file.json", "json"),
            ("/data/file.json.gz", "json"),
            ("/data/file.jsonl.gz", "jsonl"),
            ("/data/file.ndjson.gz", "jsonl"),
            ("/data/file.csv.gz", "csv"),
            ("/data/file.tsv", "csv"),
            ("/data/file.tsv.gz", "csv"),
        ]

        for path, expected in test_cases:
            result = manager._detect_database_type(path)
            self.assertEqual(result, expected, f"Failed for {path}")

    def test_detect_database_type_supported_directory(self):
        """Should detect supported shard directories for explicit DB mode."""
        from thepipe.database_utils import DatabaseManager

        manager = DatabaseManager.__new__(DatabaseManager)

        with tempfile.TemporaryDirectory() as temp_dir:
            shard_dir = Path(temp_dir)
            (shard_dir / "0001.json.gz").write_bytes(b"")
            (shard_dir / "0002.json.gz").write_bytes(b"")

            result = manager._detect_database_type(str(shard_dir))
            self.assertEqual(result, "json")

    def test_duckdb_source_view_uses_unified_view_name(self):
        """DuckDB-backed file sources should create the unified source_data view."""
        from thepipe.database_utils import DatabaseManager

        mock_db = mock.MagicMock()

        with mock.patch("thepipe.database_utils.Database", return_value=mock_db):
            manager = DatabaseManager("/tmp/example.json.gz", verbose=False)

        execute_sql = mock_db.execute.call_args[0][0]
        self.assertIn("CREATE VIEW source_data AS SELECT * FROM read_json_auto", execute_sql)
        self.assertIn("/tmp/example.json.gz", execute_sql)
        self.assertIn("ignore_errors=true", execute_sql)
        manager.close()


@unittest.skipUnless(JUPYSQL_AVAILABLE, "requires jupysql")
class TestJSONLHandling(unittest.TestCase):
    """Tests for JSONL file handling."""
    
    def test_jsonl_query(self):
        """Should be able to query JSONL files via DuckDB."""
        from thepipe.database_utils import DatabaseManager
        
        # Create temp JSONL file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write('{"name": "Alice", "age": 30}\n')
            f.write('{"name": "Bob", "age": 25}\n')
            f.write('{"name": "Charlie", "age": 35}\n')
            jsonl_path = f.name
        
        try:
            manager = DatabaseManager(jsonl_path, verbose=False)
            
            # Query should work - execute_query returns list of Chunks
            results = manager.execute_query("SELECT name, age FROM source_data ORDER BY age")
            
            # Results is a list of Chunks (schema + query result)
            self.assertIsInstance(results, list)
            self.assertGreater(len(results), 0)
            
            # Find the query result chunk
            query_chunk = None
            for chunk in results:
                if hasattr(chunk, 'path') and 'query' in chunk.path:
                    query_chunk = chunk
                    break
            
            self.assertIsNotNone(query_chunk, "Should have a query result chunk")
            
            # Check the results contain expected data
            result_text = query_chunk.text if hasattr(query_chunk, 'text') else str(query_chunk)
            self.assertIn('Bob', result_text)
            self.assertIn('Alice', result_text)
            self.assertIn('Charlie', result_text)
            
            manager.close()
        finally:
            os.unlink(jsonl_path)

    def test_json_gz_query(self):
        """Should be able to query gzip-compressed JSON through the explicit DB path."""
        from thepipe.database_utils import process_database

        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as f:
            json_gz_path = f.name

        try:
            with gzip.open(json_gz_path, "wt", encoding="utf-8") as gz:
                gz.write('{"name": "Alice", "age": 30}\n')
                gz.write('{"name": "Bob", "age": 25}\n')

            chunks = process_database(
                connection_info=json_gz_path,
                query="SELECT name, age FROM source_data ORDER BY age",
                verbose=False,
            )

            self.assertIsInstance(chunks, list)
            schema_chunk = next(chunk for chunk in chunks if "schema" in chunk.path)
            query_chunk = next(chunk for chunk in chunks if "query" in chunk.path)
            self.assertIn("forgiving mode", schema_chunk.text)
            self.assertIn("ignore_errors=true", schema_chunk.text)
            self.assertIn("Bob", query_chunk.text)
            self.assertIn("Alice", query_chunk.text)
        finally:
            os.unlink(json_gz_path)

    def test_json_gz_malformed_skips_bad_rows_in_forgiving_mode(self):
        """Malformed compressed JSON should be readable in forgiving mode with a warning."""
        from thepipe.database_utils import process_database

        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as f:
            json_gz_path = f.name

        try:
            with gzip.open(json_gz_path, "wt", encoding="utf-8") as gz:
                gz.write('{"name": "Alice", "age": 30}\n')
                gz.write('{"name": "Broken", "age": }\n')
                gz.write('{"name": "Charlie", "age": 35}\n')

            chunks = process_database(
                connection_info=json_gz_path,
                query="SELECT name, age FROM source_data ORDER BY age",
                verbose=False,
            )

            self.assertIsInstance(chunks, list)
            schema_chunk = next(chunk for chunk in chunks if "schema" in chunk.path)
            query_chunk = next(chunk for chunk in chunks if "query" in chunk.path)
            self.assertIn("ignore_errors=true", schema_chunk.text)
            self.assertIn("ignore_errors=true", query_chunk.text)
            self.assertIn("forgiving mode", schema_chunk.text)
            self.assertIn("Alice", query_chunk.text)
            self.assertIn("Charlie", query_chunk.text)
            self.assertNotIn("Broken", query_chunk.text)
        finally:
            os.unlink(json_gz_path)

    def test_csv_gz_query(self):
        """Should be able to query gzip-compressed CSV through DuckDB-backed DB mode."""
        from thepipe.database_utils import process_database

        with tempfile.NamedTemporaryFile(suffix=".csv.gz", delete=False) as f:
            csv_gz_path = f.name

        try:
            with gzip.open(csv_gz_path, "wt", encoding="utf-8") as gz:
                gz.write("name,age\n")
                gz.write("Alice,30\n")
                gz.write("Bob,25\n")

            chunks = process_database(
                connection_info=csv_gz_path,
                query="SELECT name, age FROM source_data ORDER BY age",
                verbose=False,
            )

            self.assertIsInstance(chunks, list)
            schema_chunk = next(chunk for chunk in chunks if "schema" in chunk.path)
            query_chunk = next(chunk for chunk in chunks if "query" in chunk.path)
            self.assertIn("forgiving mode", schema_chunk.text)
            self.assertIn("ignore_errors=true", schema_chunk.text)
            self.assertIn("Bob", query_chunk.text)
            self.assertIn("Alice", query_chunk.text)
        finally:
            os.unlink(csv_gz_path)


class TestODBCHandling(unittest.TestCase):
    def test_odbc_missing_pyodbc_fails_clearly(self):
        from thepipe.database_utils import DatabaseManager

        with mock.patch(
            "thepipe.database_utils.importlib.import_module",
            side_effect=ImportError("No module named pyodbc"),
        ):
            with self.assertRaisesRegex(ValueError, "pyodbc"):
                DatabaseManager("odbc://?connect=DRIVER%3DSQLite3%3BDatabase%3D/tmp/demo.db")

    def test_odbc_query_schema_and_preview(self):
        from thepipe.database_utils import DatabaseManager, process_database

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        seed = sqlite3.connect(db_path)
        try:
            seed.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, total REAL)")
            seed.execute("INSERT INTO orders (customer, total) VALUES ('Alice', 10.5)")
            seed.execute("INSERT INTO orders (customer, total) VALUES ('Bob', 20.0)")
            seed.commit()
        finally:
            seed.close()

        calls = []

        def fake_import(name):
            if name != "pyodbc":
                raise ImportError(name)
            return types.SimpleNamespace(
                connect=lambda connect_string, autocommit=True: (
                    calls.append((connect_string, autocommit)) or _SQLiteOdbcConnection(db_path)
                )
            )

        connect_url = f"odbc://?connect={quote(f'DRIVER=SQLite3;Database={db_path}', safe='')}"

        try:
            with mock.patch("thepipe.database_utils.importlib.import_module", side_effect=fake_import):
                manager = DatabaseManager(connect_url, verbose=False)
                schema_chunk = manager.get_schema()
                preview_chunk = manager.get_preview()
                query_chunks = manager.execute_query(
                    'SELECT customer, total FROM "orders" ORDER BY total'
                )
                manager.close()

            self.assertEqual(calls[0][0], f"DRIVER=SQLite3;Database={db_path}")
            self.assertTrue(calls[0][1])
            self.assertIn("### Table: orders", schema_chunk.text)
            self.assertIn("| customer | TEXT |", schema_chunk.text)
            self.assertIn("Row count: 2", preview_chunk.text)
            self.assertIn("Alice", preview_chunk.text)
            query_chunk = next(chunk for chunk in query_chunks if "query" in chunk.path)
            self.assertIn("Alice", query_chunk.text)
            self.assertIn("Bob", query_chunk.text)

            with mock.patch("thepipe.database_utils.importlib.import_module", side_effect=fake_import):
                process_chunks = process_database(
                    connection_info=connect_url,
                    query='SELECT customer, total FROM "orders" ORDER BY total',
                    verbose=False,
                )

            process_query_chunk = next(chunk for chunk in process_chunks if "query" in chunk.path)
            self.assertIn("Alice", process_query_chunk.text)
            self.assertIn("Bob", process_query_chunk.text)
        finally:
            os.unlink(db_path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
