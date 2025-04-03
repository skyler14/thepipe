"""
Enhanced database utilities module that integrates with JupySQL middleware.
This module provides a clean interface for database operations in thepipe.
"""

from typing import Dict, List, Optional, Any, Union, Tuple
import os
import pandas as pd
import json
import re
from pathlib import Path
import time

from .core import Chunk

# Import the JupySQL middleware
from .jupysql_middleware import Database
from .database_analysis import create_nl_query_prompt, detect_relationships, execute_fallback, format_analysis_for_llm, get_all_tables, get_multi_table_examples, get_schema_for_all_tables, get_sql_examples_for_intent, get_table_name,fix_sql_syntax,get_auto_analysis
# Constants
DEFAULT_MAX_ROWS = 15
DEFAULT_PREVIEW_ROWS = 5


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
        self.options = options or {}
        self.db = None
        self._connect()
        
    def _detect_database_type(self, source: Union[str, Dict]) -> str:
        """Detect database type from connection string or configuration."""
        if isinstance(source, str):
            if source.startswith("postgresql://") or source.startswith("postgres://"):
                return "postgres"
            elif source.startswith("mysql://"):
                return "mysql"
            elif source.startswith("sqlite://"):
                return "sqlite"
            elif source.startswith("mssql://"):
                return "mssql"
            elif source.endswith((".parquet", ".parq")) or "/parquet/" in source or "*.parquet" in source:
                return "parquet"
            elif source.endswith(".csv"):
                return "csv"
            elif source.endswith((".xlsx", ".xls")):
                return "excel"
            elif os.path.isdir(source):
                # Check if directory contains parquet files
                for file in os.listdir(source):
                    if file.endswith('.parquet') or file.endswith('.parq'):
                        return "parquet"
        elif isinstance(source, dict) and "type" in source:
            return source["type"]
            
        return "unknown"
    
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
            
            if self.verbose:
                print(f"[thepipe] Connecting to {self.db_type} database")
                print(f"[thepipe] Connection info: {self.connection_info if isinstance(self.connection_info, str) else 'dict'}")
            
            # Special handling for parquet files and directories
            if self.db_type == "parquet":
                # Use DuckDB for parquet files
                connection_str = f"duckdb://"
                
                if self.verbose:
                    print(f"[thepipe] Using DuckDB for parquet data: {connection_str}")
                    
                self.db = Database(connection_str, config_dict=config_dict)
                
                # We'll create the view in get_schema() later
                # This ensures we don't try to create the view before running a query
                
                if self.verbose:
                    print(f"[thepipe] DuckDB connection established for parquet data")
                    
            elif self.db_type == "csv":
                # Handle CSV files with DuckDB
                connection_str = f"duckdb://"
                self.db = Database(connection_str, config_dict=config_dict)
                
                if self.verbose:
                    print(f"[thepipe] Creating view for CSV file: {self.connection_info}")
                    
                try:
                    self.db.execute(f"CREATE VIEW csv_data AS SELECT * FROM '{self.connection_info}'")
                except Exception as e:
                    if self.verbose:
                        print(f"[thepipe] Error creating CSV view: {str(e)}")
            elif self.db_type == "excel":
                # For Excel, we'll use pandas to load the data first, then DuckDB
                import pandas as pd
                
                if self.verbose:
                    print(f"[thepipe] Reading Excel file: {self.connection_info}")
                    
                df = pd.read_excel(self.connection_info)
                
                # Save to temporary CSV
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as temp_csv:
                    temp_path = temp_csv.name
                    df.to_csv(temp_path, index=False)
                
                # Use DuckDB with the CSV
                connection_str = f"duckdb://"
                self.db = Database(connection_str, config_dict=config_dict)
                
                if self.verbose:
                    print(f"[thepipe] Creating view for Excel data from temp file: {temp_path}")
                    
                self.db.execute(f"CREATE VIEW excel_data AS SELECT * FROM '{temp_path}'")
                
                # Store path for cleanup
                self._temp_path = temp_path
            else:
                # Standard database connection
                if isinstance(self.connection_info, str):
                    connection_str = self.connection_info
                else:
                    # Create connection string from dictionary
                    connection_str = self._create_connection_string(
                        self.connection_info, self.db_type)
                
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
                
            if self.db_type in ["parquet", "csv", "excel"]:
                # Get schema from the first few rows
                view_name = {
                    "parquet": "parquet_data",
                    "csv": "csv_data", 
                    "excel": "excel_data"
                }.get(self.db_type)
                
                if self.verbose:
                    print(f"[thepipe] Using view name: {view_name}")
                
                try:
                    # First try to check if the view exists
                    if self.db_type == "parquet":
                        check_query = f"SELECT name FROM sqlite_master WHERE type='view' AND name='{view_name}'"
                        check_df = self.db.query(check_query)
                        
                        if self.verbose:
                            print(f"[thepipe] View check result: {len(check_df)} rows")
                            
                        if len(check_df) == 0:
                            if self.verbose:
                                print(f"[thepipe] View {view_name} doesn't exist, creating it")
                                
                            # The view doesn't exist, try to create it
                            if isinstance(self.connection_info, str):
                                if os.path.isdir(self.connection_info):
                                    # Directory of parquet files
                                    path_pattern = os.path.join(self.connection_info, "*.parquet")
                                    try:
                                        self.db.execute(f"CREATE VIEW {view_name} AS SELECT * FROM '{path_pattern}'")
                                        if self.verbose:
                                            print(f"[thepipe] Created view for parquet files: {path_pattern}")
                                    except Exception as e:
                                        if self.verbose:
                                            print(f"[thepipe] Error creating view: {str(e)}")
                                else:
                                    # Single parquet file or pattern
                                    try:
                                        self.db.execute(f"CREATE VIEW {view_name} AS SELECT * FROM '{self.connection_info}'")
                                        if self.verbose:
                                            print(f"[thepipe] Created view for parquet file: {self.connection_info}")
                                    except Exception as e:
                                        if self.verbose:
                                            print(f"[thepipe] Error creating view: {str(e)}")
                    
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
                    except:
                        return Chunk(
                            path=f"database://{self.db_type}/schema",
                            texts=["Could not retrieve schema information"]
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
                            except:
                                # Fallback to basic DESCRIBE
                                try:
                                    describe_df = self.db.query(f"DESCRIBE {table}")
                                    schema_info += describe_df.to_markdown()
                                except:
                                    schema_info += f"*Schema information not available for this table*\n\n"
                    except Exception as e:
                        schema_info += f"*Error retrieving schema: {str(e)}*\n\n"
                    
                    schema_info += "\n\n"
            
            if self.verbose:
                print("[thepipe] Schema extraction complete")
                
            return Chunk(
                path=f"database://{self.db_type}/schema",
                texts=[schema_info]
            )
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error getting schema: {str(e)}")
                import traceback
                traceback.print_exc()
            return Chunk(
                path=f"database://{self.db_type}/schema",
                texts=[f"Error retrieving schema: {str(e)}"]
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
            
            if self.db_type in ["parquet", "csv", "excel"]:
                view_name = {
                    "parquet": "parquet_data",
                    "csv": "csv_data", 
                    "excel": "excel_data"
                }.get(self.db_type)
                
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
                        except:
                            return Chunk(
                                path=f"database://{self.db_type}/preview",
                                texts=["Could not retrieve table information"]
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
                texts=[preview_text]
            )
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error generating data preview: {str(e)}")
            return Chunk(
                path=f"database://{self.db_type}/preview",
                texts=[f"Error generating data preview: {str(e)}"]
            )
    
    def execute_query(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Chunk]:
        """
        Execute SQL query and return results as chunks.
        
        Args:
            query: SQL query to execute
            params: Optional query parameters
            
        Returns:
            List of Chunk objects with query results
        """
        schema_chunk = self.get_schema()
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
                texts=[result_text]
            ))
            
            return chunks
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error executing query: {str(e)}")
            
            chunks.append(Chunk(
                path=f"database://{self.db_type}/error",
                texts=[f"Error executing query: {str(e)}"]
            ))
            
            return chunks

    def process_nl_query(self, natural_language_query: str, llm_config: Optional[Dict[str, Any]] = None) -> List[Chunk]:
        """
        Process natural language query using an LLM to convert to SQL.
        
        Args:
            natural_language_query: Natural language question to convert to SQL
            llm_config: Configuration for the LLM
            
        Returns:
            List of Chunk objects with query results
        """
        # FIXED: Get all tables once and reuse throughout the function
        tables = get_all_tables(self.db, self.db_type, self.verbose)
        
        # Get the view_name from tables list instead of calling get_table_name
        view_name = tables[0] if tables else None
        if not view_name:
            chunks = [Chunk(
                path=f"database://{self.db_type}/error",
                texts=["Could not determine database table name. Please provide a table name explicitly."]
            )]
            return chunks
            
        # Get schema information for all tables - reuse tables list
        schema_text = get_schema_for_all_tables(self.db, tables, self.verbose)
        schema_chunk = Chunk(
            path=f"database://{self.db_type}/schema",
            texts=[schema_text]
        )
        chunks = [schema_chunk]
        
        if self.verbose:
            print(f"[thepipe] Processing natural language query: '{natural_language_query}'")
            print(f"[thepipe] Found {len(tables)} tables")
        
        # Check if LLM configuration is provided
        if not llm_config:
            chunks.append(Chunk(
                path=f"database://{self.db_type}/error",
                texts=["Natural language queries require LLM configuration."]
            ))
            return chunks
        
        try:
            import os
            from openai import OpenAI
            
            # FIXED: Pass the view_name directly to get_auto_analysis
            analysis = get_auto_analysis(
                db_instance=self.db,
                db_type=self.db_type,
                view_name=view_name,  # Use already retrieved view_name
                verbose=self.verbose
            )
            
            # Set up OpenAI client
            api_key = llm_config.get("api_key", os.environ.get("OPENAI_API_KEY"))
            api_base = llm_config.get("api_base", os.environ.get("OPENAI_API_BASE"))
            model = llm_config.get("model", "gpt-3.5-turbo")
            
            if not api_key:
                chunks.append(Chunk(
                    path=f"database://{self.db_type}/error",
                    texts=["API key is required for natural language queries."]
                ))
                return chunks
            
            # Create OpenAI client
            client_args = {"api_key": api_key}
            if api_base:
                client_args["base_url"] = api_base
                
            client = OpenAI(**client_args)
            
            # Determine query intent
            query_intent = "general"
            query_lower = natural_language_query.lower()
            
            if any(word in query_lower for word in ["word", "text", "phrase", "mention"]):
                query_intent = "text_analysis"
            elif any(word in query_lower for word in ["average", "sum", "count", "max", "min"]):
                query_intent = "numeric_analysis"
            elif any(word in query_lower for word in ["trend", "time", "date", "year", "month"]):
                query_intent = "time_analysis"
            elif any(word in query_lower for word in ["group", "category", "type", "distribution"]):
                query_intent = "categorization"
            
            # Get analysis and examples
            analysis_text = format_analysis_for_llm(analysis)
            sql_examples = get_sql_examples_for_intent(query_intent, view_name)
            
            # Detect relationships and get multi-table examples if needed
            relationships = detect_relationships(self.db, tables, self.verbose)
            multi_table_examples = get_multi_table_examples(tables, relationships) if len(tables) > 1 else ""
            
            # Create enhanced prompt
            prompt = create_nl_query_prompt(
                natural_language_query=natural_language_query,
                schema_text=schema_text,
                analysis_text=analysis_text,
                sql_examples=sql_examples,
                multi_table_examples=multi_table_examples
            )
            
            # Get SQL query from LLM
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a database expert that converts questions to SQL."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0
            )
            
            sql_query = response.choices[0].message.content.strip()
            
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
                    texts=[result_text]
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
                texts=[f"Error processing natural language query: {str(e)}"]
            ))
            
            return chunks
        
    def close(self):
        """Close the database connection and clean up resources."""
        if hasattr(self, '_temp_path') and os.path.exists(self._temp_path):
            try:
                os.unlink(self._temp_path)
            except:
                pass
        
        # Close the database connection
        if self.db:
            try:
                if hasattr(self.db, 'close'):
                    self.db.close()
            except:
                pass

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
        # Initialize database manager
        if verbose:
            print(f"[thepipe] Initializing database manager")
            
        db_manager = DatabaseManager(
            connection_info=connection_info,
            db_type=db_type,
            verbose=verbose,
            options=options
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
            texts=[f"Error processing database: {str(e)}"]
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
        "duckdb": "duckdb",
        "parquet": "duckdb",
        "csv": "duckdb",
        "excel": "duckdb"
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