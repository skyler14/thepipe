"""
Tests for database_utils.py - database format detection, JDBC URL conversion, and query execution.
"""

import os
import sys
import tempfile
import unittest
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

JUPYSQL_AVAILABLE = importlib.util.find_spec("jupysql") is not None


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
            results = manager.execute_query("SELECT name, age FROM jsonl_data ORDER BY age")
            
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
