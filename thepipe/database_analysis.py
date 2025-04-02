"""
Database analysis utilities for natural language processing and query generation.
This module contains streamlined functions for analyzing databases, generating SQL queries from
natural language, and creating concise prompts for different query types.
"""

from typing import Dict, List, Any, Optional, Union
import pandas as pd
import re
from .core import Chunk

def get_table_name(db_instance, db_type: str = None, verbose: bool = False) -> str:
    """Determine the appropriate table or view name to use with a database connection."""
    if verbose:
        print(f"[thepipe] Getting table name for db_type: {db_type}")
    
    # Try jupysql metadata API
    try:
        if hasattr(db_instance, 'tables'):
            tables = db_instance.tables()
            if isinstance(tables, pd.DataFrame) and not tables.empty:
                table_name = tables.iloc[0, 0]
                if verbose:
                    print(f"[thepipe] Found table via jupysql metadata: {table_name}")
                return table_name
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error getting tables via metadata API: {str(e)}")
    
    # Try SQLite metadata
    try:
        tables_df = db_instance.query("SELECT name FROM sqlite_master WHERE type='table' OR type='view'")
        if not tables_df.empty:
            table_name = tables_df['name'].iloc[0]
            if verbose:
                print(f"[thepipe] Found table via SQLite metadata: {table_name}")
            return table_name
    except Exception:
        if verbose:
            print("[thepipe] SQLite metadata approach failed")
    
    # Try information_schema
    try:
        tables_df = db_instance.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        if not tables_df.empty:
            table_name = tables_df['table_name'].iloc[0]
            if verbose:
                print(f"[thepipe] Found table via information_schema: {table_name}")
            return table_name
    except Exception:
        pass
    
    # Try default view names based on db_type
    default_views = {"parquet": "parquet_data", "csv": "csv_data", "excel": "excel_data"}
    if db_type in default_views:
        view_name = default_views[db_type]
        try:
            db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
            return view_name
        except Exception:
            pass
    
    # Try common view names
    for view_name in ["parquet_data", "csv_data", "excel_data", "data"]:
        try:
            db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
            return view_name
        except Exception:
            pass
    
    raise ValueError("Could not determine database table name. Please provide a table name explicitly.")

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
                    max_samples: int = 5, verbose: bool = False) -> Dict[str, Any]:
    """Automatically analyze database to generate useful insights."""
    analysis = {}
    
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
        for col in categorical_columns[:5]:
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
        for col in numeric_columns[:5]:
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
    
    # THIS IS THE ONE VERBOSE SECTION MAINTAINED FOR SPECIFIC ANALYSIS TYPES
    analysis_types = {
        "text_analysis": f"""
        # Text Analysis Examples:
        
        # Word frequency in text column:
        SELECT word, COUNT(*) as frequency
        FROM (SELECT unnest(regexp_split_to_array(lower("text_column"), '\\\\s+')) as word FROM {view_name})
        WHERE length(word) > 3
        GROUP BY word
        ORDER BY frequency DESC
        LIMIT 20;
        
        # Finding specific terms:
        SELECT * FROM {view_name}
        WHERE lower("text_column") LIKE '%keyword%'
        LIMIT 20;
        """,
        
        "numeric_analysis": f"""
        # Numeric Analysis Examples:
        
        # Basic statistics:
        SELECT 
            MIN("number_column") as minimum,
            MAX("number_column") as maximum,
            AVG("number_column") as average,
            STDDEV("number_column") as standard_deviation
        FROM {view_name}
        WHERE "number_column" IS NOT NULL;
        
        # Value distribution into buckets:
        SELECT 
            FLOOR("number_column" / 10) * 10 as bucket,
            COUNT(*) as count
        FROM {view_name}
        GROUP BY bucket
        ORDER BY bucket;
        """,
        
        "time_analysis": f"""
        # Time Analysis Examples:
        
        # Trend by month:
        SELECT 
            DATE_TRUNC('month', "date_column") as month,
            COUNT(*) as count
        FROM {view_name}
        WHERE "date_column" IS NOT NULL
        GROUP BY month
        ORDER BY month;
        
        # Year breakdown:
        SELECT 
            extract(YEAR FROM "date_column") as year,
            COUNT(*) as count
        FROM {view_name}
        WHERE "date_column" IS NOT NULL
        GROUP BY year
        ORDER BY year;
        """,
        
        "categorization": f"""
        # Category Analysis Examples:
        
        # Basic distribution:
        SELECT 
            "category_column",
            COUNT(*) as count,
            CAST(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM {view_name}) AS DECIMAL(5,2)) as percentage
        FROM {view_name}
        GROUP BY "category_column"
        ORDER BY count DESC
        LIMIT 20;
        
        # Cross-tabulation:
        SELECT 
            "category1",
            "category2",
            COUNT(*) as count
        FROM {view_name}
        GROUP BY "category1", "category2"
        ORDER BY count DESC
        LIMIT 25;
        """
    }
    
    # Default simple examples for all query types
    examples = f"""
    # Basic SQL Examples:
    
    # Simple selection:
    SELECT * FROM {view_name} LIMIT 10;
    
    # Counting and grouping:
    SELECT column_name, COUNT(*) as count 
    FROM {view_name} 
    GROUP BY column_name 
    ORDER BY count DESC 
    LIMIT 15;
    """
    
    # Add specialized examples if we have them
    if query_intent in analysis_types:
        examples += analysis_types[query_intent]
    
    return examples

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
    
    chunks.append(Chunk(path=f"database://fallback", texts=[result_text]))
    return chunks

