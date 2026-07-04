"""
Enhanced database utilities module that integrates with JupySQL middleware.
This module provides a clean interface for database operations in thepipe.
"""

from typing import Dict, List, Optional, Any, Union, Tuple
import importlib
import logging
import os
import pandas as pd
import json
import re
from pathlib import Path
import time
from urllib.parse import parse_qs, urlparse

from .core import Chunk

# Import the JupySQL middleware
from .jupysql_middleware import Database
from .database_analysis import execute_fallback, format_analysis_for_llm, get_all_tables, get_auto_analysis, get_schema_for_all_tables, fix_sql_syntax
# Constants
DEFAULT_MAX_ROWS = 15
DEFAULT_PREVIEW_ROWS = 5
DUCKDB_SOURCE_VIEW = "source_data"
DUCKDB_FILE_SOURCE_TYPES = {"parquet", "csv", "excel", "orc", "feather", "json", "jsonl"}
DUCKDB_FORGIVING_SOURCE_TYPES = {"json", "jsonl", "csv"}

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manager class that handles database operations through JupySQL middleware.
    Provides a unified interface for thepipe to interact with databases.
    """
    
    def __init__(self, connection_info: Union[str, Dict], 
                db_type: Optional[str] = None,
                verbose: bool = False,
                options: Optional[Dict[str, Any]] = None):
        """
        Initialize database manager with connection info and options.
        
        Args:
            connection_info: Connection string or dictionary with connection parameters
            db_type: Optional database type for type-specific handling
            verbose: Enable verbose logging
            options: Additional options for customizing behavior
        """
        self.connection_info = connection_info
        self.db_type = db_type or self._detect_database_type(connection_info)
        self.verbose = verbose
        self.options = options or {}  # Store options for use in other methods
        self.db = None
        self._odbc_connection = None
        self._duckdb_config_dict: Optional[Dict[str, Any]] = None
        self._duckdb_read_mode = self._resolve_duckdb_read_mode()
        self._duckdb_read_warning: Optional[str] = None
        self._connect()
        
    def _detect_database_type(self, source: Union[str, Dict]) -> str:
        """Detect database type from connection string or configuration."""
        if isinstance(source, str):
            # JDBC URL support - convert to standard format
            if source.startswith("jdbc:"):
                source = self._convert_jdbc_url(source)
                self.connection_info = source  # Update stored connection
            
            if source.startswith("postgresql://") or source.startswith("postgres://"):
                return "postgres"
            elif source.startswith("mysql://") or source.startswith("mysql+pymysql://") or source.startswith("mariadb://"):
                return "mysql"  # MariaDB uses same driver as MySQL
            elif source.startswith("sqlite://"):
                return "sqlite"
            elif source.startswith("mssql://") or source.startswith("mssql+pyodbc://"):
                return "mssql"
            elif source.startswith("odbc://"):
                return "odbc"
            elif source.startswith("duckdb://"):
                return "duckdb"
            lower_source = source.lower()
            if lower_source.endswith((".parquet", ".parq")) or "/parquet/" in lower_source or "*.parquet" in lower_source:
                return "parquet"
            elif lower_source.endswith(".orc"):
                return "orc"
            elif lower_source.endswith((".feather", ".arrow", ".ipc")):
                return "feather"
            elif lower_source.endswith((".jsonl", ".ndjson", ".jsonl.gz", ".ndjson.gz")):
                return "jsonl"
            elif lower_source.endswith((".json", ".json.gz")):
                return "json"
            elif lower_source.endswith((".csv", ".csv.gz", ".tsv", ".tsv.gz")):
                return "csv"
            elif lower_source.endswith((".xlsx", ".xls")):
                return "excel"
            elif lower_source.endswith(".duckdb") or lower_source.endswith(".db"):
                return "duckdb"
            elif os.path.isdir(source):
                directory_type = self._detect_directory_source_type(source)
                if directory_type:
                    return directory_type
        elif isinstance(source, dict) and "type" in source:
            return source["type"]
            
        return "unknown"

    def _detect_directory_source_type(self, directory: str) -> Optional[str]:
        family_to_extensions = {
            "parquet": (".parquet", ".parq"),
            "jsonl": (".jsonl", ".ndjson", ".jsonl.gz", ".ndjson.gz"),
            "json": (".json", ".json.gz"),
            "csv": (".csv", ".csv.gz", ".tsv", ".tsv.gz"),
        }

        counts = {family: 0 for family in family_to_extensions}
        for path in Path(directory).rglob("*"):
            if not path.is_file():
                continue
            lower_name = path.name.lower()
            for family, extensions in family_to_extensions.items():
                if lower_name.endswith(extensions):
                    counts[family] += 1
                    break

        best_family = max(counts, key=counts.get)
        return best_family if counts[best_family] > 0 else None

    def _uses_duckdb_source_view(self) -> bool:
        return self.db_type in DUCKDB_FILE_SOURCE_TYPES

    def _is_odbc(self) -> bool:
        return self.db_type == "odbc"

    def _resolve_duckdb_read_mode(self) -> str:
        requested_mode = str(self.options.get("db_read_mode", "")).strip().lower()
        if requested_mode in {"strict", "forgiving"}:
            return requested_mode
        if self.db_type in DUCKDB_FORGIVING_SOURCE_TYPES:
            return "forgiving"
        return "strict"

    def _sql_literal(self, value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

    def _duckdb_source_paths(self) -> List[str]:
        if not isinstance(self.connection_info, str):
            raise ValueError("DuckDB file sources require a string path")

        source = Path(self.connection_info)
        if source.is_dir():
            family = self._detect_directory_source_type(str(source))
            if not family:
                raise ValueError(f"Could not find DuckDB-readable files in directory: {source}")
            patterns = {
                "parquet": ("*.parquet", "*.parq"),
                "jsonl": ("*.jsonl", "*.ndjson", "*.jsonl.gz", "*.ndjson.gz"),
                "json": ("*.json", "*.json.gz"),
                "csv": ("*.csv", "*.csv.gz", "*.tsv", "*.tsv.gz"),
            }[family]
            files: List[str] = []
            for pattern in patterns:
                files.extend(str(path) for path in source.rglob(pattern))
            if not files:
                raise ValueError(f"No files matched supported DuckDB patterns in {source}")
            return sorted(set(files))
        return [str(source)]

    def _duckdb_source_argument(self, paths: List[str]) -> str:
        if len(paths) == 1:
            return self._sql_literal(paths[0])
        return "[" + ", ".join(self._sql_literal(path) for path in paths) + "]"

    def _create_duckdb_source_view(self) -> None:
        if not self.db:
            raise ValueError("DuckDB database connection is not initialized")

        ignore_errors = (
            self._duckdb_read_mode == "forgiving"
            and self.db_type in DUCKDB_FORGIVING_SOURCE_TYPES
        )

        if self.db_type == "excel":
            import pandas as pd
            import tempfile

            if self.verbose:
                print(f"[thepipe] Reading Excel file: {self.connection_info}")

            df = pd.read_excel(self.connection_info)
            with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as temp_csv:
                temp_path = temp_csv.name
                df.to_csv(temp_path, index=False)
            self._temp_path = temp_path
            source_arg = self._sql_literal(temp_path)
            create_view_sql = f"CREATE VIEW {DUCKDB_SOURCE_VIEW} AS SELECT * FROM read_csv_auto({source_arg})"
        elif self.db_type in {"json", "jsonl"}:
            source_arg = self._duckdb_source_argument(self._duckdb_source_paths())
            ignore_clause = ", ignore_errors=true" if ignore_errors else ""
            create_view_sql = f"CREATE VIEW {DUCKDB_SOURCE_VIEW} AS SELECT * FROM read_json_auto({source_arg}{ignore_clause})"
        elif self.db_type == "csv":
            source_arg = self._duckdb_source_argument(self._duckdb_source_paths())
            ignore_clause = ", ignore_errors=true" if ignore_errors else ""
            create_view_sql = f"CREATE VIEW {DUCKDB_SOURCE_VIEW} AS SELECT * FROM read_csv_auto({source_arg}{ignore_clause})"
        elif self.db_type == "parquet":
            source_arg = self._duckdb_source_argument(self._duckdb_source_paths())
            create_view_sql = f"CREATE VIEW {DUCKDB_SOURCE_VIEW} AS SELECT * FROM read_parquet({source_arg})"
        elif self.db_type == "orc":
            source_arg = self._duckdb_source_argument(self._duckdb_source_paths())
            create_view_sql = f"CREATE VIEW {DUCKDB_SOURCE_VIEW} AS SELECT * FROM read_orc({source_arg})"
        elif self.db_type == "feather":
            source_arg = self._duckdb_source_argument(self._duckdb_source_paths())
            create_view_sql = f"CREATE VIEW {DUCKDB_SOURCE_VIEW} AS SELECT * FROM {source_arg}"
        else:
            raise ValueError(f"Unsupported DuckDB source type: {self.db_type}")

        if self.verbose:
            print(f"[thepipe] Creating DuckDB source view: {create_view_sql}")
        self.db.execute(create_view_sql)
        if ignore_errors:
            self._duckdb_read_warning = (
                f"Note: DuckDB is reading this {self.db_type} source in forgiving mode "
                f"(`ignore_errors=true`); malformed rows may be skipped."
            )

    def _new_duckdb_file_source_db(self):
        if self._duckdb_config_dict is None:
            raise ValueError("DuckDB file-source config is not initialized")
        return Database("duckdb://", config_dict=self._duckdb_config_dict)

    def _prepend_duckdb_read_warning(self, text: str) -> str:
        if not self._duckdb_read_warning:
            return text
        return f"{self._duckdb_read_warning}\n\n{text}"

    def _parse_odbc_connect_string(self) -> str:
        if not isinstance(self.connection_info, str):
            raise ValueError("ODBC connections require a string source")

        parsed = urlparse(self.connection_info)
        connect = parse_qs(parsed.query).get("connect", [""])[0]
        if not connect:
            raise ValueError(
                "ODBC connections require a URL like "
                "`odbc://?connect=<urlencoded ODBC connection string>`"
            )
        return connect

    def _quote_identifier(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def _list_odbc_tables(self) -> List[str]:
        if not self._odbc_connection:
            return []

        cursor = self._odbc_connection.cursor()
        try:
            tables = []
            seen = set()
            for row in cursor.tables():
                table_name = getattr(row, "table_name", None)
                table_type = str(getattr(row, "table_type", "") or "").upper()
                if not table_name or table_name in seen:
                    continue
                if table_type and table_type not in {"TABLE", "VIEW"}:
                    continue
                seen.add(table_name)
                tables.append(table_name)
            return tables
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _get_odbc_columns(self, table: str) -> List[Any]:
        if not self._odbc_connection:
            return []

        cursor = self._odbc_connection.cursor()
        try:
            return list(cursor.columns(table=table))
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _execute_odbc_query(self, query: str, params: Optional[Union[List[Any], Tuple[Any, ...]]] = None) -> Optional[pd.DataFrame]:
        if not self._odbc_connection:
            raise ValueError("ODBC connection is not initialized")

        cursor = self._odbc_connection.cursor()
        try:
            if params is None:
                cursor.execute(query)
            elif isinstance(params, (list, tuple)):
                cursor.execute(query, params)
            else:
                raise ValueError("ODBC query params must be a list or tuple")

            if cursor.description:
                columns = [column[0] for column in cursor.description]
                rows = cursor.fetchall()
                return pd.DataFrame.from_records(rows, columns=columns)

            try:
                self._odbc_connection.commit()
            except Exception:
                pass
            return None
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def _convert_jdbc_url(self, jdbc_url: str) -> str:
        """Convert JDBC URL to SQLAlchemy-compatible format.
        
        Supported formats:
        - jdbc:mysql://host:port/database -> mysql+pymysql://host:port/database
        - jdbc:mariadb://host:port/database -> mysql+pymysql://host:port/database
        - jdbc:postgresql://host:port/database -> postgresql://host:port/database
        - jdbc:sqlite:/path/to/db -> sqlite:///path/to/db
        - jdbc:sqlserver://host:port;databaseName=db -> mssql+pyodbc://host:port/db
        """
        if jdbc_url.startswith("jdbc:mysql://"):
            return jdbc_url.replace("jdbc:mysql://", "mysql+pymysql://")
        elif jdbc_url.startswith("jdbc:mariadb://"):
            # MariaDB uses same pymysql driver
            return jdbc_url.replace("jdbc:mariadb://", "mysql+pymysql://")
        elif jdbc_url.startswith("jdbc:postgresql://"):
            return jdbc_url.replace("jdbc:postgresql://", "postgresql://")
        elif jdbc_url.startswith("jdbc:sqlite:"):
            # jdbc:sqlite:/path/to/db -> sqlite:///path/to/db
            path = jdbc_url.replace("jdbc:sqlite:", "")
            return f"sqlite:///{path}"
        elif jdbc_url.startswith("jdbc:sqlserver://"):
            url = jdbc_url.replace("jdbc:sqlserver://", "")
            if "databaseName=" in url:
                parts = url.split(";")
                host_port = parts[0]
                db_name = ""
                for part in parts[1:]:
                    if part.startswith("databaseName="):
                        db_name = part.replace("databaseName=", "")
                return f"mssql+pyodbc://{host_port}/{db_name}?driver=ODBC+Driver+17+for+SQL+Server"
            return f"mssql+pyodbc://{url}"
        else:
            # Unsupported JDBC, return as-is
            return jdbc_url
    
    def _connect(self):
        """Establish connection to the database using JupySQL middleware."""
        try:
            # Configure JupySQL middleware options
            config_dict = {
                "autopandas": True,
                "autopolars": False,
                "autocommit": True,
                "feedback": False if not self.verbose else True,
                "autolimit": self.options.get("max_rows", DEFAULT_MAX_ROWS),
                "displaylimit": self.options.get("max_rows", DEFAULT_MAX_ROWS),
            }
            self._duckdb_config_dict = config_dict
            
            if self.verbose:
                print(f"[thepipe] Connecting to {self.db_type} database")
                print(f"[thepipe] Connection info: {self.connection_info if isinstance(self.connection_info, str) else 'dict'}")
            
            # DuckDB-backed file-like sources
            if self._uses_duckdb_source_view():
                self.db = self._new_duckdb_file_source_db()
                self._create_duckdb_source_view()
            elif self._is_odbc():
                connect_string = self._parse_odbc_connect_string()
                try:
                    pyodbc = importlib.import_module("pyodbc")
                except ImportError as e:
                    raise ImportError(
                        "ODBC support requires optional dependency `pyodbc`. "
                        "Install it and ensure the target ODBC driver is available."
                    ) from e
                self._odbc_connection = pyodbc.connect(connect_string, autocommit=True)
                self.db = self._odbc_connection
            elif self.db_type == "duckdb":
                # Handle DuckDB database files directly
                if self.connection_info.startswith("duckdb://"):
                    connection_str = self.connection_info
                else:
                    # It's a .duckdb file path
                    connection_str = f"duckdb:///{self.connection_info}"
                
                if self.verbose:
                    print(f"[thepipe] Connecting to DuckDB: {connection_str}")
                    
                self.db = Database(connection_str, config_dict=config_dict)
            else:
                # Standard database connection
                if isinstance(self.connection_info, str):
                    connection_str = self.connection_info
                else:
                    # Create connection string from dictionary
                    connection_str = self._create_connection_string(
                        self.connection_info, self.db_type)
                
                # Normalize connection URLs to SQLAlchemy format
                # mysql:// -> mysql+pymysql://
                if connection_str.startswith("mysql://"):
                    connection_str = connection_str.replace("mysql://", "mysql+pymysql://", 1)
                # mariadb:// -> mysql+pymysql:// (compatible driver)
                elif connection_str.startswith("mariadb://"):
                    connection_str = connection_str.replace("mariadb://", "mysql+pymysql://", 1)
                # postgres:// -> postgresql:// (SQLAlchemy standard)
                elif connection_str.startswith("postgres://"):
                    connection_str = connection_str.replace("postgres://", "postgresql://", 1)
                # mssql:// -> mssql+pyodbc://
                elif connection_str.startswith("mssql://"):
                    connection_str = connection_str.replace("mssql://", "mssql+pyodbc://", 1)
                
                if self.verbose:
                    print(f"[thepipe] Connecting to database with connection string: {connection_str}")
                    
                self.db = Database(connection_str, config_dict=config_dict)
            
            if self.verbose:
                print(f"[thepipe] Connected to {self.db_type} database")
                
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error connecting to database: {str(e)}")
                import traceback
                traceback.print_exc()
            raise ValueError(f"Failed to connect to database: {str(e)}")

    def _create_connection_string(self, config: Dict, db_type: str) -> str:
        """Create a connection string from configuration dictionary."""
        if db_type == "postgres":
            return f"postgresql://{config.get('user')}:{config.get('password')}@{config.get('host')}:{config.get('port', 5432)}/{config.get('database')}"
        elif db_type == "mysql":
            return f"mysql+pymysql://{config.get('user')}:{config.get('password')}@{config.get('host')}:{config.get('port', 3306)}/{config.get('database')}"
        elif db_type == "sqlite":
            return f"sqlite:///{config.get('database')}"
        elif db_type == "mssql":
            return f"mssql+pyodbc://{config.get('user')}:{config.get('password')}@{config.get('host')}:{config.get('port', 1433)}/{config.get('database')}"
        else:
            raise ValueError(f"Unsupported database type: {db_type}")
    
    def get_schema(self) -> Chunk:
        """
        Extract database schema information and return as a Chunk.
        
        Returns:
            Chunk object containing schema information in markdown format
        """
        try:
            if self.verbose:
                print(f"[thepipe] Getting schema for {self.db_type} database")

            if self._is_odbc():
                tables = self._list_odbc_tables()
                schema_info = "## Database Schema\n\n"

                if not tables:
                    schema_info += "*No tables found*\n"

                for table in tables:
                    schema_info += f"### Table: {table}\n\n"
                    schema_info += "| Column | Type | Nullable | Default |\n"
                    schema_info += "|--------|------|----------|---------|\n"
                    try:
                        columns = self._get_odbc_columns(table)
                        if not columns:
                            schema_info += "*Schema information not available*\n\n"
                            continue
                        for column in columns:
                            column_name = getattr(column, "column_name", "")
                            type_name = getattr(column, "type_name", "") or "unknown"
                            nullable = getattr(column, "nullable", "")
                            default = getattr(column, "column_def", None) or "NULL"
                            schema_info += f"| {column_name} | {type_name} | {nullable} | {default} |\n"
                    except Exception as e:
                        schema_info += f"*Error retrieving schema: {str(e)}*\n"
                    schema_info += "\n"
            elif self._uses_duckdb_source_view():
                # Get schema from the first few rows of the DuckDB-backed source view
                view_name = DUCKDB_SOURCE_VIEW
                
                if self.verbose:
                    print(f"[thepipe] Using view name: {view_name}")
                
                try:
                    # Query the view to get schema information
                    query = f"SELECT * FROM {view_name} LIMIT 1"
                    if self.verbose:
                        print(f"[thepipe] Executing query to get schema: {query}")
                        
                    df = self.db.query(query)
                    
                    if self.verbose:
                        print(f"[thepipe] DataFrame columns: {df.columns.tolist()}")
                        print(f"[thepipe] DataFrame dtypes: {df.dtypes}")
                    
                    schema_info = "## Database Schema\n\n"
                    schema_info += f"### Table: {view_name}\n\n"
                    schema_info += "| Column | Type |\n"
                    schema_info += "|--------|------|\n"
                    
                    for col_name, dtype in df.dtypes.items():
                        schema_info += f"| {col_name} | {dtype} |\n"
                except Exception as e:
                    if self.verbose:
                        print(f"[thepipe] Error querying view: {str(e)}")
                    schema_info = f"## Database Schema\n\nError querying {self.db_type} data: {str(e)}"
            else:
                # For standard databases, get all tables and their schemas
                schema_info = "## Database Schema\n\n"
                
                # Get list of tables
                if self.db_type == "sqlite":
                    tables_df = self.db.query("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = tables_df['name'].tolist()
                elif self.db_type in ["postgres", "mysql"]:
                    tables_df = self.db.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                    tables = tables_df['table_name'].tolist()
                elif self.db_type == "mssql":
                    tables_df = self.db.query("SELECT table_name FROM information_schema.tables")
                    tables = tables_df['table_name'].tolist()
                else:
                    # Fallback - try a general approach
                    try:
                        result = self.db.execute("SHOW TABLES")
                        tables = result.iloc[:, 0].tolist() if not result.empty else []
                    except Exception as e:
                        logger.error(f"Failed to retrieve table list: {e}", exc_info=True)
                        return Chunk(
                            path=f"database://{self.db_type}/schema",
                            text=f"Could not retrieve schema information: {type(e).__name__}: {e}"
                        )
                
                if self.verbose:
                    print(f"[thepipe] Found tables: {tables}")
                
                # Get schema for each table
                for table in tables:
                    schema_info += f"### Table: {table}\n\n"
                    try:
                        if self.db_type == "sqlite":
                            columns_df = self.db.query(f"PRAGMA table_info({table})")
                            schema_info += "| Column | Type | Nullable | Default | Primary Key |\n"
                            schema_info += "|--------|------|----------|---------|------------|\n"
                            for _, row in columns_df.iterrows():
                                schema_info += f"| {row['name']} | {row['type']} | {not row['notnull']} | {row['dflt_value'] or 'NULL'} | {row['pk'] == 1} |\n"
                        else:
                            # Generic approach using INFORMATION_SCHEMA
                            try:
                                columns_df = self.db.query(f"""
                                    SELECT column_name, data_type, is_nullable, column_default 
                                    FROM information_schema.columns 
                                    WHERE table_name = '{table}'
                                """)
                                schema_info += "| Column | Type | Nullable | Default |\n"
                                schema_info += "|--------|------|----------|--------|\n"
                                for _, row in columns_df.iterrows():
                                    schema_info += f"| {row['column_name']} | {row['data_type']} | {row['is_nullable']} | {row['column_default'] or 'NULL'} |\n"
                            except Exception as e:
                                logger.debug(f"Failed to get detailed schema for {table}: {e}")
                                # Fallback to basic DESCRIBE
                                try:
                                    describe_df = self.db.query(f"DESCRIBE {table}")
                                    schema_info += describe_df.to_markdown()
                                except Exception as e2:
                                    logger.warning(f"DESCRIBE also failed for {table}: {e2}")
                                    schema_info += f"*Schema information not available for this table*\n\n"
                    except Exception as e:
                        schema_info += f"*Error retrieving schema: {str(e)}*\n\n"
                    
                    schema_info += "\n\n"
            
            if self.verbose:
                print("[thepipe] Schema extraction complete")
                
            return Chunk(
                path=f"database://{self.db_type}/schema",
                text=self._prepend_duckdb_read_warning(schema_info)
            )
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error getting schema: {str(e)}")
                import traceback
                traceback.print_exc()
            return Chunk(
                path=f"database://{self.db_type}/schema",
                text=f"Error retrieving schema: {str(e)}"
            )

    def get_preview(self, max_rows: int = DEFAULT_PREVIEW_ROWS) -> Chunk:
        """
        Generate a preview of database contents.
        
        Args:
            max_rows: Maximum number of rows to include in the preview
            
        Returns:
            Chunk object containing data preview in markdown format
        """
        try:
            preview_text = "## Data Preview\n\n"

            if self._is_odbc():
                tables = self._list_odbc_tables()
                if not tables:
                    preview_text += "*No tables found*\n"

                for table in tables:
                    quoted_table = self._quote_identifier(table)
                    preview_text += f"### Table: {table}\n\n"
                    try:
                        count_df = self._execute_odbc_query(f"SELECT COUNT(*) AS count FROM {quoted_table}")
                        if isinstance(count_df, pd.DataFrame) and not count_df.empty:
                            preview_text += f"Row count: {int(count_df['count'].iloc[0]):,}\n\n"
                    except Exception as e:
                        preview_text += f"*Error getting row count: {str(e)}*\n\n"

                    try:
                        # ponytail: generic ODBC preview uses LIMIT; add driver-specific TOP/FETCH FIRST fallback only when a real backend needs it.
                        sample_df = self._execute_odbc_query(f"SELECT * FROM {quoted_table} LIMIT {max_rows}")
                        if isinstance(sample_df, pd.DataFrame) and not sample_df.empty:
                            preview_text += "Sample data:\n\n```\n"
                            preview_text += sample_df.to_string()
                            preview_text += "\n```\n\n"
                        else:
                            preview_text += "*No data in table*\n\n"
                    except Exception as e:
                        preview_text += f"*Error getting preview: {str(e)}*\n\n"
            elif self._uses_duckdb_source_view():
                view_name = DUCKDB_SOURCE_VIEW
                
                # Get row count
                count_df = self.db.query(f"SELECT COUNT(*) as count FROM {view_name}")
                count = count_df['count'].iloc[0]
                
                preview_text += f"### {view_name}\n\n"
                preview_text += f"Row count: {count:,}\n\n"
                
                # Get sample data
                sample_df = self.db.query(f"SELECT * FROM {view_name} LIMIT {max_rows}")
                preview_text += "Sample data:\n\n```\n"
                preview_text += sample_df.to_string()
                preview_text += "\n```\n\n"
                
                # Add basic statistics for each column
                preview_text += "#### Column Statistics\n\n"
                
                for col in sample_df.columns:
                    preview_text += f"**{col}**\n"
                    
                    try:
                        # For numeric columns
                        if pd.api.types.is_numeric_dtype(sample_df[col]):
                            stats_df = self.db.query(f"""
                                SELECT 
                                    MIN("{col}") as min,
                                    MAX("{col}") as max,
                                    AVG("{col}") as mean,
                                    COUNT(*) - COUNT("{col}") as null_count
                                FROM {view_name}
                            """)
                            preview_text += f"- Type: Numeric\n"
                            preview_text += f"- Min: {stats_df['min'].iloc[0]}\n"
                            preview_text += f"- Max: {stats_df['max'].iloc[0]}\n"
                            preview_text += f"- Mean: {stats_df['mean'].iloc[0]}\n"
                            preview_text += f"- Null count: {stats_df['null_count'].iloc[0]}\n"
                        else:
                            # For non-numeric columns
                            stats_df = self.db.query(f"""
                                SELECT 
                                    COUNT(DISTINCT "{col}") as unique_count,
                                    COUNT(*) - COUNT("{col}") as null_count
                                FROM {view_name}
                            """)
                            preview_text += f"- Type: {sample_df[col].dtype}\n"
                            preview_text += f"- Unique values: {stats_df['unique_count'].iloc[0]}\n"
                            preview_text += f"- Null count: {stats_df['null_count'].iloc[0]}\n"
                            
                            # Show top values if not too many unique values
                            if stats_df['unique_count'].iloc[0] <= 10:
                                top_values_df = self.db.query(f"""
                                    SELECT "{col}", COUNT(*) as count
                                    FROM {view_name}
                                    WHERE "{col}" IS NOT NULL
                                    GROUP BY "{col}"
                                    ORDER BY count DESC
                                    LIMIT 5
                                """)
                                preview_text += "- Most common values:\n"
                                for _, row in top_values_df.iterrows():
                                    preview_text += f"  - {row[col]}: {row['count']}\n"
                    except Exception as e:
                        preview_text += f"- Error analyzing column: {str(e)}\n"
                    
                    preview_text += "\n"
            else:
                # For standard databases, show preview of each table
                try:
                    # Get list of tables
                    if self.db_type == "sqlite":
                        tables_df = self.db.query("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = tables_df['name'].tolist()
                    elif self.db_type in ["postgres", "mysql"]:
                        tables_df = self.db.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                        tables = tables_df['table_name'].tolist()
                    elif self.db_type == "mssql":
                        tables_df = self.db.query("SELECT table_name FROM information_schema.tables")
                        tables = tables_df['table_name'].tolist()
                    else:
                        # Fallback
                        try:
                            result = self.db.execute("SHOW TABLES")
                            tables = result.iloc[:, 0].tolist() if not result.empty else []
                        except Exception as e:
                            logger.error(f"Failed to retrieve preview tables: {e}", exc_info=True)
                            return Chunk(
                                path=f"database://{self.db_type}/preview",
                                text=f"Could not retrieve table information: {type(e).__name__}: {e}"
                            )
                    
                    # Get preview for each table
                    for table in tables:
                        preview_text += f"### Table: {table}\n\n"
                        
                        try:
                            # Get row count
                            count_df = self.db.query(f"SELECT COUNT(*) as count FROM {table}")
                            count = count_df['count'].iloc[0]
                            preview_text += f"Row count: {count:,}\n\n"
                            
                            # Get sample data
                            if count > 0:
                                sample_df = self.db.query(f"SELECT * FROM {table} LIMIT {max_rows}")
                                preview_text += "Sample data:\n\n```\n"
                                preview_text += sample_df.to_string()
                                preview_text += "\n```\n\n"
                            else:
                                preview_text += "*No data in table*\n\n"
                        except Exception as e:
                            preview_text += f"*Error getting preview: {str(e)}*\n\n"
                except Exception as e:
                    preview_text += f"*Error listing tables: {str(e)}*\n\n"
            
            return Chunk(
                path=f"database://{self.db_type}/preview",
                text=self._prepend_duckdb_read_warning(preview_text)
            )
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error generating data preview: {str(e)}")
            return Chunk(
                path=f"database://{self.db_type}/preview",
                text=f"Error generating data preview: {str(e)}"
            )
    
    def execute_query(self, query: str, params: Optional[Any] = None) -> List[Chunk]:
        """
        Execute SQL query and return results as chunks.
        
        Args:
            query: SQL query to execute
            params: Optional query parameters
            
        Returns:
            List of Chunk objects with query results
        """
        if self._is_odbc():
            schema_chunk = self.get_schema()
            chunks = [schema_chunk]
        else:
            # Get tables and schema information
            tables = get_all_tables(self.db, self.db_type, self.verbose)
            view_name = tables[0] if tables else None

            # Get schema text
            schema_text = ""
            if view_name:
                schema_text = get_schema_for_all_tables(self.db, tables, self.verbose)

            # Always run auto-analysis with detailed output
            analysis_text = ""
            if view_name:
                try:
                    # Pass through any options for analysis
                    analysis_options = self.options.get("analysis", {}) if hasattr(self, "options") else {}

                    auto_analysis = get_auto_analysis(
                        self.db,
                        self.db_type,
                        view_name,
                        verbose=self.verbose,
                        options=analysis_options
                    )

                    # Format detailed analysis with full column statistics
                    analysis_text = "## Automatic Database Analysis\n\n"

                    # Add basic dataset info
                    if 'total_rows' in auto_analysis:
                        analysis_text += f"Total rows: {auto_analysis['total_rows']:,}\n"

                    if 'columns' in auto_analysis:
                        analysis_text += f"Total columns: {len(auto_analysis['columns'])}\n\n"
                        analysis_text += f"Columns: {', '.join(auto_analysis['columns'])}\n\n"

                    # Add column type categorization
                    if 'column_types' in auto_analysis:
                        cat_cols = auto_analysis['column_types'].get('categorical', [])
                        num_cols = auto_analysis['column_types'].get('numeric', [])

                        if cat_cols:
                            analysis_text += f"Categorical columns: {', '.join(cat_cols)}\n\n"

                        if num_cols:
                            analysis_text += f"Numeric columns: {', '.join(num_cols)}\n\n"

                    # Add detailed column statistics
                    if 'column_stats' in auto_analysis:
                        analysis_text += "### Column Statistics\n\n"

                        for col, stats in auto_analysis['column_stats'].items():
                            analysis_text += f"#### {col}\n"

                            if stats['type'] == 'categorical':
                                analysis_text += f"Type: Categorical\n"
                                if 'distinct_count' in stats:
                                    analysis_text += f"Distinct values: {stats['distinct_count']}\n"

                                if 'top_values' in stats:
                                    analysis_text += "Top values:\n"
                                    for val in stats['top_values']:
                                        analysis_text += f"- {val['value']}: {val['count']} ({val['percentage']:.2f}%)\n"

                            elif stats['type'] == 'numeric':
                                analysis_text += f"Type: Numeric\n"
                                if 'stats' in stats:
                                    stat_data = stats['stats']
                                    analysis_text += f"Range: {stat_data.get('min', 'N/A')} to {stat_data.get('max', 'N/A')}\n"
                                    analysis_text += f"Mean: {stat_data.get('mean', 'N/A')}\n"
                                    analysis_text += f"Null count: {stat_data.get('null_count', 'N/A')}\n"

                            analysis_text += "\n"

                    # Add key columns info
                    if 'potential_keys' in auto_analysis and auto_analysis['potential_keys']:
                        analysis_text += f"Potential key columns: {', '.join(auto_analysis['potential_keys'])}\n\n"

                    if 'date_columns' in auto_analysis and auto_analysis['date_columns']:
                        analysis_text += f"Date columns: {', '.join(auto_analysis['date_columns'])}\n\n"

                except Exception as e:
                    if self.verbose:
                        print(f"[thepipe] Error running auto analysis: {str(e)}")

            # Create combined schema and analysis chunk
            combined_text = schema_text
            if analysis_text:
                combined_text += f"\n\n{analysis_text}"

            # TODO(database-graph): consider projecting schema metadata into the same
            # graph query shape as codegraph: (Database)-[:HAS_TABLE]->(Table),
            # (Table)-[:HAS_COLUMN]->(Column), and
            # (Column)-[:REFERENCES]->(Column). Cypher would then be useful for
            # metadata questions like dependency paths, FK impact, PII-looking
            # columns, and view/table reachability while SQL remains the row-data
            # query language.
            schema_chunk = Chunk(
                path=f"database://{self.db_type}/schema",
                text=self._prepend_duckdb_read_warning(combined_text)
            )
            chunks = [schema_chunk]
        try:
            # Fix query syntax if SQLFluff is available
            if is_sqlfluff_available():
                dialect = get_sql_dialect(self.db_type)
                original_query = query
                query = lint_and_fix_sql(query, dialect)
                if self.verbose and original_query != query:
                    print(f"[thepipe] SQLFluff fixed query from:\n{original_query}\nto:\n{query}")

            # Execute the query
            if self._is_odbc():
                result = self._execute_odbc_query(query, params=params)
            else:
                result = self.db.query(query, params=params)

            # Format the result
            result_text = f"## SQL Query\n\n```sql\n{query}\n```\n\n"

            if isinstance(result, pd.DataFrame):
                result_text += f"## Results ({len(result)} rows)\n\n"

                if not result.empty:
                    # Convert to JSON for consistent formatting
                    result_text += "```json\n"
                    result_text += result.to_json(orient='records', indent=2)
                    result_text += "\n```"
                else:
                    result_text += "*No rows returned*"
            else:
                # Non-DataFrame result (e.g., for non-SELECT queries)
                result_text += f"## Results\n\n"
                result_text += "Query executed successfully."

            chunks.append(Chunk(
                path=f"database://{self.db_type}/query",
                text=self._prepend_duckdb_read_warning(result_text)
            ))
            schema_chunk.text = self._prepend_duckdb_read_warning(schema_chunk.text or "")

            return chunks

        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error executing query: {str(e)}")

            chunks.append(Chunk(
                path=f"database://{self.db_type}/error",
                text=f"Error executing query: {str(e)}"
            ))

            return chunks

    def execute_iterative_analysis(self, natural_language_query: str, 
                                tables: List[str], schema_text: str,
                                llm_config: Dict[str, Any],
                                max_iterations: int = 3, 
                                verbose: bool = False) -> List[Chunk]:
        """
        Execute an iterative, LLM-guided analysis of a database.
        
        This uses a multi-stage approach:
        1. Strategy: LLM plans a series of queries to answer the question
        2. Execute: Run queries one by one, collecting results
        3. Refine: Send results back to LLM for analysis and next steps
        4. Conclude: Generate final insights from all collected data
        
        Args:
            natural_language_query: The natural language question
            tables: List of available tables
            schema_text: Database schema information
            llm_config: LLM configuration
            max_iterations: Maximum number of query iterations
            verbose: Enable verbose logging
            
        Returns:
            List of Chunk objects with query results and insights
        """
        # Setup - use unified LLMClient
        from .llm import LLMClient
        
        # Convert llm_config to options format
        options = {
            'llm_provider': llm_config.get('llm_provider', 'openai'),
            'api_key': llm_config.get('api_key'),
            'api_base': llm_config.get('api_base'),
            'model': llm_config.get('model', 'gpt-3.5-turbo'),
        }
        model = options['model']
        
        llm_client = LLMClient.from_options(options=options)
        
        chunks = []
        schema_chunk = Chunk(path=f"database://{self.db_type}/schema", text=schema_text)
        chunks.append(schema_chunk)
        
        executed_queries = []
        remaining_iterations = max_iterations
        
        # STAGE 1: Generate strategy
        if verbose:
            print(f"[thepipe] Stage 1: Generating query strategy")
        
        strategy_prompt = f"""
        You are a database analyst. Create a strategy to answer this question using SQL queries.
        
        QUESTION: {natural_language_query}
        
        DATABASE SCHEMA:
        {schema_text}
        
        Propose a series of 1-3 SQL queries that will help answer this question.
        For each query, explain what insights it will provide.
        Keep your response brief and focused.
        
        Format:
        STRATEGY: Brief 1-2 sentence overall approach
        
        QUERY 1:
        ```sql
        -- Your first SQL query
        ```
        PURPOSE: What you'll learn from this query
        
        [Additional queries as needed]
        """
        
        strategy_response = llm_client.query(
            messages=[
                {"role": "system", "content": "You are a database expert planning an analysis strategy."},
                {"role": "user", "content": strategy_prompt}
            ],
            temperature=0.2
        )
        
        strategy_text = strategy_response.content
        
        # Extract queries from strategy
        query_pattern = r"QUERY \d+:\s*```(?:sql)?\s*([\s\S]*?)```\s*PURPOSE:\s*([\s\S]*?)(?=QUERY \d+:|$)"
        planned_queries = []
        
        for match in re.finditer(query_pattern, strategy_text):
            sql_query = match.group(1).strip()
            purpose = match.group(2).strip()
            planned_queries.append({"query": sql_query, "purpose": purpose})
        
        # Save strategy as a chunk
        strategy_chunk = Chunk(
            path=f"database://{self.db_type}/strategy",
            text=f"## Query Strategy\n\n{strategy_text}"
        )
        chunks.append(strategy_chunk)
        
        # STAGE 2-3: Iterative execution and refinement
        current_results = []
        
        while planned_queries and remaining_iterations > 0:
            # Get next query
            query_info = planned_queries.pop(0)
            sql_query = query_info["query"]
            purpose = query_info["purpose"]
            
            if verbose:
                print(f"[thepipe] Executing query: {sql_query}")
                print(f"[thepipe] Purpose: {purpose}")
            
            # Fix and execute query
            if is_sqlfluff_available():
                dialect = get_sql_dialect(self.db_type)
                sql_query = lint_and_fix_sql(sql_query, dialect)
            
            sql_query = fix_sql_syntax(sql_query)
            
            try:
                result = self.db.query(sql_query)
                query_info["result"] = result
                query_info["success"] = True
                executed_queries.append(query_info)
                
                # Convert result to text format for LLM
                if isinstance(result, pd.DataFrame):
                    if not result.empty:
                        result_text = f"RESULTS ({len(result)} rows):\n{result.head(10).to_string()}"
                    else:
                        result_text = "RESULTS: No rows returned"
                else:
                    result_text = "RESULTS: Query executed successfully"
                
                current_results.append(result_text)
                
                # Refine strategy if we have more iterations
                if planned_queries or remaining_iterations > 1:
                    refine_prompt = f"""
                    You are analyzing data to answer this question: {natural_language_query}
                    
                    So far, you've executed these queries:
                    
                    {strategy_text}
                    
                    LATEST RESULTS:
                    {result_text}
                    
                    Based on these results:
                    1. Do you need additional queries to answer the question?
                    2. If yes, provide ONE refined SQL query.
                    3. If no, just say "COMPLETE"
                    
                    Format if more queries needed:
                    ANALYSIS: Brief analysis of current results
                    
                    NEXT QUERY:
                    ```sql
                    -- Your refined SQL query
                    ```
                    PURPOSE: What this query will help determine
                    """
                    
                    refine_response = llm_client.query(
                        messages=[
                            {"role": "system", "content": "You are a database expert refining an analysis."},
                            {"role": "user", "content": refine_prompt}
                        ],
                        temperature=0.2
                    )
                    
                    refine_text = refine_response.content
                    
                    if "COMPLETE" not in refine_text.upper():
                        # Extract next query
                        next_query_match = re.search(r"NEXT QUERY:\s*```(?:sql)?\s*([\s\S]*?)```\s*PURPOSE:\s*([\s\S]*?)(?=NEXT QUERY:|$)", refine_text)
                        if next_query_match:
                            new_sql = next_query_match.group(1).strip()
                            new_purpose = next_query_match.group(2).strip()
                            planned_queries.append({"query": new_sql, "purpose": new_purpose})
                        
                        if verbose:
                            print(f"[thepipe] Added refined query to plan")
            
            except Exception as e:
                query_info["error"] = str(e)
                query_info["success"] = False
                executed_queries.append(query_info)
                
                if verbose:
                    print(f"[thepipe] Error executing query: {str(e)}")
            
            remaining_iterations -= 1
        
        # STAGE 4: Generate final insights
        if verbose:
            print(f"[thepipe] Generating final insights")
        
        # Prepare query results summary
        all_query_results = ""
        for i, query_info in enumerate(executed_queries):
            all_query_results += f"\nQUERY {i+1}: {query_info['query']}\n"
            all_query_results += f"PURPOSE: {query_info.get('purpose', 'N/A')}\n"
            
            if query_info.get('success', False) and isinstance(query_info.get('result'), pd.DataFrame):
                df = query_info['result']
                if not df.empty:
                    all_query_results += f"RESULTS:\n{df.head(15).to_string()}\n"
                else:
                    all_query_results += "RESULTS: No rows returned\n"
            else:
                all_query_results += f"ERROR: {query_info.get('error', 'Unknown error')}\n"
        
        # Final insights prompt
        insight_prompt = f"""
        Based on all query results, provide key insights that answer this question:
        
        QUESTION: {natural_language_query}
        
        QUERY RESULTS:
        {all_query_results}
        
        Provide a concise report with:
        1. A direct answer to the question (1-2 sentences)
        2. 3-5 key insights with specific data points
        3. A brief conclusion
        """
        
        insight_response = llm_client.query(
            messages=[
                {"role": "system", "content": "You are a data analyst creating a clear insights report."},
                {"role": "user", "content": insight_prompt}
            ],
            temperature=0.1
        )
        
        # Create final report
        final_report = f"# Data Insight Report\n\n"
        final_report += f"## Question\n\n{natural_language_query}\n\n"
        final_report += f"{insight_response.content}\n\n"
        
        # Add supporting queries
        final_report += f"## Supporting Data\n\n"
        for i, query_info in enumerate(executed_queries):
            if query_info.get('success', False) and isinstance(query_info.get('result'), pd.DataFrame):
                df = query_info['result']
                if not df.empty:
                    final_report += f"### Query {i+1}\n\n"
                    final_report += f"```sql\n{query_info['query']}\n```\n\n"
                    final_report += "```json\n"
                    final_report += df.head(15).to_json(orient='records', indent=2)
                    final_report += "\n```\n\n"
        
        chunks.append(Chunk(path=f"database://{self.db_type}/insights", text=final_report))
        return chunks

    def process_nl_query(self, natural_language_query: str, llm_config: Optional[Dict[str, Any]] = None, 
                        debug_mode: bool = False, iterative: bool = True,
                        max_iterations: int = 3) -> List[Chunk]:
        """
        Process natural language query using an LLM to convert to SQL.
        
        Args:
            natural_language_query: Natural language question to convert to SQL
            llm_config: Configuration for the LLM
            debug_mode: If True, return the generated SQL without executing it
            iterative: If True, use the iterative analysis approach
            max_iterations: Maximum number of query iterations (for iterative mode)
            
        Returns:
            List of Chunk objects with query results
        """
        if self._is_odbc():
            return [Chunk(
                path="database://odbc/error",
                text="Natural language database analysis is not supported for generic ODBC sources yet. Provide explicit SQL."
            )]

        # Get tables and schema information
        tables = get_all_tables(self.db, self.db_type, self.verbose)
        view_name = tables[0] if tables else None
        
        if not view_name:
            chunks = [Chunk(
                path=f"database://{self.db_type}/error",
                text="Could not determine database table name. Please provide a table name explicitly."
            )]
            return chunks
        
        # Get schema for all tables
        schema_text = get_schema_for_all_tables(self.db, tables, self.verbose)
        
        # Always run auto-analysis with detailed output
        analysis_text = ""
        if view_name:
            try:
                # Pass through any options for analysis
                analysis_options = self.options.get("analysis", {}) if hasattr(self, "options") else {}
                
                auto_analysis = get_auto_analysis(
                    self.db, 
                    self.db_type, 
                    view_name, 
                    verbose=self.verbose,
                    options=analysis_options
                )
                
                # Format detailed analysis with full column statistics
                analysis_text = "## Automatic Database Analysis\n\n"
                
                # Add basic dataset info
                if 'total_rows' in auto_analysis:
                    analysis_text += f"Total rows: {auto_analysis['total_rows']:,}\n"
                    
                if 'columns' in auto_analysis:
                    analysis_text += f"Total columns: {len(auto_analysis['columns'])}\n\n"
                    analysis_text += f"Columns: {', '.join(auto_analysis['columns'])}\n\n"
                
                # Add column type categorization
                if 'column_types' in auto_analysis:
                    cat_cols = auto_analysis['column_types'].get('categorical', [])
                    num_cols = auto_analysis['column_types'].get('numeric', [])
                    
                    if cat_cols:
                        analysis_text += f"Categorical columns: {', '.join(cat_cols)}\n\n"
                        
                    if num_cols:
                        analysis_text += f"Numeric columns: {', '.join(num_cols)}\n\n"
                
                # Add detailed column statistics
                if 'column_stats' in auto_analysis:
                    analysis_text += "### Column Statistics\n\n"
                    
                    for col, stats in auto_analysis['column_stats'].items():
                        analysis_text += f"#### {col}\n"
                        
                        if stats['type'] == 'categorical':
                            analysis_text += f"Type: Categorical\n"
                            if 'distinct_count' in stats:
                                analysis_text += f"Distinct values: {stats['distinct_count']}\n"
                            
                            if 'top_values' in stats:
                                analysis_text += "Top values:\n"
                                for val in stats['top_values']:
                                    analysis_text += f"- {val['value']}: {val['count']} ({val['percentage']:.2f}%)\n"
                        
                        elif stats['type'] == 'numeric':
                            analysis_text += f"Type: Numeric\n"
                            if 'stats' in stats:
                                stat_data = stats['stats']
                                analysis_text += f"Range: {stat_data.get('min', 'N/A')} to {stat_data.get('max', 'N/A')}\n"
                                analysis_text += f"Mean: {stat_data.get('mean', 'N/A')}\n"
                                analysis_text += f"Null count: {stat_data.get('null_count', 'N/A')}\n"
                        
                        analysis_text += "\n"
                
                # Add key columns info
                if 'potential_keys' in auto_analysis and auto_analysis['potential_keys']:
                    analysis_text += f"Potential key columns: {', '.join(auto_analysis['potential_keys'])}\n\n"
                    
                if 'date_columns' in auto_analysis and auto_analysis['date_columns']:
                    analysis_text += f"Date columns: {', '.join(auto_analysis['date_columns'])}\n\n"
                    
            except Exception as e:
                if self.verbose:
                    print(f"[thepipe] Error running auto analysis: {str(e)}")
        
        # Create combined schema and analysis chunk
        combined_text = schema_text
        if analysis_text:
            combined_text += f"\n\n{analysis_text}"
        
        schema_chunk = Chunk(
            path=f"database://{self.db_type}/schema",
            text=combined_text
        )
        chunks = [schema_chunk]
        
        if self.verbose:
            print(f"[thepipe] Processing natural language query: '{natural_language_query}'")
            print(f"[thepipe] Found {len(tables)} tables")
        
        # Check if LLM configuration is provided
        if not llm_config:
            chunks.append(Chunk(
                path=f"database://{self.db_type}/error",
                text="Natural language queries require LLM configuration."
            ))
            return chunks
        
        # Check if using iterative mode
        if iterative:
            # Pass the combined schema and analysis text to the iterative analysis
            return self.execute_iterative_analysis(
                natural_language_query=natural_language_query,
                tables=tables,
                schema_text=combined_text,  # Using combined text with analysis
                llm_config=llm_config,
                max_iterations=max_iterations,
                verbose=self.verbose
            )
        
        # If not using iterative mode, proceed with the existing approach
        try:
            from .llm import LLMClient
            
            # Set up LLMClient from options
            options = {
                'llm_provider': llm_config.get('llm_provider', 'openai'),
                'api_key': llm_config.get('api_key'),
                'api_base': llm_config.get('api_base'),
                'model': llm_config.get('model', 'gpt-3.5-turbo'),
            }
            model = options['model']
            
            # Check for API key if not using agent mode
            if options['llm_provider'] != 'agent' and not options.get('api_key') and not os.environ.get('OPENAI_API_KEY'):
                chunks.append(Chunk(
                    path=f"database://{self.db_type}/error",
                    text="API key is required for natural language queries."
                ))
                return chunks
            
            llm_client = LLMClient.from_options(options=options)
            
            # Create prompt that includes both schema and analysis
            prompt = f"""
            Convert this natural language question into a SQL query.

            QUESTION: {natural_language_query}
            
            DATABASE INFORMATION:
            {combined_text}
            
            IMPORTANT:
            - Consider which tables are relevant to this question
            - Use appropriate JOINs if multiple tables are needed
            - Use the database analysis to guide your query construction
            - Return ONLY the SQL query without explanations
            """
            
            # Get SQL query from LLM
            response = llm_client.query(
                messages=[
                    {"role": "system", "content": "You are a database expert. Convert questions to SQL."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0
            )
            
            sql_query = response.content.strip()
            
            # Clean up the SQL query
            if sql_query.startswith("```sql"):
                sql_query = sql_query.split("```sql")[1]
            elif sql_query.startswith("```"):
                sql_query = sql_query.split("```")[1]
                
            if sql_query.endswith("```"):
                sql_query = sql_query.split("```")[0]
            
            sql_query = sql_query.strip()
            
            # Fix SQL using SQLFluff
            if is_sqlfluff_available():
                dialect = get_sql_dialect(self.db_type)
                original_query = sql_query
                sql_query = lint_and_fix_sql(sql_query, dialect)
                if self.verbose and original_query != sql_query:
                    print(f"[thepipe] SQLFluff fixed query from:\n{original_query}\nto:\n{sql_query}")
            
            # Also apply our basic syntax fixes
            sql_query = fix_sql_syntax(sql_query)
            
            # Debug mode - return SQL without executing
            if debug_mode:
                result_text = f"## Natural Language Query\n\n{natural_language_query}\n\n"
                result_text += f"## Generated SQL\n\n```sql\n{sql_query}\n```\n\n"
                result_text += "*Debug mode: SQL not executed*"
                
                chunks.append(Chunk(
                    path=f"database://{self.db_type}/debug",
                    text=result_text
                ))
                return chunks
                
            # Check if we got a valid query
            if not sql_query or not sql_query.lower().startswith("select"):
                return execute_fallback(
                    query=natural_language_query,
                    db_instance=self.db,
                    view_name=view_name,
                    verbose=self.verbose
                )
            
            # Execute the generated SQL query
            try:
                result = self.db.query(sql_query)
                
                # Format the result
                result_text = f"## Natural Language Query\n\n{natural_language_query}\n\n"
                result_text += f"## Generated SQL\n\n```sql\n{sql_query}\n```\n\n"
                
                if isinstance(result, pd.DataFrame):
                    result_text += f"## Results ({len(result)} rows)\n\n"
                    
                    if not result.empty:
                        result_text += "```json\n"
                        result_text += result.to_json(orient='records', indent=2)
                        result_text += "\n```"
                    else:
                        result_text += "*No rows returned*"
                else:
                    result_text += "Query executed successfully."
                
                chunks.append(Chunk(
                    path=f"database://{self.db_type}/nl_query",
                    text=result_text
                ))
                
            except Exception as e:
                return execute_fallback(
                    query=natural_language_query,
                    view_name=view_name,
                    error=str(e),
                    failed_query=sql_query,
                    db_instance=self.db,
                    verbose=self.verbose
                )
            
            return chunks
                
        except Exception as e:
            chunks.append(Chunk(
                path=f"database://{self.db_type}/error",
                text=f"Error processing natural language query: {str(e)}"
            ))
            
            return chunks
                
    def close(self):
        """Close the database connection and clean up resources."""
        if hasattr(self, '_temp_path') and os.path.exists(self._temp_path):
            try:
                os.unlink(self._temp_path)
            except OSError as e:
                logger.debug(f"Failed to delete temp file {self._temp_path}: {e}")
        
        # Close the database connection
        if self.db:
            try:
                if hasattr(self.db, 'close'):
                    self.db.close()
            except Exception as e:
                logger.debug(f"Failed to close database connection: {e}")

def parse_database_args(args) -> None:
    """
    Process database-related command line arguments.
    
    Args:
        args: Namespace object from argparse with command line arguments
    
    Returns:
        None - modifies args in place
    """
    if not hasattr(args, 'db') or args.db is None:
        return
        
    if len(args.db) == 0:
        # No arguments provided with --db, default to preview mode
        args.db_query = None
        args.db_type = None
        args.db_mode = "preview"
    else:
        args.db_query = args.db[0] if len(args.db) >= 1 else None
        args.db_type = args.db[1] if len(args.db) >= 2 else None
        args.db_mode = args.db[2] if len(args.db) >= 3 else None
        
    # Add mode to options
    if not hasattr(args, 'options') or args.options is None:
        args.options = {}
    elif isinstance(args.options, str):
        try:
            args.options = json.loads(args.options)
        except json.JSONDecodeError:
            print("Error: Invalid JSON in options")
            import sys
            sys.exit(1)
            
    # Set the appropriate option based on the mode
    if hasattr(args, 'db_mode') and args.db_mode:
        if args.db_mode.lower() == "schema":
            args.options["schema_only"] = True
        elif args.db_mode.lower() == "preview":
            args.options["preview"] = True

def process_database(
    connection_info: Union[str, Dict],
    query: Optional[str] = None,
    db_type: Optional[str] = None,
    mode: Optional[str] = None,
    verbose: bool = False,
    options: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    """
    Process a database connection and return extracted data.
    
    This function is the main entry point for database operations in thepipe.
    It handles connecting to databases, executing queries, and formatting results.
    
    Args:
        connection_info: Connection string or config dictionary
        query: Natural language or SQL query to execute
        db_type: Database type (auto-detected if not specified)
        mode: Processing mode ("schema", "preview", or None for query)
        verbose: If True, print detailed logs
        options: Additional options for customizing behavior
        
    Returns:
        List of Chunk objects with data
    """
    options = options or {}
    
    if verbose:
        print(f"[thepipe] Starting database processing")
        print(f"[thepipe] Connection info: {connection_info if isinstance(connection_info, str) else 'dict'}")
        print(f"[thepipe] Query: {query}")
        print(f"[thepipe] Database type: {db_type}")
        print(f"[thepipe] Mode: {mode}")
        print(f"[thepipe] Options: {options}")
    
    try:
        # Initialize database manager with options
        if verbose:
            print(f"[thepipe] Initializing database manager")
            
        db_manager = DatabaseManager(
            connection_info=connection_info,
            db_type=db_type,
            verbose=verbose,
            options=options  # Pass full options here
        )
        
        # Get schema information
        if verbose:
            print(f"[thepipe] Getting schema information")
            
        schema_chunk = db_manager.get_schema()
        
        # Handle schema or preview mode
        if mode == "schema":
            if verbose:
                print(f"[thepipe] Schema mode detected, returning schema only")
                
            db_manager.close()
            return [schema_chunk]
        elif mode == "preview":
            if verbose:
                print(f"[thepipe] Preview mode detected, generating data preview")
                
            preview_chunk = db_manager.get_preview(
                max_rows=options.get("max_rows", DEFAULT_PREVIEW_ROWS)
            )
            schema_chunk.text = db_manager._prepend_duckdb_read_warning(schema_chunk.text or "")
            db_manager.close()
            return [schema_chunk, preview_chunk]
        
        # Handle empty query (should have been caught by preview mode above)
        if not query:
            if verbose:
                print(f"[thepipe] No query provided, returning schema only")
                
            db_manager.close()
            return [schema_chunk]
        
        # Determine if query is SQL or natural language
        is_sql_query_result = is_sql(query)
        
        if verbose:
            print(f"[thepipe] Query type: {'SQL' if is_sql_query_result else 'Natural Language'}")
        
        if not is_sql_query_result and db_manager.db_type == "odbc":
            result_chunks = [schema_chunk, Chunk(
                path="database://odbc/error",
                text="Natural language database analysis is not supported for generic ODBC sources yet. Provide explicit SQL."
            )]
            db_manager.close()
            return result_chunks

        if is_sql_query_result:
            # Direct SQL execution
            if verbose:
                print(f"[thepipe] Executing SQL query: {query}")
                
            result_chunks = db_manager.execute_query(
                query=query,
                params=options.get("params")
            )
        else:
            # Natural language query processing
            if verbose:
                print(f"[thepipe] Processing natural language query: {query}")
                print(f"[thepipe] LLM config: {options.get('llm_extractor', {})}")
                
            result_chunks = db_manager.process_nl_query(
                natural_language_query=query,
                llm_config=options.get("llm_extractor", {})
            )
        
        if verbose:
            print(f"[thepipe] Query execution complete")
            print(f"[thepipe] Closing database connection")
            
        db_manager.close()
        
        if verbose:
            print(f"[thepipe] Database connection closed")
            print(f"[thepipe] Returning {len(result_chunks)} chunks")
            
        return result_chunks
    
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error processing database: {str(e)}")
            import traceback
            traceback.print_exc()
        return [Chunk(
            path=f"database://{db_type if db_type else 'unknown'}/error",
            text=f"Error processing database: {str(e)}"
        )]
        
def is_sql(query: str) -> bool:
    """Determine if a query is SQL or natural language."""
    if not query or not isinstance(query, str):
        return False
        
    query_lower = query.lower().strip()
    
    # Common SQL command starters
    sql_keywords = [
        "select ", "show ", "describe ", "insert ", "update ", 
        "delete ", "create ", "alter ", "drop ", "truncate ", 
        "grant ", "revoke ", "commit ", "rollback ", "use "
    ]
    
    # Check if query starts with any SQL keyword
    return any(query_lower.startswith(keyword) for keyword in sql_keywords)

def is_sqlfluff_available() -> bool:
    """Check if SQLFluff is available."""
    try:
        import sqlfluff
        return True
    except ImportError:
        return False

def get_sql_dialect(db_type: str) -> str:
    """Get SQLFluff dialect for a database type."""
    dialect_mapping = {
        "postgres": "postgres",
        "postgresql": "postgres",
        "mysql": "mysql",
        "sqlite": "sqlite",
        "odbc": "ansi",
        "duckdb": "duckdb",
        "parquet": "duckdb",
        "csv": "duckdb",
        "excel": "duckdb",
        "json": "duckdb",
        "jsonl": "duckdb",
        "orc": "duckdb",
        "feather": "duckdb",
    }
    return dialect_mapping.get(db_type.lower(), "ansi")

def is_templated_sql(sql_query: str) -> bool:
    """Check if SQL query contains template syntax."""
    template_patterns = [
        r'{{.*?}}',  # Jinja/dbt style
        r'\$\{.*?\}',  # String interpolation style
        r':\w+',  # Named parameter style
        r'\$\d+'   # Positional parameter style
    ]
    
    return any(re.search(pattern, sql_query) for pattern in template_patterns)

def lint_and_fix_sql(sql_query: str, dialect: str = "duckdb") -> str:
    """Lint and fix SQL query using SQLFluff."""
    if not is_sqlfluff_available():
        return sql_query
        
    try:
        import sqlfluff
        
        # Check for templating
        templated = is_templated_sql(sql_query)
        
        # Remove trailing semicolons
        sql_query = sql_query.strip()
        if sql_query.endswith(';'):
            sql_query = sql_query[:-1]
            
        # Configure SQLFluff
        config = {
            "dialect": dialect,
            "templater": "jinja" if templated else "raw"
        }
        
        # Fix the query
        fixed_query = sqlfluff.fix(
            sql_query,
            config=config,
            only_fix_lint_errors=True
        )
        
        return fixed_query if fixed_query else sql_query
    except Exception:
        return sql_query
