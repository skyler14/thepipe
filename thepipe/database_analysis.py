"""
Database analysis utilities for natural language processing and query generation.
This module contains streamlined functions for analyzing databases, generating SQL queries from
natural language, and creating concise prompts for different query types.
"""

from typing import Dict, List, Any, Optional, Union
import pandas as pd
import re
from .core import Chunk

DUCKDB_SOURCE_VIEW = "source_data"
DUCKDB_FILE_SOURCE_TYPES = {"parquet", "csv", "excel", "orc", "feather", "json", "jsonl"}

def get_all_tables(db_instance, db_type: str = None, verbose: bool = False) -> List[str]:
    """Retrieve all available tables in the database."""
    tables = []
    
    # Try jupysql metadata API
    try:
        if hasattr(db_instance, 'tables'):
            tables_df = db_instance.tables()
            if isinstance(tables_df, pd.DataFrame) and not tables_df.empty:
                tables.extend(tables_df.iloc[:, 0].tolist())
                if verbose:
                    print(f"[thepipe] Found {len(tables)} tables via jupysql metadata")
                return tables
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error getting tables via metadata API: {str(e)}")
    
    # Try SQLite metadata
    try:
        tables_df = db_instance.query("SELECT name FROM sqlite_master WHERE type='table' OR type='view'")
        if not tables_df.empty:
            tables.extend(tables_df['name'].tolist())
            if verbose:
                print(f"[thepipe] Found {len(tables)} tables via SQLite metadata")
            return tables
    except Exception:
        pass
    
    # Try information_schema
    try:
        tables_df = db_instance.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        if not tables_df.empty:
            tables.extend(tables_df['table_name'].tolist())
            if verbose:
                print(f"[thepipe] Found {len(tables)} tables via information_schema")
            return tables
    except Exception:
        pass
    
    # Default tables based on db_type
    if db_type in DUCKDB_FILE_SOURCE_TYPES:
        tables.append(DUCKDB_SOURCE_VIEW)
    
    return tables

def get_table_name(db_instance, db_type: str = None, verbose: bool = False) -> str:
    """
    Determine the appropriate table or view name to use with a database connection.
    Simplified version that gets the first table from get_all_tables.
    """
    tables = get_all_tables(db_instance, db_type, verbose)
    if not tables:
        raise ValueError("Could not determine database table name. Please provide a table name explicitly.")
    return tables[0]

def get_schema_for_all_tables(db_instance, tables: List[str], verbose: bool = False) -> str:
    """Generate schema information for all tables."""
    schema_text = "## Database Schema\n\n"
    
    for table in tables:
        try:
            sample_df = db_instance.query(f"SELECT * FROM {table} LIMIT 1")
            
            schema_text += f"### Table: {table}\n\n"
            schema_text += "| Column | Type |\n"
            schema_text += "|--------|------|\n"
            
            for col_name, dtype in sample_df.dtypes.items():
                schema_text += f"| {col_name} | {dtype} |\n"
            
            schema_text += "\n"
            
        except Exception as e:
            if verbose:
                print(f"[thepipe] Error getting schema for table {table}: {str(e)}")
            schema_text += f"### Table: {table}\n\n*Schema information not available*\n\n"
    
    return schema_text

def detect_relationships(db_instance, tables: List[str], verbose: bool = False) -> List[Dict]:
    """Detect potential relationships between tables."""
    relationships = []
    
    # Only try for standard database types
    try:
        # Try standard FK information (works for MySQL, PostgreSQL)
        fk_query = """
        SELECT
            tc.table_name as table_name,
            kcu.column_name as column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu ON ccu.constraint_name = tc.constraint_name
        WHERE constraint_type = 'FOREIGN KEY'
        """
        
        try:
            fk_df = db_instance.query(fk_query)
            if not fk_df.empty:
                for _, row in fk_df.iterrows():
                    relationships.append({
                        'table': row['table_name'],
                        'column': row['column_name'],
                        'foreign_table': row['foreign_table_name'],
                        'foreign_column': row['foreign_column_name']
                    })
                return relationships
        except Exception:
            pass
            
        # If standard approach fails, try heuristic detection
        common_columns = {}
        
        for table in tables:
            try:
                sample_df = db_instance.query(f"SELECT * FROM {table} LIMIT 1")
                columns = sample_df.columns.tolist()
                
                for col in columns:
                    if col.endswith('_id') or col == 'id':  # Potential key columns
                        if col not in common_columns:
                            common_columns[col] = []
                        common_columns[col].append(table)
            except Exception:
                continue
        
        # Create relationship entries for columns that appear in multiple tables
        for col, tables_list in common_columns.items():
            if len(tables_list) > 1:
                for i in range(len(tables_list)):
                    for j in range(i+1, len(tables_list)):
                        relationships.append({
                            'table': tables_list[i],
                            'column': col,
                            'foreign_table': tables_list[j],
                            'foreign_column': col,
                            'confidence': 'heuristic'
                        })
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error detecting relationships: {str(e)}")
    
    return relationships

