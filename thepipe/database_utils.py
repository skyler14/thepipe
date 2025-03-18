import json
import os
from typing import Dict, List, Optional, Union, Any

from .core import Chunk

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
    
    This function handles all database interactions including:
    - Auto-detecting database type
    - Setting up connections
    - Executing queries (SQL or natural language)
    - Returning schema or preview information
    
    Args:
        connection_info: Connection string or config
        query: Natural language or SQL query
        db_type: Database type (auto-detected if not specified)
        mode: Processing mode ("schema", "preview", or None for query)
        verbose: If True, print detailed logs
        options: Additional options
        
    Returns:
        List of Chunk objects with data
    """
    try:
        # Auto-detect database type if not specified
        if not db_type:
            if isinstance(connection_info, str):
                db_type = detect_database_type(connection_info)
                if verbose:
                    print(f"[thepipe] Auto-detected database type: {db_type}")
            elif isinstance(connection_info, dict) and 'type' in connection_info:
                db_type = connection_info['type']
        
        # If still not determined, use a default
        if not db_type:
            if verbose:
                print("[thepipe] Could not determine database type, assuming SQL database")
            db_type = "sql"  # Generic SQL type
            
        # Import requirements
        try:
            from sqlalchemy import create_engine, text
            import pandas as pd
        except ImportError:
            return [Chunk(
                path="database://error",
                texts=["Required libraries not installed. Please install: pip install sqlalchemy pandas"]
            )]
            
        # Initialize options
        options = options or {}
        max_rows = options.get("max_rows", 15)
        tables = options.get("tables")
        
        # If no arguments provided with --db, default to preview mode
        if query is None and mode is None:
            mode = "preview"
            
        # Setup database connection
        engine = setup_database_connection(connection_info, db_type, verbose, options)
        
        # Create SQLDatabase adapter for schema operations
        # Only import LlamaIndex components when needed
        from llama_index.core import SQLDatabase
        sql_database = SQLDatabase(engine, include_tables=tables)
        
        # Get schema information
        schema_chunk = get_schema_info(sql_database, db_type, connection_info, verbose)
        
        # Handle schema or preview mode
        if mode == "schema":
            return [schema_chunk]
        elif mode == "preview":
            preview_chunk = get_data_preview(sql_database, db_type, connection_info, verbose, max_rows)
            return [schema_chunk, preview_chunk]
            
        # Handle empty query (should have been caught by preview mode above)
        if not query:
            return [schema_chunk]
            
        # Determine if query is SQL or natural language
        is_sql_query = is_sql(query)
        
        if is_sql_query:
            # Direct SQL execution - no LLM needed
            return execute_sql_query(
                engine, query, schema_chunk, db_type, connection_info, max_rows, verbose
            )
        else:
            # Natural language query processing - requires LLM
            return process_nl_query(
                sql_database, query, schema_chunk, db_type, connection_info, tables, verbose, options
            )
            
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error accessing database: {str(e)}")
        return [Chunk(
            path=f"database://{db_type if db_type else 'unknown'}/error",
            texts=[f"Error accessing database: {str(e)}"]
        )]

def detect_database_type(source: str) -> str:
    """Detect database type from connection string or file path."""
    if source.startswith("postgresql://"):
        return "postgres"
    elif source.startswith("mysql://"):
        return "mysql"
    elif source.startswith("sqlite://"):
        return "sqlite"
    elif source.startswith("mssql://"):
        return "mssql"
    # Check if it's a directory containing parquet files
    elif os.path.isdir(source):
        # Check if directory contains parquet files
        for file in os.listdir(source):
            if file.endswith('.parquet') or file.endswith('.parq'):
                return "parquet"
    elif source.endswith(".parquet") or source.endswith(".parq") or '/parquet/' in source or "*.parquet" in source:
        return "parquet"
    elif source.endswith(".csv"):
        return "csv"
    elif source.endswith(".xlsx") or source.endswith(".xls"):
        return "excel"
    else:
        return "unknown"

def setup_database_connection(connection_info, db_type, verbose, options=None):
    """Setup database connection based on type and return engine."""
    from sqlalchemy import create_engine
    import os
    
    if db_type in ["parquet", "duckdb"]:
        try:
            import duckdb
            
            # Create a more persistent connection - file-based or memory with longer lifecycle
            db_file = None
            if options and options.get("temp_db_file"):
                # Use provided temp file
                db_file = options.get("temp_db_file")
            else:
                # Create temp file for DuckDB database
                import tempfile
                temp_db = tempfile.NamedTemporaryFile(suffix='.duckdb', delete=False)
                db_file = temp_db.name
                temp_db.close()
                
            # Connect to the database file instead of :memory:
            conn = duckdb.connect(db_file)
            
            # Handle S3 paths
            if isinstance(connection_info, str) and connection_info.startswith("s3://"):
                conn.execute("INSTALL httpfs; LOAD httpfs;")
                
                # Set S3 credentials if provided
                if options and "s3_credentials" in options:
                    creds = options["s3_credentials"]
                    conn.execute(f"SET s3_access_key_id='{creds.get('access_key')}'")
                    conn.execute(f"SET s3_secret_access_key='{creds.get('secret_key')}'")
            
            # Create engine connecting to the same file
            engine = create_engine(f"duckdb:///{db_file}")
            
            # Handle directory of parquet files
            if isinstance(connection_info, str):
                if os.path.isdir(connection_info):
                    if verbose:
                        print(f"[thepipe] Detected directory of parquet files: {connection_info}")
                    # Use glob pattern to match all parquet files in directory
                    path_pattern = os.path.join(connection_info, "*.parquet")
                    table_name = "parquet_data"
                    
                    # Create the view in the DuckDB connection
                    conn.execute(f"CREATE VIEW {table_name} AS SELECT * FROM '{path_pattern}'")
                    
                    # Register the connection with engine to keep it alive
                    # This is key - attaching the connection to the engine's context
                    from sqlalchemy import event
                    @event.listens_for(engine, 'connect')
                    def connect(dbapi_connection, connection_record):
                        connection_record.info['duckdb_conn'] = conn
                    
                elif "*" in connection_info:
                    # Already a glob pattern
                    table_name = "parquet_data"
                    conn.execute(f"CREATE VIEW {table_name} AS SELECT * FROM '{connection_info}'")
                    
                    # Same connection attachment
                    from sqlalchemy import event
                    @event.listens_for(engine, 'connect')
                    def connect(dbapi_connection, connection_record):
                        connection_record.info['duckdb_conn'] = conn
                    
                else:
                    # Regular single file
                    table_name = "parquet_data"
                    conn.execute(f"CREATE VIEW {table_name} AS SELECT * FROM '{connection_info}'")
                    
                    # Same connection attachment
                    from sqlalchemy import event
                    @event.listens_for(engine, 'connect')
                    def connect(dbapi_connection, connection_record):
                        connection_record.info['duckdb_conn'] = conn
            
            if verbose:
                print(f"[thepipe] Connected to parquet data source with table: parquet_data")
                
            return engine
            
        except Exception as e:
            if verbose:
                print(f"[thepipe] Error setting up DuckDB connection: {str(e)}")
            raise
    else:
        # Regular SQL database connection
        if isinstance(connection_info, str):
            return create_engine(connection_info)
        else:
            # Create connection string from config
            conn_str = create_connection_string(connection_info, db_type)
            return create_engine(conn_str)

def create_connection_string(config: Dict, db_type: str) -> str:
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

def get_schema_info(sql_database, db_type: str, connection_source: str, verbose: bool) -> Chunk:
    """Get database schema information."""
    try:
        tables = sql_database.get_usable_table_names()
        if not tables and db_type == "parquet":
            # Special handling for parquet files
            try:
                import pandas as pd
                import os
                
                if verbose:
                    print("[thepipe] Using pandas to analyze parquet file structure")
                
                # For directory with multiple parquet files
                if os.path.isdir(connection_source):
                    import glob
                    parquet_files = glob.glob(os.path.join(connection_source, "*.parquet"))
                    if parquet_files:
                        sample_file = parquet_files[0]
                        df = pd.read_parquet(sample_file)
                        tables = ["parquet_data"]
                    else:
                        return Chunk(
                            path=f"database://{db_type}/schema",
                            texts=["No parquet files found in directory"]
                        )
                else:
                    # Single parquet file
                    df = pd.read_parquet(connection_source)
                    tables = ["parquet_data"]
                
                schema_info = "## Database Schema\n\n"
                schema_info += "### Table: parquet_data\n\n"
                schema_info += "| Column | Type |\n"
                schema_info += "|--------|------|\n"
                
                for col_name, dtype in df.dtypes.items():
                    schema_info += f"| {col_name} | {dtype} |\n"
                    
                return Chunk(
                    path=f"database://{db_type}/schema",
                    texts=[schema_info]
                )
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error analyzing parquet with pandas: {str(e)}")
                schema_info = f"## Database Schema\n\nError analyzing parquet structure: {str(e)}"
                return Chunk(
                    path=f"database://{db_type}/schema",
                    texts=[schema_info]
                )
                
        schema_info = "## Database Schema\n\n"
        for table in tables:
            schema_info += f"### Table: {table}\n"
            schema_info += sql_database.get_table_schema(table)
            schema_info += "\n\n"
            
        return Chunk(
            path=f"database://{db_type}/schema",
            texts=[schema_info]
        )
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error getting schema: {str(e)}")
        return Chunk(
            path=f"database://{db_type}/schema",
            texts=[f"Error retrieving schema: {str(e)}"]
        )
    
def get_data_preview(sql_database, db_type: str, connection_source: str, verbose: bool, max_rows: int = 5) -> Chunk:
    """Generate preview statistics for tables."""
    try:
        tables = sql_database.get_usable_table_names()
        
        # Special handling for parquet files when tables is empty
        if not tables and db_type == "parquet":
            try:
                import pandas as pd
                import numpy as np
                import os
                
                if verbose:
                    print("[thepipe] Using pandas to analyze parquet data")
                
                # For directory with multiple parquet files
                if os.path.isdir(connection_source):
                    import glob
                    parquet_files = glob.glob(os.path.join(connection_source, "*.parquet"))
                    if parquet_files:
                        sample_file = parquet_files[0]
                        df = pd.read_parquet(sample_file)
                        preview_text = f"## Data Preview\n\n### Parquet Data\n\n"
                        preview_text += f"Total files: {len(parquet_files)}\n"
                        preview_text += f"Sample file: {os.path.basename(sample_file)}\n"
                    else:
                        return Chunk(
                            path=f"database://{db_type}/preview",
                            texts=["No parquet files found in directory"]
                        )
                else:
                    # Single parquet file
                    df = pd.read_parquet(connection_source)
                    preview_text = f"## Data Preview\n\n### Parquet Data\n\n"
                    preview_text += f"File: {os.path.basename(connection_source)}\n"
                
                # Add basic statistics
                preview_text += f"Row count: {len(df):,}\n\n"
                
                # Generate basic statistics for each column
                preview_text += "#### Column Statistics\n\n"
                
                for col in df.columns:
                    preview_text += f"**{col}**\n"
                    # Detect column type
                    if pd.api.types.is_numeric_dtype(df[col]):
                        # For numeric columns
                        stats = df[col].describe()
                        preview_text += f"- Type: Numeric\n"
                        preview_text += f"- Min: {stats['min']}\n"
                        preview_text += f"- Max: {stats['max']}\n"
                        preview_text += f"- Mean: {stats['mean']}\n"
                        preview_text += f"- Null count: {df[col].isna().sum()}\n"
                    elif pd.api.types.is_string_dtype(df[col]):
                        # For string columns
                        unique_count = df[col].nunique()
                        preview_text += f"- Type: String\n"
                        preview_text += f"- Unique values: {unique_count}\n"
                        preview_text += f"- Null count: {df[col].isna().sum()}\n"
                        # Show top values if not too many unique values
                        if unique_count <= 10:
                            value_counts = df[col].value_counts().head(5)
                            preview_text += "- Most common values:\n"
                            for val, count in value_counts.items():
                                preview_text += f"  - {val}: {count}\n"
                    elif pd.api.types.is_datetime64_dtype(df[col]):
                        # For datetime columns
                        min_date = df[col].min()
                        max_date = df[col].max()
                        preview_text += f"- Type: Datetime\n"
                        preview_text += f"- Range: {min_date} to {max_date}\n"
                        preview_text += f"- Null count: {df[col].isna().sum()}\n"
                    else:
                        # For other types
                        preview_text += f"- Type: Other ({df[col].dtype})\n"
                        preview_text += f"- Unique values: {df[col].nunique()}\n"
                        preview_text += f"- Null count: {df[col].isna().sum()}\n"
                    
                    preview_text += "\n"
                
                # Add sample data
                preview_text += "#### Sample Data\n\n```\n"
                preview_text += df.head(max_rows).to_string()
                preview_text += "\n```\n\n"
                
                return Chunk(
                    path=f"database://{db_type}/preview",
                    texts=[preview_text]
                )
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error analyzing parquet with pandas: {str(e)}")
                preview_text = f"## Data Preview\n\nError analyzing parquet data: {str(e)}"
                return Chunk(
                    path=f"database://{db_type}/preview",
                    texts=[preview_text]
                )
        
        preview_text = "## Data Preview\n\n"
        
        for table in tables:
            preview_text += f"### Table: {table}\n\n"
            
            try:
                # Sample query to get row count
                count_query = f"SELECT COUNT(*) FROM {table}"
                with sql_database.engine.connect() as conn:
                    from sqlalchemy import text
                    result = conn.execute(text(count_query))
                    count = result.scalar()
                    preview_text += f"Row count: {count:,}\n\n"
                
                # Sample query to get a few rows
                sample_query = f"SELECT * FROM {table} LIMIT {max_rows}"
                with sql_database.engine.connect() as conn:
                    import pandas as pd
                    sample_df = pd.read_sql(sample_query, conn)
                    
                    if not sample_df.empty:
                        preview_text += "Sample data:\n\n```\n"
                        preview_text += sample_df.to_string()
                        preview_text += "\n```\n\n"
                
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error getting preview for table {table}: {str(e)}")
                preview_text += f"Error getting preview: {str(e)}\n\n"
                
        return Chunk(
            path=f"database://{db_type}/preview",
            texts=[preview_text]
        )
            
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error generating data preview: {str(e)}")
        return Chunk(
            path=f"database://{db_type}/preview",
            texts=[f"Error generating data preview: {str(e)}"]
        )
                
def execute_sql_query(engine, query: str, schema_chunk: Chunk, db_type: str, 
                     connection_source: str, max_rows: int, verbose: bool) -> List[Chunk]:
    """Execute SQL query with safe handling of large results."""
    try:
        from sqlalchemy import text
        import pandas as pd
        
        chunks = [schema_chunk]  # Always include schema
        
        # Add safety limit if not present for SELECT queries
        if query.lower().strip().startswith("select") and "limit" not in query.lower():
            query = f"{query} LIMIT {max_rows}"
            if verbose:
                print(f"[thepipe] Added safety LIMIT {max_rows} to query")
                
        # Execute query
        with engine.connect() as conn:
            result = conn.execute(text(query))
            
            if query.lower().strip().startswith("select"):
                # Process SELECT results
                df = pd.DataFrame(result.fetchall(), columns=result.keys())
                
                chunks.append(Chunk(
                    path=f"database://{db_type}/query",
                    texts=[f"## SQL Query\n\n```sql\n{query}\n```\n\n## Results ({len(df)} rows)\n\n```json\n{df.to_json(orient='records', indent=2)}\n```"]
                ))
            else:
                # Process non-SELECT results
                chunks.append(Chunk(
                    path=f"database://{db_type}/query",
                    texts=[f"## SQL Query\n\n```sql\n{query}\n```\n\n## Results\n\nQuery executed successfully. Rows affected: {result.rowcount}"]
                ))
                
        return chunks
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error executing SQL query: {str(e)}")
        return [
            schema_chunk,
            Chunk(
                path=f"database://{db_type}/error",
                texts=[f"Error executing SQL query: {str(e)}"]
            )
        ]
        
def process_nl_query(sql_database, query: str, schema_chunk: Chunk, db_type: str, 
                  connection_source: str, tables: Optional[List[str]], 
                  verbose: bool, options: Optional[Dict] = None) -> List[Chunk]:
    """Process natural language query using LlamaIndex."""
    chunks = [schema_chunk]  # Always include schema
    
    # Check if LLM options are provided
    llm_config = options.get("llm_extractor", {}) if options else {}
    if not llm_config:
        if verbose:
            print("[thepipe] No LLM configuration provided for natural language query")
        return [
            schema_chunk,
            Chunk(
                path=f"database://{db_type}/error",
                texts=["Natural language queries require LLM configuration. "
                      "Please provide LLM configuration via the --options parameter:\n"
                      "--options '{\"llm_extractor\": {\"model\": \"model-name\", \"api_key\": \"your-api-key\", \"api_base\": \"endpoint-url\"}}'\n"
                      "or set the OPENAI_API_KEY environment variable."]
            )
        ]
    
    try:
        # Set up OpenAI compatibility with the provided configuration
        api_key = llm_config.get("api_key")
        api_base = llm_config.get("api_base")
        model = llm_config.get("model")
        
        if verbose:
            endpoint = api_base or "default OpenAI endpoint"
            print(f"[thepipe] Configuring LLM with model: {model} using {endpoint}")
        
        # Only import these if we actually need them
        import os
        from llama_index.core.query_engine import NLSQLTableQueryEngine
        from llama_index.llms.openai import OpenAI
        from llama_index.core import Settings
        
        # Set environment variables for libraries that read from them
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        
        if api_base:
            os.environ["OPENAI_API_BASE"] = api_base
        
        # Create the LLM instance with explicit parameters
        llm = OpenAI(
            model=model or "gpt-3.5-turbo",
            api_key=api_key,
            api_base=api_base
        )
        
        # Set it as the global LLM for LlamaIndex
        Settings.llm = llm
        
        if verbose:
            print(f"[thepipe] Processing natural language query: {query}")
        
        # Create query engine with the configured LLM
        query_engine = NLSQLTableQueryEngine(
            sql_database=sql_database,
            tables=tables
        )
        
        # Execute query
        if verbose:
            print("[thepipe] Executing query...")
            
        response = query_engine.query(query)
        
        # Extract SQL query
        sql_query = response.metadata.get("sql_query", "SQL query not available")
        
        if verbose:
            print(f"[thepipe] Generated SQL: {sql_query}")
            
        chunks.append(Chunk(
            path=f"database://{db_type}/nl_query",
            texts=[f"## Natural Language Query\n\n{query}\n\n## Generated SQL\n\n```sql\n{sql_query}\n```\n\n## Results\n\n{str(response)}"]
        ))
        
        return chunks
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error processing natural language query: {str(e)}")
            import traceback
            traceback.print_exc()
        return [
            schema_chunk,
            Chunk(
                path=f"database://{db_type}/error",
                texts=[f"Error processing natural language query: {str(e)}"]
            )
        ]