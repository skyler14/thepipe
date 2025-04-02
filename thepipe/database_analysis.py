"""
Database analysis utilities for natural language processing and query generation.
This module contains functions for analyzing databases, generating SQL queries from
natural language, and creating specialized prompts for different query intents.
"""

from typing import Dict, List, Any, Optional, Union
import pandas as pd
import re
from .core import Chunk

def get_table_name(db_instance, db_type: str = None, verbose: bool = False) -> str:
    """
    Determine the appropriate table or view name to use with a database connection.
    
    This function uses jupysql metadata API and fallback methods to identify 
    available tables in the database.
    
    Args:
        db_instance: Database connection instance
        db_type: Optional database type hint
        verbose: Enable verbose logging
        
    Returns:
        String containing the appropriate table/view name
    """
    if verbose:
        print(f"[thepipe] Getting table name for db_type: {db_type}")
    
    # Try the jupysql metadata approach first
    try:
        if hasattr(db_instance, 'tables'):
            # Get list of tables from jupysql metadata API
            tables = db_instance.tables()
            if isinstance(tables, pd.DataFrame) and not tables.empty:
                table_name = tables.iloc[0, 0]  # First table name
                if verbose:
                    print(f"[thepipe] Found table via jupysql metadata: {table_name}")
                return table_name
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error getting tables via metadata API: {str(e)}")
    
    # Direct query approach for standard databases
    try:
        # Try SQLite-style metadata
        tables_df = db_instance.query("SELECT name FROM sqlite_master WHERE type='table' OR type='view'")
        if not tables_df.empty:
            table_name = tables_df['name'].iloc[0]
            if verbose:
                print(f"[thepipe] Found table via SQLite metadata: {table_name}")
            return table_name
    except Exception:
        if verbose:
            print("[thepipe] SQLite metadata approach failed")
    
    # Try standard information_schema approach
    try:
        tables_df = db_instance.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        if not tables_df.empty:
            table_name = tables_df['table_name'].iloc[0]
            if verbose:
                print(f"[thepipe] Found table via information_schema: {table_name}")
            return table_name
    except Exception:
        if verbose:
            print("[thepipe] information_schema approach failed")
    
    # Default view names based on db_type
    if db_type:
        default_views = {
            "parquet": "parquet_data",
            "csv": "csv_data",
            "excel": "excel_data"
        }
        
        if db_type in default_views:
            view_name = default_views[db_type]
            # Verify the view exists by trying to query it
            try:
                db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
                if verbose:
                    print(f"[thepipe] Using default view for {db_type}: {view_name}")
                return view_name
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error verifying default view {view_name}: {str(e)}")
    
    # Final fallback - try the specific known views
    for view_name in ["parquet_data", "csv_data", "excel_data", "data"]:
        try:
            db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
            if verbose:
                print(f"[thepipe] Found working fallback view: {view_name}")
            return view_name
        except Exception:
            pass
    
    # If we get here, we couldn't determine a table name
    raise ValueError("Could not determine database table name. Please provide a table name explicitly.")

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

def format_analysis_for_llm(analysis: Dict[str, Any]) -> str:
    """Format analysis results for LLM consumption."""
    analysis_text = ""
    
    if 'error' in analysis:
        analysis_text += f"Error in analysis: {analysis['error']}\n\n"
        return analysis_text
        
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
            analysis_text += f"\nCategorical columns ({len(cat_cols)}): {', '.join(cat_cols[:10])}"
            if len(cat_cols) > 10:
                analysis_text += f" and {len(cat_cols) - 10} more"
            analysis_text += "\n"
            
        if num_cols:
            analysis_text += f"\nNumeric columns ({len(num_cols)}): {', '.join(num_cols[:10])}"
            if len(num_cols) > 10:
                analysis_text += f" and {len(num_cols) - 10} more"
            analysis_text += "\n"
    
    # Add key value distributions
    if 'column_stats' in analysis:
        analysis_text += "\nKey column statistics:\n"
        
        # Categorical columns
        cat_shown = 0
        for col, stats in analysis['column_stats'].items():
            if stats['type'] == 'categorical' and cat_shown < 3:
                cat_shown += 1
                analysis_text += f"\n{col} (categorical):\n"
                for val in stats['top_values'][:5]:
                    analysis_text += f"- {val['value']}: {val['count']} rows ({val['percentage']:.1f}%)\n"
        
        # Numeric columns
        num_shown = 0
        for col, stats in analysis['column_stats'].items():
            if stats['type'] == 'numeric' and num_shown < 3:
                num_shown += 1
                analysis_text += f"\n{col} (numeric):\n"
                stat_data = stats['stats']
                analysis_text += f"- Range: {stat_data['min']} to {stat_data['max']}\n"
                analysis_text += f"- Mean: {stat_data['mean']}\n"
                analysis_text += f"- Null count: {stat_data['null_count']}\n"
    
    # Add any potential keys or date columns
    if 'potential_keys' in analysis and analysis['potential_keys']:
        analysis_text += f"\nPotential key columns: {', '.join(analysis['potential_keys'])}\n"
        
    if 'date_columns' in analysis and analysis['date_columns']:
        analysis_text += f"\nPossible date/time columns: {', '.join(analysis['date_columns'])}\n"
        
    return analysis_text