def is_sql(query: str) -> bool:
    """Determine if a query is SQL or natural language."""
    if not query or not isinstance(query, str):
        return False
        
    query_lower = query.lower().strip()
    sql_keywords = [
        "select ", "show ", "describe ", "insert ", "update ", 
        "delete ", "create ", "alter ", "drop ", "truncate ", 
        "grant ", "revoke ", "commit ", "rollback ", "use "
    ]
    
    return any(query_lower.startswith(keyword) for keyword in sql_keywords)

def format_analysis_for_llm(analysis: Dict[str, Any]) -> str:
    """Format analysis results for LLM consumption."""
    analysis_text = ""
    
    if 'error' in analysis:
        return f"Error in analysis: {analysis['error']}\n\n"
        
    # Basic dataset info
    if 'total_rows' in analysis:
        analysis_text += f"Dataset contains {analysis['total_rows']:,} rows.\n"
        
    # Column information
    if 'columns' in analysis:
        analysis_text += f"Dataset has {len(analysis['columns'])} columns.\n"
        
    # Column type categorization
    if 'column_types' in analysis:
        cat_cols = analysis['column_types'].get('categorical', [])
        num_cols = analysis['column_types'].get('numeric', [])
        
        if cat_cols:
            analysis_text += f"\nCategorical columns: {', '.join(cat_cols[:10])}"
            if len(cat_cols) > 10:
                analysis_text += f" and {len(cat_cols) - 10} more"
            analysis_text += "\n"
            
        if num_cols:
            analysis_text += f"\nNumeric columns: {', '.join(num_cols[:10])}"
            if len(num_cols) > 10:
                analysis_text += f" and {len(num_cols) - 10} more"
            analysis_text += "\n"
    
    # Add key statistics
    if 'column_stats' in analysis:
        analysis_text += "\nKey column statistics:\n"
        
        # Show sample of categorical columns
        for col, stats in analysis['column_stats'].items():
            if stats['type'] == 'categorical':
                analysis_text += f"\n{col} (top values): "
                analysis_text += ', '.join([f"{val['value']} ({val['percentage']:.1f}%)" 
                                          for val in stats['top_values'][:3]])
                break
        
        # Show sample of numeric columns
        for col, stats in analysis['column_stats'].items():
            if stats['type'] == 'numeric':
                stat_data = stats['stats']
                analysis_text += f"\n{col} (numeric): range {stat_data['min']} to {stat_data['max']}, avg {stat_data['mean']}"
                break
    
    # Add key columns
    if 'potential_keys' in analysis and analysis['potential_keys']:
        analysis_text += f"\nPotential key columns: {', '.join(analysis['potential_keys'][:3])}\n"
        
    if 'date_columns' in analysis and analysis['date_columns']:
        analysis_text += f"\nDate columns: {', '.join(analysis['date_columns'][:3])}\n"
        
    return analysis_text