def generate_data_insights(db_instance, natural_language_query: str, 
                         db_type: str = None, view_name: str = None,
                         llm_config: Optional[Dict[str, Any]] = None,
                         max_iterations: int = 1, verbose: bool = False) -> List[Chunk]:
    """Generate insights from database using LLM-driven analysis."""
    # Auto-detect view_name if not provided
    if view_name is None:
        try:
            view_name = get_table_name(db_instance, db_type, verbose)
        except ValueError as e:
            return [Chunk(path=f"database://{db_type or 'unknown'}/error", 
                         texts=[f"Error determining table name: {str(e)}"])]
    
    # Get schema information
    schema_text = ""
    try:
        sample_df = db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
        
        schema_text = "## Database Schema\n\n"
        schema_text += f"### Table: {view_name}\n\n"
        schema_text += "| Column | Type |\n"
        schema_text += "|--------|------|\n"
        
        for col_name, dtype in sample_df.dtypes.items():
            schema_text += f"| {col_name} | {dtype} |\n"
    except Exception:
        schema_text = "## Database Schema\n\nSchema information not available."
    
    schema_chunk = Chunk(path=f"database://{db_type}/schema", texts=[schema_text])
    chunks = [schema_chunk]
    
    # Check if LLM configuration is provided
    if not llm_config:
        chunks.append(Chunk(path=f"database://{db_type}/error",
                          texts=["Insight generation requires LLM configuration."]
                         ))
        return chunks
    
    try:
        import os
        from openai import OpenAI
        
        # Set up OpenAI client
        api_key = llm_config.get("api_key", os.environ.get("OPENAI_API_KEY"))
        api_base = llm_config.get("api_base", os.environ.get("OPENAI_API_BASE"))
        model = llm_config.get("model", "gpt-3.5-turbo")
        
        if not api_key:
            chunks.append(Chunk(path=f"database://{db_type}/error",
                              texts=["API key is required for insight generation."]
                             ))
            return chunks
        
        client_args = {"api_key": api_key}
        if api_base:
            client_args["base_url"] = api_base
            
        client = OpenAI(**client_args)
        
        # Get initial data analysis
        initial_analysis = get_auto_analysis(
            db_instance=db_instance,
            db_type=db_type,
            view_name=view_name,
            verbose=verbose
        )
        
        analysis_summary = format_analysis_for_llm(initial_analysis)
        
        # Generate SQL queries to answer the question
        prompt = f"""
        You are a data analyst analyzing a database to answer this question:
        
        QUESTION: {natural_language_query}
        
        DATABASE SCHEMA:
        {schema_text}
        
        DATA ANALYSIS:
        {analysis_summary}
        
        Suggest 2 SQL queries that would help answer this question.
        For each query, briefly explain what insight it will provide.
        
        Format:
        QUERY 1:
        ```sql
        -- Your SQL query
        ```
        PURPOSE: What this query will show
        
        QUERY 2:
        ```sql
        -- Your SQL query
        ```
        PURPOSE: What this query will show
        """
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a data analyst expert in SQL."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        # Extract and execute queries
        query_pattern = r"QUERY \d+:\s*```(?:sql)?\s*([\s\S]*?)```\s*PURPOSE:\s*([\s\S]*?)(?=QUERY \d+:|$)"
        executed_queries = []
        
        for match in re.finditer(query_pattern, response.choices[0].message.content):
            sql_query = match.group(1).strip()
            purpose = match.group(2).strip()
            
            query_info = {"query": sql_query, "purpose": purpose}
            
            try:
                result = db_instance.query(sql_query)
                query_info["result"] = result
                query_info["success"] = True
                executed_queries.append(query_info)
            except Exception as e:
                query_info["error"] = str(e)
                query_info["success"] = False
        
        # Generate insights from results
        all_query_results = ""
        for i, query_info in enumerate(executed_queries):
            all_query_results += f"\nQUERY {i+1}: {query_info['query']}\n"
            all_query_results += f"PURPOSE: {query_info.get('purpose', 'N/A')}\n"
            
            if query_info.get('success', False) and isinstance(query_info.get('result'), pd.DataFrame):
                df = query_info['result']
                if not df.empty:
                    all_query_results += f"RESULTS:\n{df.head(10).to_string()}\n"
                else:
                    all_query_results += "RESULTS: No rows returned\n"
            else:
                all_query_results += f"QUERY ERROR: {query_info.get('error', 'Unknown error')}\n"
        
        # Generate insights
        insight_prompt = f"""
        Based on these database query results, provide 3-5 key insights that answer this question:
        
        QUESTION: {natural_language_query}
        
        QUERY RESULTS:
        {all_query_results}
        
        Format your response as a concise report with:
        1. A brief summary (1-2 sentences)
        2. Bullet points of key findings with specific data points
        3. A short conclusion
        """
        
        insight_response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a data analyst providing clear insights."},
                {"role": "user", "content": insight_prompt}
            ],
            temperature=0.1
        )
        
        # Final report
        final_report = f"# Data Insight Report\n\n"
        final_report += f"## Question\n\n{natural_language_query}\n\n"
        final_report += f"{insight_response.choices[0].message.content}\n\n"
        
        # Add supporting data
        if executed_queries:
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
        
        chunks.append(Chunk(path=f"database://{db_type}/insights", texts=[final_report]))
        return chunks
        
    except Exception as e:
        chunks.append(Chunk(path=f"database://{db_type}/error", 
                          texts=[f"Error generating insights: {str(e)}"]
                         ))
        return chunks