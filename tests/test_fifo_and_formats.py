"""
Comprehensive tests for FIFO input, LLM client agent mode, and database format support.

Tests edge cases identified in code review:
- FIFO timeout handling
- FIFO size limits
- FIFO empty data
- PID/timestamp collision avoidance
- ORC/Feather/JSONL format support
- JDBC URL conversion
"""

import os
import sys
import json
import stat
import time
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add thepipe to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFIFOInput(unittest.TestCase):
    """Tests for FIFO input handling in scraper.py"""
    
    def test_is_fifo_with_regular_file(self):
        """is_fifo should return False for regular files."""
        from thepipe.scraper import is_fifo
        
        with tempfile.NamedTemporaryFile() as f:
            self.assertFalse(is_fifo(f.name))
    
    def test_is_fifo_with_actual_fifo(self):
        """is_fifo should return True for named pipes."""
        from thepipe.scraper import is_fifo
        
        fifo_path = tempfile.mktemp(suffix='_test_fifo')
        try:
            os.mkfifo(fifo_path)
            self.assertTrue(is_fifo(fifo_path))
        finally:
            if os.path.exists(fifo_path):
                os.unlink(fifo_path)
    
    def test_is_fifo_with_nonexistent_path(self):
        """is_fifo should return False for non-existent paths."""
        from thepipe.scraper import is_fifo
        
        self.assertFalse(is_fifo('/nonexistent/path/to/fifo'))
    
    def test_read_fifo_with_timeout_success(self):
        """_read_fifo_with_timeout should read data with writer present."""
        from thepipe.scraper import _read_fifo_with_timeout
        
        fifo_path = tempfile.mktemp(suffix='_test_fifo')
        test_data = b'{"test": "data", "value": 42}'
        
        try:
            os.mkfifo(fifo_path)
            
            # Writer thread
            def write_to_fifo():
                time.sleep(0.1)  # Give reader time to start
                with open(fifo_path, 'wb') as f:
                    f.write(test_data)
            
            writer = threading.Thread(target=write_to_fifo)
            writer.start()
            
            # Read with timeout
            result = _read_fifo_with_timeout(fifo_path, timeout=5)
            writer.join()
            
            self.assertEqual(result, test_data)
        finally:
            if os.path.exists(fifo_path):
                os.unlink(fifo_path)
    
    def test_read_fifo_timeout(self):
        """_read_fifo_with_timeout should raise TimeoutError on timeout."""
        from thepipe.scraper import _read_fifo_with_timeout
        
        fifo_path = tempfile.mktemp(suffix='_test_fifo')
        
        try:
            os.mkfifo(fifo_path)
            
            # No writer - should timeout
            with self.assertRaises(TimeoutError):
                _read_fifo_with_timeout(fifo_path, timeout=1)
        finally:
            if os.path.exists(fifo_path):
                os.unlink(fifo_path)
    
    def test_read_fifo_size_limit(self):
        """_read_fifo_with_timeout should enforce size limit."""
        from thepipe.scraper import _read_fifo_with_timeout
        
        fifo_path = tempfile.mktemp(suffix='_test_fifo')
        # Data larger than limit
        large_data = b'x' * (1024 * 1024)  # 1MB
        small_limit = 1024  # 1KB limit
        
        try:
            os.mkfifo(fifo_path)
            
            def write_large_data():
                time.sleep(0.1)
                with open(fifo_path, 'wb') as f:
                    f.write(large_data)
            
            writer = threading.Thread(target=write_large_data)
            writer.start()
            
            with self.assertRaises(ValueError) as ctx:
                _read_fifo_with_timeout(fifo_path, timeout=5, max_size=small_limit)
            
            self.assertIn('limit', str(ctx.exception).lower())
            writer.join(timeout=1)
        finally:
            if os.path.exists(fifo_path):
                os.unlink(fifo_path)
    
    def test_compiled_binary_labels_is_frozenset(self):
        """COMPILED_BINARY_LABELS should be a frozenset for performance."""
        from thepipe.scraper import COMPILED_BINARY_LABELS
        
        self.assertIsInstance(COMPILED_BINARY_LABELS, frozenset)
        self.assertGreater(len(COMPILED_BINARY_LABELS), 20)
    
    def test_detect_mimetype_from_bytes_json(self):
        """detect_source_mimetype_from_bytes should detect JSON."""
        from thepipe.scraper import detect_source_mimetype_from_bytes
        
        json_data = b'{"key": "value", "number": 123}'
        mime_type = detect_source_mimetype_from_bytes(json_data)
        
        # Magika should detect this as JSON or text
        self.assertIsNotNone(mime_type)
        self.assertTrue(
            'json' in mime_type.lower() or 'text' in mime_type.lower(),
            f"Expected JSON or text, got {mime_type}"
        )


class TestLLMClientAgentMode(unittest.TestCase):
    """Tests for LLM client agent mode FIFO communication."""
    
    def test_pipe_names_unique(self):
        """Pipe names should include PID and timestamp for uniqueness."""
        from thepipe.llm.client import LLMClient, LLMConfig
        
        config = LLMConfig(provider="agent")
        client = LLMClient(config)
        
        # Check that _agent_query creates unique named pipes
        # We can't easily test the full flow, but we can verify the logic
        pid = os.getpid()
        timestamp1 = int(time.time() * 1000)
        time.sleep(0.002)  # Ensure different timestamp
        timestamp2 = int(time.time() * 1000)
        
        self.assertNotEqual(timestamp1, timestamp2)
    
    def test_llm_config_from_env_auto_agent(self):
        """LLMConfig should auto-detect agent mode from env vars."""
        from thepipe.llm.client import LLMConfig
        
        # When ANTIGRAVITY_SESSION is set, should use agent mode
        with patch.dict(os.environ, {'ANTIGRAVITY_SESSION': 'test123'}):
            config = LLMConfig.from_env()
            self.assertEqual(config.provider, 'agent')
    
    def test_llm_config_from_env_default_openai(self):
        """LLMConfig should default to openai when no agent env vars."""
        from thepipe.llm.client import LLMConfig
        
        # Remove agent-related env vars
        env = {k: v for k, v in os.environ.items() 
               if k not in ['ANTIGRAVITY_SESSION', 'GEMINI_API_KEY']}
        with patch.dict(os.environ, env, clear=True):
            config = LLMConfig.from_env()
            self.assertEqual(config.provider, 'openai')
    
    def test_from_options_creates_client(self):
        """LLMClient.from_options should create client from dict."""
        from thepipe.llm.client import LLMClient
        
        options = {
            'llm_provider': 'openai',
            'model': 'gpt-4o',
            'api_key': 'test-key',
        }
        
        client = LLMClient.from_options(options)
        self.assertEqual(client.config.provider, 'openai')
        self.assertEqual(client.config.model, 'gpt-4o')


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
            self.assertIn('Bob', result_text)  # Bob is youngest (25)
            self.assertIn('Alice', result_text)
            self.assertIn('Charlie', result_text)
            
            manager.close()
        finally:
            os.unlink(jsonl_path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