def get_auto_analysis(db_instance, db_type: str = None, view_name: str = None, 
                     max_samples: int = 5, verbose: bool = False,
                     options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Automatically analyze database to generate useful insights."""
    analysis = {}
    options = options or {}
    max_samples = options.get("max_samples", max_samples)
    
    try:
        # Auto-detect view_name if not provided
        if view_name is None:
            view_name = get_table_name(db_instance, db_type, verbose)
            
        if verbose:
            print(f"[thepipe] Analyzing view/table: {view_name}")
        
        # Get basic info about the dataset
        count_df = db_instance.query(f"SELECT COUNT(*) as count FROM {view_name}")
        total_rows = count_df['count'].iloc[0]
        analysis['total_rows'] = total_rows
        
        # Get sample data and columns
        sample_df = db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
        columns = sample_df.columns.tolist()
        analysis['columns'] = columns
        
        # Generate statistics for each column
        column_stats = {}
        categorical_columns = []
        numeric_columns = []
        
        # Identify column types
        for col in columns:
            try:
                # Check if column is numeric
                type_check = db_instance.query(f"""
                    SELECT typeof("{col}") as data_type
                    FROM {view_name} WHERE "{col}" IS NOT NULL LIMIT 1
                """)
                
                if len(type_check) > 0:
                    data_type = type_check['data_type'].iloc[0].lower()
                    if data_type in ('integer', 'real', 'double', 'float', 'decimal', 'numeric'):
                        numeric_columns.append(col)
                    else:
                        # Check distinct count for categorical columns
                        distinct_count = db_instance.query(f"""
                            SELECT COUNT(DISTINCT "{col}") as count 
                            FROM {view_name} WHERE "{col}" IS NOT NULL
                        """)
                        
                        if distinct_count['count'].iloc[0] < min(50, total_rows * 0.1):
                            categorical_columns.append(col)
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error analyzing column {col}: {str(e)}")
        
        analysis['column_types'] = {
            'categorical': categorical_columns,
            'numeric': numeric_columns
        }
        
        # Analyze a few categorical columns
        for col in categorical_columns[:max_samples]:
            try:
                value_counts = db_instance.query(f"""
                    SELECT 
                        "{col}" as value,
                        COUNT(*) as count,
                        CAST(COUNT(*) * 100.0 / {total_rows} AS REAL) as percentage
                    FROM {view_name}
                    WHERE "{col}" IS NOT NULL
                    GROUP BY "{col}"
                    ORDER BY count DESC
                    LIMIT {max_samples}
                """)
                
                column_stats[col] = {
                    'type': 'categorical',
                    'top_values': value_counts.to_dict(orient='records'),
                    'distinct_count': len(value_counts)
                }
            except Exception:
                pass
        
        # Analyze a few numeric columns
        for col in numeric_columns[:max_samples]:
            try:
                stats = db_instance.query(f"""
                    SELECT 
                        MIN("{col}") as min,
                        MAX("{col}") as max,
                        AVG("{col}") as mean,
                        COUNT(*) - COUNT("{col}") as null_count
                    FROM {view_name}
                    WHERE "{col}" IS NOT NULL
                """)
                
                column_stats[col] = {
                    'type': 'numeric',
                    'stats': stats.to_dict(orient='records')[0]
                }
            except Exception:
                pass
        
        analysis['column_stats'] = column_stats
        
        # Find date/time columns
        date_columns = []
        for col in columns:
            if any(date_term in col.lower() for date_term in ['date', 'time', 'year', 'month', 'day']):
                date_columns.append(col)
        
        analysis['date_columns'] = date_columns
        
        # Find potential key columns (check for uniqueness)
        analysis['potential_keys'] = []
        for col in columns[:5]:  # Only check a few columns
            try:
                unique_check = db_instance.query(f"""
                    SELECT COUNT(DISTINCT "{col}") as unique_count
                    FROM {view_name}
                """)
                
                if unique_check['unique_count'].iloc[0] == total_rows:
                    analysis['potential_keys'].append(col)
            except Exception:
                pass
                
        return analysis
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error performing auto-analysis: {str(e)}")
        return {'error': str(e)}

def get_sql_examples_for_intent(query_intent: str, view_name: str) -> str:
    """Generate simplified SQL examples based on query intent."""
    
    # ONE-LINERS by query intent - minimal and concise
    intent_examples = {
        "text_analysis": f"SELECT word, COUNT(*) FROM (SELECT unnest(regexp_split_to_array(lower(text_column), '\\\\s+')) as word FROM {view_name}) GROUP BY word ORDER BY COUNT(*) DESC LIMIT 20;",
        "numeric_analysis": f"SELECT MIN(num_column), MAX(num_column), AVG(num_column), STDDEV(num_column) FROM {view_name} WHERE num_column IS NOT NULL;",
        "time_analysis": f"SELECT DATE_TRUNC('month', date_column) as month, COUNT(*) FROM {view_name} GROUP BY month ORDER BY month;",
        "categorization": f"SELECT category_column, COUNT(*) as count FROM {view_name} GROUP BY category_column ORDER BY count DESC LIMIT 20;"
    }
    
    # Default simple example for all query types
    example = f"SELECT * FROM {view_name} LIMIT 10;"
    
    # Add specialized example if available
    if query_intent in intent_examples:
        example += f"\n{intent_examples[query_intent]}"
    
    return example

def fix_sql_syntax(sql_query: str) -> str:
    """Fix common SQL syntax errors in generated queries."""
    
    # Fix regex patterns
    sql_query = re.sub(r"r'(.*?)'", r"'\1'", sql_query)
    sql_query = re.sub(r'r"(.*?)"', r'"\1"', sql_query)
    
    # Fix escaping
    sql_query = sql_query.replace(r'\s', r'\\s')
    sql_query = sql_query.replace(r'\d', r'\\d')
    sql_query = sql_query.replace(r'\w', r'\\w')
    
    # Fix function names
    sql_query = sql_query.replace("REGEXP_EXTRACT", "regexp_matches")
    sql_query = sql_query.replace("EXTRACT(", "extract(")
    
    # Fix casting
    sql_query = re.sub(r"CAST\((.*?) AS ([A-Za-z]+)\)", r"CAST(\1 AS \2)", sql_query)
    
    return sql_query

def generate_fallback_query(view_name: str, columns: List[str], 
                          numeric_columns: List[str] = None,
                          categorical_columns: List[str] = None,
                          date_columns: List[str] = None) -> str:
    """Generate a simple fallback query based on available column types."""
    
    if not numeric_columns:
        numeric_columns = []
    if not categorical_columns:
        categorical_columns = []
    if not date_columns:
        date_columns = []
    
    # Try different query types based on available columns
    if categorical_columns:
        col = categorical_columns[0]
        return f"""
        SELECT "{col}", COUNT(*) as count
        FROM {view_name}
        GROUP BY "{col}"
        ORDER BY count DESC
        LIMIT 20
        """
    elif numeric_columns:
        col = numeric_columns[0]
        return f"""
        SELECT 
            MIN("{col}") as minimum,
            MAX("{col}") as maximum,
            AVG("{col}") as average
        FROM {view_name}
        WHERE "{col}" IS NOT NULL
        """
    elif date_columns:
        col = date_columns[0]
        return f"""
        SELECT 
            DATE_TRUNC('month', "{col}") as month,
            COUNT(*) as count
        FROM {view_name}
        GROUP BY month
        ORDER BY month
        """
    else:
        return f"SELECT * FROM {view_name} LIMIT 15"

def execute_fallback(query: str, db_instance, view_name: str, 
                   verbose: bool = False, error: Optional[str] = None,
                   failed_query: Optional[str] = None) -> List[Chunk]:
    """Execute a fallback query when NL-to-SQL conversion fails."""
    chunks = []
    result_text = f"## Natural Language Query\n\n{query}\n\n"
    
    if error and failed_query:
        result_text += f"## Error in Generated Query\n\n```sql\n{failed_query}\n```\n\n"
        result_text += f"Error: {error}\n\n"
    
    try:
        # Get column information
        sample_df = db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
        columns = sample_df.columns.tolist()
        
        # Simple column type detection
        numeric_columns = []
        categorical_columns = []
        date_columns = []
        
        for col in columns:
            try:
                check_query = f"SELECT typeof(\"{col}\") as data_type FROM {view_name} WHERE \"{col}\" IS NOT NULL LIMIT 1"
                type_df = db_instance.query(check_query)
                data_type = type_df['data_type'].iloc[0].lower() if not type_df.empty else ""
                
                if data_type in ('integer', 'real', 'double', 'float', 'decimal', 'numeric'):
                    numeric_columns.append(col)
                elif any(date_term in col.lower() for date_term in ['date', 'time', 'year', 'month', 'day']):
                    date_columns.append(col)
                else:
                    categorical_columns.append(col)
            except Exception:
                pass
        
        # Generate and execute fallback query
        fallback_query = generate_fallback_query(
            view_name=view_name,
            columns=columns,
            numeric_columns=numeric_columns,
            categorical_columns=categorical_columns,
            date_columns=date_columns
        )
        
        if verbose:
            print(f"[thepipe] Executing fallback query: {fallback_query}")
        
        result = db_instance.query(fallback_query)
        
        result_text += f"## Fallback Analysis\n\n"
        result_text += f"```sql\n{fallback_query}\n```\n\n"
        
        if isinstance(result, pd.DataFrame) and not result.empty:
            result_text += f"## Results ({len(result)} rows)\n\n"
            result_text += "```json\n"
            result_text += result.to_json(orient='records', indent=2)
            result_text += "\n```"
        else:
            result_text += "*No results found.*"
    except Exception as e:
        result_text += f"*Error executing fallback query: {str(e)}*"
    
    chunks.append(Chunk(path=f"database://fallback", text=result_text))
    return chunks