def get_auto_analysis(db_instance, db_type: str = None, view_name: str = None, max_samples: int = 5, verbose: bool = False) -> Dict[str, Any]:
    """
    Automatically analyze the database to generate useful insights.
    
    This helps provide better context to both the LLM and fallback mechanisms
    by extracting distribution statistics and value examples.
    
    Args:
        db_instance: Database connection instance
        db_type: Database type
        view_name: Optional database view/table name (will be auto-detected if not provided)
        max_samples: Maximum number of sample values to collect per column
        verbose: Enable verbose logging
        
    Returns:
        Dict containing column statistics and common values
    """
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
        
        # Get sample data
        sample_df = db_instance.query(f"SELECT * FROM {view_name} LIMIT 1")
        columns = sample_df.columns.tolist()
        analysis['columns'] = columns
        
        # Generate statistics for each column
        column_stats = {}
        categorical_columns = []
        numeric_columns = []
        
        # First pass - identify column types
        for col in columns:
            try:
                # Check if column is numeric
                type_check = db_instance.query(f"""
                    SELECT 
                        typeof("{col}") as data_type
                    FROM {view_name}
                    WHERE "{col}" IS NOT NULL
                    LIMIT 1
                """)
                
                if len(type_check) > 0:
                    data_type = type_check['data_type'].iloc[0].lower()
                    if data_type in ('integer', 'real', 'double', 'float', 'decimal', 'numeric'):
                        numeric_columns.append(col)
                    else:
                        # Get distinct count for potential categorical columns
                        distinct_count = db_instance.query(f"""
                            SELECT COUNT(DISTINCT "{col}") as count 
                            FROM {view_name}
                            WHERE "{col}" IS NOT NULL
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
        
        # Analyze categorical columns - get value distributions
        for col in categorical_columns[:10]:  # Limit to first 10 to avoid excessive queries
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
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error analyzing categorical column {col}: {str(e)}")
        
        # Analyze numeric columns
        for col in numeric_columns[:10]:  # Limit to first 10
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
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error analyzing numeric column {col}: {str(e)}")
        
        analysis['column_stats'] = column_stats
        
        # Generate suggested queries based on the data
        suggested_queries = []
        
        # Find candidate primary key columns (check for uniqueness)
        for col in columns:
            try:
                unique_check = db_instance.query(f"""
                    SELECT COUNT(DISTINCT "{col}") as unique_count
                    FROM {view_name}
                """)
                
                if unique_check['unique_count'].iloc[0] == total_rows:
                    analysis['potential_keys'] = analysis.get('potential_keys', []) + [col]
            except Exception:
                pass
        
        # Find date/time columns
        date_columns = []
        for col in columns:
            if any(date_term in col.lower() for date_term in ['date', 'time', 'year', 'month', 'day']):
                date_columns.append(col)
        
        analysis['date_columns'] = date_columns
        
        # Generate potential interesting queries
        if categorical_columns:
            target_col = categorical_columns[0]
            suggested_queries.append({
                'description': f'Distribution of {target_col}',
                'query': f'SELECT "{target_col}", COUNT(*) as count FROM {view_name} GROUP BY "{target_col}" ORDER BY count DESC LIMIT 10'
            })
        
        if len(categorical_columns) > 1:
            col1, col2 = categorical_columns[0], categorical_columns[1]
            suggested_queries.append({
                'description': f'Cross-tabulation of {col1} and {col2}',
                'query': f'SELECT "{col1}", "{col2}", COUNT(*) as count FROM {view_name} GROUP BY "{col1}", "{col2}" ORDER BY count DESC LIMIT 15'
            })
        
        if date_columns and categorical_columns:
            date_col = date_columns[0]
            cat_col = categorical_columns[0]
            suggested_queries.append({
                'description': f'Trends over time by {date_col}',
                'query': f'SELECT "{date_col}", "{cat_col}", COUNT(*) as count FROM {view_name} GROUP BY "{date_col}", "{cat_col}" ORDER BY "{date_col}"'
            })
        
        analysis['suggested_queries'] = suggested_queries
        
        return analysis
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error performing auto-analysis: {str(e)}")
            import traceback
            traceback.print_exc()
        return {'error': str(e)}

def get_specialized_sql_examples(query_intent: str, view_name: str, 
                               text_columns: List[str], numeric_columns: List[str],
                               date_columns: List[str], categorical_columns: List[str]) -> str:
    """Generate specialized SQL examples based on query intent and available columns."""
    
    # Default to first column of appropriate type, or fallback to safer options
    text_col = text_columns[0] if text_columns else "text_column"
    num_col = numeric_columns[0] if numeric_columns else "numeric_column"
    date_col = date_columns[0] if date_columns else "date_column"
    cat_col = categorical_columns[0] if categorical_columns else "category_column"
    
    # Specialized examples based on query intent
    if query_intent == "text_analysis":
        return f"""
        # EXAMPLES FOR TEXT ANALYSIS IN DUCKDB
        
        # Word frequency analysis:
        SELECT 
            word, 
            COUNT(*) as frequency
        FROM (
            SELECT unnest(regexp_split_to_array(lower("{text_col}"), '\\\\s+')) as word
            FROM {view_name}
            WHERE "{text_col}" IS NOT NULL
        ) t
        WHERE length(word) > 3  -- Skip small words
        AND word NOT IN ('and', 'the', 'for', 'with', 'that', 'this')  -- Skip common words
        GROUP BY word
        ORDER BY frequency DESC
        LIMIT 50;
        
        # Finding specific terms:
        SELECT 
            "{text_col}", 
            COUNT(*) as count
        FROM {view_name}
        WHERE lower("{text_col}") LIKE '%specific_term%'
        GROUP BY "{text_col}"
        ORDER BY count DESC
        LIMIT 20;
        
        # Extracting patterns using regex:
        SELECT 
            regexp_matches("{text_col}", '[A-Z][a-z]+', 'g') as extracted_terms,
            COUNT(*) as frequency
        FROM {view_name}
        WHERE "{text_col}" IS NOT NULL
        GROUP BY extracted_terms
        ORDER BY frequency DESC
        LIMIT 50;
        
        # IMPORTANT NOTES:
        # 1. DO NOT use 'r' prefix for regex patterns in SQL (r'pattern' is Python syntax)
        # 2. Double escape backslashes in regex patterns: '\\\\b' not '\b'
        # 3. Use regexp_split_to_array for tokenizing text into words
        # 4. Always convert to lowercase when doing case-insensitive analysis
        """
    
    elif query_intent == "numeric_analysis":
        return f"""
        # EXAMPLES FOR NUMERIC ANALYSIS IN DUCKDB
        
        # Basic statistics:
        SELECT 
            COUNT(*) as count,
            MIN("{num_col}") as minimum,
            MAX("{num_col}") as maximum,
            AVG("{num_col}") as average,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "{num_col}") as median,
            STDDEV("{num_col}") as standard_deviation
        FROM {view_name}
        WHERE "{num_col}" IS NOT NULL;
        
        # Value distribution into buckets:
        SELECT 
            FLOOR("{num_col}" / 10) * 10 as bucket_start,
            COUNT(*) as count
        FROM {view_name}
        WHERE "{num_col}" IS NOT NULL
        GROUP BY bucket_start
        ORDER BY bucket_start;
        
        # Top values with their frequencies:
        SELECT 
            "{num_col}" as value,
            COUNT(*) as frequency,
            COUNT(*) * 100.0 / (SELECT COUNT(*) FROM {view_name} WHERE "{num_col}" IS NOT NULL) as percentage
        FROM {view_name}
        WHERE "{num_col}" IS NOT NULL
        GROUP BY value
        ORDER BY frequency DESC
        LIMIT 20;
        
        # IMPORTANT NOTES:
        # 1. Handle NULL values explicitly with IS NULL/IS NOT NULL checks
        # 2. Use CAST or :: when type conversion is needed
        # 3. Consider bucketing or binning for continuous numeric values
        # 4. Use appropriate aggregate functions (SUM, AVG, MIN, MAX, etc.)
        """
    
    elif query_intent == "time_analysis":
        return f"""
        # EXAMPLES FOR TIME-BASED ANALYSIS IN DUCKDB
        
        # Trend analysis by month:
        SELECT 
            DATE_TRUNC('month', "{date_col}") as month,
            COUNT(*) as count
        FROM {view_name}
        WHERE "{date_col}" IS NOT NULL
        GROUP BY month
        ORDER BY month;
        
        # Aggregation by year:
        SELECT 
            EXTRACT(YEAR FROM "{date_col}") as year,
            COUNT(*) as count,
            AVG(numeric_column) as average_value
        FROM {view_name}
        WHERE "{date_col}" IS NOT NULL
        GROUP BY year
        ORDER BY year;
        
        # Day of week distribution:
        SELECT 
            DAYNAME("{date_col}") as day_of_week,
            COUNT(*) as count
        FROM {view_name}
        WHERE "{date_col}" IS NOT NULL
        GROUP BY day_of_week
        ORDER BY CASE day_of_week
            WHEN 'Monday' THEN 1
            WHEN 'Tuesday' THEN 2
            WHEN 'Wednesday' THEN 3
            WHEN 'Thursday' THEN 4
            WHEN 'Friday' THEN 5
            WHEN 'Saturday' THEN 6
            WHEN 'Sunday' THEN 7
        END;
        
        # IMPORTANT NOTES:
        # 1. Use DATE_TRUNC for aggregating by time periods
        # 2. Use EXTRACT to get specific components (year, month, day)
        # 3. Pay attention to timezone handling if relevant
        # 4. Consider using window functions for cumulative calculations
        """
    
    elif query_intent == "categorization":
        return f"""
        # EXAMPLES FOR CATEGORIZATION ANALYSIS IN DUCKDB
        
        # Basic category distribution:
        SELECT 
            "{cat_col}" as category,
            COUNT(*) as count,
            CAST(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM {view_name}) AS DECIMAL(5,2)) as percentage
        FROM {view_name}
        WHERE "{cat_col}" IS NOT NULL
        GROUP BY category
        ORDER BY count DESC;
        
        # Cross-tabulation of two categories:
        SELECT 
            "{cat_col}" as category1,
            secondary_category_column as category2,
            COUNT(*) as count
        FROM {view_name}
        WHERE "{cat_col}" IS NOT NULL AND secondary_category_column IS NOT NULL
        GROUP BY category1, category2
        ORDER BY count DESC
        LIMIT 50;
        
        # Category with statistics:
        SELECT 
            "{cat_col}" as category,
            COUNT(*) as count,
            AVG(numeric_column) as average,
            MIN(numeric_column) as minimum,
            MAX(numeric_column) as maximum
        FROM {view_name}
        WHERE "{cat_col}" IS NOT NULL
        GROUP BY category
        ORDER BY count DESC;
        
        # IMPORTANT NOTES:
        # 1. Always include NULL handling with IS NULL/IS NOT NULL
        # 2. Consider limiting results for categories with many unique values
        # 3. Use ORDER BY to sort results meaningfully
        # 4. Calculate percentages for better context
        """
    
    else:
        # General examples as fallback
        return f"""
        # GENERAL SQL EXAMPLES IN DUCKDB
        
        # Basic query:
        SELECT * FROM {view_name} LIMIT 20;
        
        # Counting and grouping:
        SELECT column_name, COUNT(*) as count
        FROM {view_name}
        GROUP BY column_name
        ORDER BY count DESC
        LIMIT 20;
        
        # Filtering data:
        SELECT *
        FROM {view_name}
        WHERE condition_column > value
        LIMIT 50;
        
        # IMPORTANT NOTES:
        # 1. Always include LIMIT to avoid returning too many rows
        # 2. Handle NULL values explicitly with IS NULL/IS NOT NULL
        # 3. Use appropriate aggregate functions when needed
        # 4. Double quote column names that might be reserved words
        """

def fix_sql_syntax(sql_query: str, query_intent: str) -> str:
    """Fix common SQL syntax errors in generated queries based on query intent."""
    
    # Fix Python-style regex prefix
    if query_intent in ["text_analysis"]:
        # Replace Python r-prefixed regex patterns
        sql_query = re.sub(r"r'(.*?)'", r"'\1'", sql_query)
        sql_query = re.sub(r'r"(.*?)"', r'"\1"', sql_query)
        
        # Fix backslash escaping in regex
        sql_query = sql_query.replace(r'\b', r'\\b')
        sql_query = sql_query.replace(r'\w', r'\\w')
        sql_query = sql_query.replace(r'\s', r'\\s')
        sql_query = sql_query.replace(r'\d', r'\\d')
        
        # Replace common function name mistakes
        sql_query = sql_query.replace("REGEXP_EXTRACT", "regexp_matches")
        
        # Check for missing word tokenization in text analysis
        if "word frequency" in query_intent.lower() and "unnest" not in sql_query.lower():
            # This is a text analysis query missing proper tokenization
            pass
    
    # Fix common date function issues
    if query_intent == "time_analysis":
        # DuckDB uses extract() not EXTRACT()
        sql_query = sql_query.replace("EXTRACT(", "extract(")
        
    # Fix common casting issues
    sql_query = re.sub(r"CAST\((.*?) AS ([A-Za-z]+)\)", r"CAST(\1 AS \2)", sql_query, flags=re.IGNORECASE)
    
    return sql_query

def execute_intent_based_fallback(chunks: List[Chunk], query: str,
                                intent: str, db_instance, 
                                text_columns: List[str], numeric_columns: List[str],
                                date_columns: List[str], categorical_columns: List[str],
                                db_type: str = "unknown", view_name: str = None,
                                error: Optional[str] = None,
                                failed_query: Optional[str] = None,
                                verbose: bool = False) -> List[Chunk]:
    """
    Execute an intent-based fallback query based on query intent and available columns.
    
    Args:
        chunks: Existing chunks (including schema)
        query: Original natural language query
        intent: Detected query intent
        db_instance: Database instance for executing queries
        text_columns: Available text columns
        numeric_columns: Available numeric columns
        date_columns: Available date columns
        categorical_columns: Available categorical columns
        db_type: Database type
        view_name: Optional database view/table name (will be auto-detected if not provided)
        error: Optional error message
        failed_query: The query that failed
        verbose: Enable verbose logging
        
    Returns:
        List of Chunk objects with fallback results
    """
    result_text = f"## Natural Language Query\n\n{query}\n\n"
    
    if error and failed_query:
        result_text += f"## Error in Generated Query\n\n```sql\n{failed_query}\n```\n\n"
        result_text += f"Error: {error}\n\n"
    
    result_text += f"## Fallback Analysis\n\n"
    
    try:
        # Auto-detect view_name if not provided
        if view_name is None:
            view_name = get_table_name(db_instance, db_type, verbose)
            
        if verbose:
            print(f"[thepipe] Executing fallback for table: {view_name}")
            
        # Choose an appropriate fallback query based on query intent
        fallback_query = None
        fallback_desc = "Fallback analysis"
        
        # Intent-specific fallback queries
        if intent == "text_analysis" and text_columns:
            target_col = text_columns[0]
            fallback_query = f"""
            WITH words AS (
                SELECT unnest(regexp_split_to_array(LOWER("{target_col}"), '\\\\s+')) as word
                FROM {view_name}
                WHERE "{target_col}" IS NOT NULL
            )
            SELECT 
                word, 
                COUNT(*) as frequency
            FROM words
            WHERE length(word) > 3
            AND word NOT IN ('and', 'the', 'for', 'with', 'that', 'this', 'have', 'from')
            GROUP BY word
            ORDER BY frequency DESC
            LIMIT 50
            """
            fallback_desc = f"Word frequency analysis in {target_col}"
                        
        elif intent == "numeric_analysis" and numeric_columns:
            target_col = numeric_columns[0]
            
            fallback_query = f"""
            SELECT 
                MIN("{target_col}") as minimum,
                MAX("{target_col}") as maximum,
                AVG("{target_col}") as average,
                STDDEV("{target_col}") as standard_deviation,
                COUNT(*) as count,
                COUNT(*) - COUNT("{target_col}") as null_count
            FROM {view_name}
            """
            fallback_desc = f"Statistical analysis of {target_col}"
            
        elif intent == "time_analysis" and date_columns:
            target_col = date_columns[0]
            
            fallback_query = f"""
            SELECT 
                date_trunc('month', "{target_col}") as month,
                COUNT(*) as count
            FROM {view_name}
            WHERE "{target_col}" IS NOT NULL
            GROUP BY month
            ORDER BY month
            LIMIT 24
            """
            fallback_desc = f"Monthly trend analysis of {target_col}"
            
        elif intent == "categorization" and categorical_columns:
            target_col = categorical_columns[0]
            
            fallback_query = f"""
            SELECT 
                "{target_col}",
                COUNT(*) as count,
                CAST(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM {view_name}) AS DECIMAL(5,2)) as percentage
            FROM {view_name}
            WHERE "{target_col}" IS NOT NULL
            GROUP BY "{target_col}"
            ORDER BY count DESC
            LIMIT 30
            """
            fallback_desc = f"Distribution analysis of {target_col}"
            
        else:
            # General fallback for other intents or when no specific columns available
            priority_columns = []
            
            # Try to find a column mentioned in the query
            query_lower = query.lower()
            for col in categorical_columns + text_columns + numeric_columns + date_columns:
                if col.lower() in query_lower:
                    priority_columns.append(col)
            
            if priority_columns:
                target_col = priority_columns[0]
                fallback_query = f"""
                SELECT "{target_col}", COUNT(*) as count
                FROM {view_name}
                WHERE "{target_col}" IS NOT NULL
                GROUP BY "{target_col}"
                ORDER BY count DESC
                LIMIT 30
                """
                fallback_desc = f"Distribution of {target_col} values"
            else:
                # Last resort - show sample rows
                fallback_query = f"""
                SELECT *
                FROM {view_name}
                LIMIT 20
                """
                fallback_desc = "Sample of data rows"
        
        # Execute the fallback query
        if fallback_query and db_instance:
            if verbose:
                print(f"[thepipe] Executing fallback query: {fallback_query}")
                print(f"[thepipe] Fallback description: {fallback_desc}")
                
            result = db_instance.query(fallback_query)
            
            result_text += f"*{fallback_desc}*\n\n"
            result_text += f"```sql\n{fallback_query}\n```\n\n"
            
            if isinstance(result, pd.DataFrame) and not result.empty:
                result_text += f"## Results ({len(result)} rows)\n\n"
                result_text += "```json\n"
                result_text += result.to_json(orient='records', indent=2)
                result_text += "\n```"
            else:
                result_text += "*No results found.*"
        else:
            result_text += "*No suitable fallback query could be generated for this question.*"
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error executing fallback query: {str(e)}")
            import traceback
            traceback.print_exc()
        
        result_text += f"*Error executing fallback query: {str(e)}*"
    
    chunks.append(Chunk(
        path=f"database://{db_type}/fallback",
        texts=[result_text]
    ))
    
    return chunks

def provide_analysis_results(chunks: List[Chunk], query: str, analysis: Dict[str, Any], 
                           db_instance, db_type: str = "unknown", 
                           view_name: str = None, error: Optional[str] = None, 
                           verbose: bool = False) -> List[Chunk]:
    """
    Provide analysis results when SQL generation fails.
    
    Args:
        chunks: Existing chunks (including schema)
        query: Original natural language query
        analysis: Data analysis results
        db_instance: Database instance for executing queries
        db_type: Database type
        view_name: Optional database view/table name (will be auto-detected if not provided)
        error: Optional error message to include
        verbose: Enable verbose logging
        
    Returns:
        List of Chunk objects with analysis results
    """
    analysis_text = f"## Natural Language Query\n\n{query}\n\n"
    
    if error:
        analysis_text += f"## Error\n\n{error}\n\n"
    
    analysis_text += f"## Automatic Data Analysis\n\n"
    
    # Identify potentially relevant columns based on the query
    query_lower = query.lower()
    relevant_columns = []
    
    # Look for column names mentioned in the query
    if 'column_stats' in analysis:
        for col, stats in analysis['column_stats'].items():
            # Check if column name or related terms appear in query
            if col.lower() in query_lower or any(term in query_lower for term in col.lower().split('_')):
                relevant_columns.append((col, stats))
    
    # If looking for keywords in text/descriptions
    text_columns = []
    if any(term in query_lower for term in ['keyword', 'word', 'text', 'description']):
        for col in analysis.get('columns', []):
            if 'description' in col.lower() or 'text' in col.lower() or 'comment' in col.lower():
                text_columns.append(col)
    
    # Create summaries based on the analysis
    if 'error' in analysis:
        analysis_text += f"Error performing analysis: {analysis['error']}\n"
    else:
        # Add basic dataset stats
        if 'total_rows' in analysis:
            analysis_text += f"Dataset contains {analysis['total_rows']:,} rows.\n\n"
        
        # First, show relevant columns if any were found
        if relevant_columns:
            analysis_text += f"### Relevant Column Information\n\n"
            for col, stats in relevant_columns:
                if stats['type'] == 'categorical':
                    analysis_text += f"#### Distribution of {col}\n\n"
                    analysis_text += "| Value | Count | Percentage |\n"
                    analysis_text += "|-------|-------|------------|\n"
                    
                    for val in stats['top_values']:
                        analysis_text += f"| {val['value']} | {val['count']:,} | {val['percentage']:.1f}% |\n"
                    
                    analysis_text += "\n"
                elif stats['type'] == 'numeric':
                    stat_data = stats['stats']
                    analysis_text += f"#### Summary statistics for {col}\n\n"
                    analysis_text += "| Metric | Value |\n"
                    analysis_text += "|--------|-------|\n"
                    analysis_text += f"| Minimum | {stat_data['min']} |\n"
                    analysis_text += f"| Maximum | {stat_data['max']} |\n"
                    analysis_text += f"| Mean | {stat_data['mean']} |\n"
                    analysis_text += f"| Null count | {stat_data['null_count']} |\n\n"
        
        # Add general categorical column summaries if no relevant columns found
        if not relevant_columns:
            category_counts = 0
            for col, stats in analysis.get('column_stats', {}).items():
                if stats['type'] == 'categorical' and category_counts < 3:
                    category_counts += 1
                    
                    # Add column summary
                    analysis_text += f"### Distribution of {col}\n\n"
                    analysis_text += "| Value | Count | Percentage |\n"
                    analysis_text += "|-------|-------|------------|\n"
                    
                    for val in stats['top_values']:
                        analysis_text += f"| {val['value']} | {val['count']:,} | {val['percentage']:.1f}% |\n"
                    
                    analysis_text += "\n"
    
    # Add the analysis chunk
    chunks.append(Chunk(
        path=f"database://{db_type}/analysis",
        texts=[analysis_text]
    ))
    
    return chunks

def generate_data_insights(db_instance, natural_language_query: str, 
                         db_type: str = None, view_name: str = None,
                         llm_config: Optional[Dict[str, Any]] = None,
                         max_iterations: int = 3, verbose: bool = False) -> List[Chunk]:
    """
    Generate insights from database using an iterative LLM-driven analysis approach.
    
    This method:
    1. Performs initial data analysis
    2. Uses LLM to generate potential queries based on the analysis
    3. Executes those queries to get additional data
    4. Iteratively refines insights based on what's learned
    5. Synthesizes findings into a comprehensive insight
    
    Args:
        db_instance: Database connection instance
        natural_language_query: The user's question or insight request
        db_type: Optional database type
        view_name: Optional database view/table name (will be auto-detected if not provided)
        llm_config: Configuration for the LLM
        max_iterations: Maximum number of analysis cycles
        verbose: Enable verbose logging
        
    Returns:
        List of Chunk objects with insights and supporting data
    """
    # Auto-detect view_name if not provided
    if view_name is None:
        try:
            view_name = get_table_name(db_instance, db_type, verbose)
        except ValueError as e:
            if verbose:
                print(f"[thepipe] Error getting table name: {str(e)}")
            error_chunk = Chunk(
                path=f"database://{db_type or 'unknown'}/error",
                texts=[f"Error determining table name: {str(e)}"]
            )
            return [error_chunk]
            
    if verbose:
        print(f"[thepipe] Generating insights for table: {view_name}")
    
    # Get schema information
    schema_text = ""
    try:
        # Try to get schema information if available
        tables_query = f"SELECT * FROM {view_name} LIMIT 1"
        sample_df = db_instance.query(tables_query)
        
        schema_text = "## Database Schema\n\n"
        schema_text += f"### Table: {view_name}\n\n"
        schema_text += "| Column | Type |\n"
        schema_text += "|--------|------|\n"
        
        for col_name, dtype in sample_df.dtypes.items():
            schema_text += f"| {col_name} | {dtype} |\n"
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error getting schema: {str(e)}")
        schema_text = "## Database Schema\n\nSchema information not available."
    
    schema_chunk = Chunk(
        path=f"database://{db_type}/schema",
        texts=[schema_text]
    )
    
    chunks = [schema_chunk]
    
    if verbose:
        print(f"[thepipe] Generating insights for: '{natural_language_query}'")
        print(f"[thepipe] Using max {max_iterations} iterations")
    
    # Check if LLM configuration is provided
    if not llm_config:
        chunks.append(Chunk(
            path=f"database://{db_type}/error",
            texts=["Insight generation requires LLM configuration. "
                "Please provide LLM configuration via the options parameter."]
        ))
        return chunks
    
    try:
        # Import necessary components
        import os
        from openai import OpenAI
        
        # Set up OpenAI client configuration
        api_key = llm_config.get("api_key", os.environ.get("OPENAI_API_KEY"))
        api_base = llm_config.get("api_base", os.environ.get("OPENAI_API_BASE"))
        model = llm_config.get("model", "gpt-3.5-turbo")
        
        if not api_key:
            chunks.append(Chunk(
                path=f"database://{db_type}/error",
                texts=["API key is required for insight generation. "
                    "Please provide it via llm_config or set OPENAI_API_KEY environment variable."]
            ))
            return chunks
        
        # Create OpenAI client
        client_args = {"api_key": api_key}
        if api_base:
            client_args["base_url"] = api_base
            
        client = OpenAI(**client_args)
        
        # Phase 1: Initial data analysis
        if verbose:
            print(f"[thepipe] Phase 1: Performing initial data analysis")
            
        initial_analysis = get_auto_analysis(
            db_instance=db_instance,
            db_type=db_type,
            view_name=view_name,
            verbose=verbose
        )
        
        # Track our findings and queries across iterations
        findings = []
        executed_queries = []
        failed_queries = []
        
        # Track data we've already seen to avoid redundant analysis
        analyzed_columns = set()
        
        # Phase 2: Iterative analysis
        for iteration in range(max_iterations):
            if verbose:
                print(f"[thepipe] Phase 2: Starting iteration {iteration+1}/{max_iterations}")
            
            # Compile what we know so far
            analysis_summary = format_analysis_for_llm(initial_analysis)
            findings_summary = "\n".join([f"- {finding}" for finding in findings])
            
            # Information about executed queries
            queries_summary = ""
            for i, query_info in enumerate(executed_queries):
                results_summary = ""
                if isinstance(query_info.get('result'), pd.DataFrame):
                    df = query_info['result']
                    if not df.empty:
                        row_summary = min(5, len(df))
                        results_summary = f"Results (showing {row_summary} of {len(df)} rows):\n{df.head(row_summary).to_string()}"
                
                queries_summary += f"\nQuery {i+1}: {query_info['query']}\n{results_summary}\n"
            
            # Create prompt for the LLM - asking for specific insights
            prompt = f"""
            You are a data analyst examining a database. Based on existing analysis and the user's question, 
            suggest new SQL queries that would reveal additional insights.

            USER QUESTION: {natural_language_query}
            
            DATABASE SCHEMA:
            {schema_chunk.texts[0]}
            
            INITIAL ANALYSIS:
            {analysis_summary}
            
            CURRENT FINDINGS:
            {findings_summary if findings else "No findings yet."}
            
            QUERIES ALREADY EXECUTED:
            {queries_summary if executed_queries else "No queries executed yet."}
            
            ITERATION: {iteration+1} of {max_iterations}
            
            Suggest 1-3 new SQL queries that would help answer the user's question or uncover new insights.
            For each query, explain what insight you expect it to provide. Be specific and creative.
            
            Format your response as follows:
            
            QUERY 1:
            ```sql
            -- Your SQL query here
            ```
            EXPECTED INSIGHT: What you expect to learn from this query
            
            QUERY 2:
            ```sql
            -- Another SQL query here
            ```
            EXPECTED INSIGHT: What you expect to learn from this query
            
            (repeat as needed)
            """
            
            if verbose:
                print(f"[thepipe] Generating analysis queries via LLM")
            
            # Get query suggestions from LLM
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a data analyst expert in SQL and data exploration."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3  # Some creativity needed for exploration
                )
                
                llm_response = response.choices[0].message.content.strip()
                
                if verbose:
                    print(f"[thepipe] Generated query suggestions")
                    
                # Extract SQL queries and expected insights
                query_pattern = r"QUERY\s+\d+:\s*```(?:sql)?\s*([\s\S]*?)```\s*EXPECTED INSIGHT:\s*([\s\S]*?)(?=QUERY|\Z)"
                query_matches = re.finditer(query_pattern, llm_response)
                
                # Execute each suggested query
                for match in query_matches:
                    sql_query = match.group(1).strip()
                    expected_insight = match.group(2).strip()
                    
                    query_info = {
                        "query": sql_query,
                        "expected_insight": expected_insight
                    }
                    
                    if verbose:
                        print(f"[thepipe] Executing query: {sql_query}")
                        print(f"[thepipe] Expected insight: {expected_insight}")
                    
                    try:
                        result = db_instance.query(sql_query)
                        query_info["result"] = result
                        query_info["success"] = True
                        executed_queries.append(query_info)
                        
                        if verbose:
                            print(f"[thepipe] Query returned {len(result) if isinstance(result, pd.DataFrame) else 'N/A'} rows")
                    except Exception as query_error:
                        query_info["error"] = str(query_error)
                        query_info["success"] = False
                        failed_queries.append(query_info)
                        
                        if verbose:
                            print(f"[thepipe] Query execution failed: {str(query_error)}")
                
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error generating queries: {str(e)}")
                # Continue to next phase with the data we have
            
            # Phase 3: Insight generation based on executed queries
            if verbose:
                print(f"[thepipe] Generating insights from query results")
            
            # Create a summary of all executed queries and their results
            all_query_results = ""
            for i, query_info in enumerate(executed_queries):
                all_query_results += f"\nQUERY {i+1}: {query_info['query']}\n"
                all_query_results += f"EXPECTED INSIGHT: {query_info.get('expected_insight', 'N/A')}\n"
                
                if query_info.get('success', False) and isinstance(query_info.get('result'), pd.DataFrame):
                    df = query_info['result']
                    if not df.empty:
                        all_query_results += f"RESULTS (showing up to 10 rows of {len(df)}):\n{df.head(10).to_string()}\n"
                    else:
                        all_query_results += "RESULTS: No rows returned\n"
                else:
                    all_query_results += f"QUERY ERROR: {query_info.get('error', 'Unknown error')}\n"
            
            # Generate insights from the current results
            insight_prompt = f"""
            You are a data analyst interpreting results from a database exploration.
            Based on the following query results and analysis, generate insights about the data 
            that help answer the user's question.

            USER QUESTION: {natural_language_query}
            
            DATABASE INFO:
            {schema_chunk.texts[0]}
            
            ANALYSIS SUMMARY:
            {analysis_summary}
            
            QUERY RESULTS:
            {all_query_results}
            
            CURRENT FINDINGS:
            {findings_summary if findings else "No findings yet."}
            
            ITERATION: {iteration+1} of {max_iterations}
            
            Generate 1-3 clear, specific insights based on the available data. Focus on facts supported by the data,
            not speculation. Consider patterns, anomalies, distributions, and relationships between variables.
            
            Format each insight as a concise bullet point. Be precise and include actual values/numbers
            from the data when relevant.
            """
            
            try:
                insight_response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a data analyst providing clear, specific, data-driven insights."},
                        {"role": "user", "content": insight_prompt}
                    ],
                    temperature=0.1  # Low temperature for factual insights
                )
                
                insight_text = insight_response.choices[0].message.content.strip()
                
                # Extract insights
                for line in insight_text.split('\n'):
                    line = line.strip()
                    if line.startswith('- ') or line.startswith('* '):
                        insight = line[2:].strip()
                        if insight and insight not in findings:
                            findings.append(insight)
                            
                if verbose:
                    print(f"[thepipe] Generated {len(findings)} total insights so far")
                
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error generating insights: {str(e)}")
        
        # Phase 4: Final synthesis and report generation
        if verbose:
            print(f"[thepipe] Phase 4: Generating final insight report")
        
        # Summarize all findings and create a comprehensive report
        all_results_summary = ""
        for i, query_info in enumerate(executed_queries):
            if query_info.get('success', False) and isinstance(query_info.get('result'), pd.DataFrame):
                df = query_info['result']
                if not df.empty and len(df) <= 15:  # Only include small result sets
                    all_results_summary += f"\n### Query {i+1} Results:\n"
                    all_results_summary += f"```sql\n{query_info['query']}\n```\n\n"
                    all_results_summary += f"```\n{df.to_string()}\n```\n\n"
        
        synthesis_prompt = f"""
        You are a data analyst creating a final insight report. Synthesize all findings into
        a clear, concise report that directly answers the user's question.
        
        USER QUESTION: {natural_language_query}
        
        DATABASE INFO:
        {schema_chunk.texts[0]}
        
        ALL FINDINGS:
        {findings_summary if findings else "No significant findings identified."}
        
        Create a comprehensive insight report with these sections:
        1. Summary (direct answer to the user's question in 1-2 sentences)
        2. Key Insights (3-5 most important findings with supporting evidence)
        3. Details (additional context and nuance)
        
        Format your response in Markdown. Be concise, data-driven, and focus on the most relevant information.
        """
        
        try:
            report_response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a data analyst creating clear, data-driven reports."},
                    {"role": "user", "content": synthesis_prompt}
                ],
                temperature=0.2  # Low temperature for factual reports
            )
            
            report_text = report_response.choices[0].message.content.strip()
            
            # Create the final insight report
            final_report = f"# Data Insight Report\n\n"
            final_report += f"## Question\n\n{natural_language_query}\n\n"
            final_report += f"{report_text}\n\n"
            
            if all_results_summary:
                final_report += f"## Supporting Data\n\n{all_results_summary}\n\n"
            
            # Add query execution summary
            final_report += f"## Analysis Details\n\n"
            final_report += f"- Database type: {db_type}\n"
            final_report += f"- Rows analyzed: {initial_analysis.get('total_rows', 'unknown')}\n"
            final_report += f"- Queries executed: {len(executed_queries)}\n"
            final_report += f"- Analysis iterations: {max_iterations}\n"
            
            chunks.append(Chunk(
                path=f"database://{db_type}/insights",
                texts=[final_report]
            ))
            
            if verbose:
                print(f"[thepipe] Insight report generation complete")
            
            return chunks
            
        except Exception as e:
            if verbose:
                print(f"[thepipe] Error generating final report: {str(e)}")
            
            # Fallback to raw findings
            if findings:
                report = f"# Data Insights\n\n"
                report += f"## Question\n\n{natural_language_query}\n\n"
                report += "## Key Findings\n\n"
                
                for finding in findings:
                    report += f"- {finding}\n"
                
                if all_results_summary:
                    report += f"\n## Supporting Data\n\n{all_results_summary}\n"
                
                chunks.append(Chunk(
                    path=f"database://{db_type}/insights",
                    texts=[report]
                ))
            else:
                # Fall back to basic analysis if no findings
                return provide_analysis_results(
                    chunks=chunks, 
                    query=natural_language_query, 
                    analysis=initial_analysis,
                    db_instance=db_instance,
                    db_type=db_type,
                    verbose=verbose
                )
                
            return chunks
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error generating insights: {str(e)}")
            import traceback
            traceback.print_exc()
        
        chunks.append(Chunk(
            path=f"database://{db_type}/error",
            texts=[f"Error generating insights: {str(e)}"]
        ))
        
        return chunks