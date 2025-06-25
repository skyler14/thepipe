"""
JupySQL Middleware Module for thepipe
-------------------------------------

This module provides a unified interface to database systems through JupySQL.
It simplifies database interactions by providing a consistent API that works
across different database technologies and connection types.
"""

from typing import Union, Optional, Any, Dict, List
import pandas as pd
import os
import importlib.util
import warnings

# Check if JupySQL is installed
JUPYSQL_AVAILABLE = importlib.util.find_spec("sql") is not None

# Suppress warnings from JupySQL
warnings.filterwarnings("ignore", module="sql")


class Database:
    """A simplified interface to JupySQL's database capabilities"""
    
    def __init__(self, connection_source: Union[str, Any], config_dict: Dict[str, Any] = None):
        """
        Initialize the database connection
        
        Parameters:
        -----------
        connection_source : str or connection object
            Either a connection string or an existing DB-API or SQLAlchemy connection
            
        config_dict : dict, optional
            Configuration options to customize behavior
        """
        global JUPYSQL_AVAILABLE
        if not JUPYSQL_AVAILABLE:
            # Attempt to install JupySQL if not available
            try:
                import subprocess
                subprocess.check_call(["pip", "install", "jupysql", "--quiet"])
                JUPYSQL_AVAILABLE = True
            except:
                raise ImportError(
                    "JupySQL is required but not installed. Please install it with: pip install jupysql"
                )
        
        # Import JupySQL components now that we know it's available
        from sql.connection import ConnectionManager, SQLAlchemyConnection, DBAPIConnection
        from sql.run.run import run_statements
        from sql.run.resultset import ResultSet
        
        self.ConnectionManager = ConnectionManager
        self.SQLAlchemyConnection = SQLAlchemyConnection
        self.DBAPIConnection = DBAPIConnection
        self.run_statements = run_statements
        self.ResultSet = ResultSet
        
        # Create config class instance
        self.config = self._create_config(config_dict)
        
        # Handle different connection types
        if isinstance(connection_source, str):
            # It's a connection string
            from sqlalchemy import create_engine
            engine = create_engine(connection_source)
            self.connection = self.SQLAlchemyConnection(engine, config=self.config)
        elif hasattr(connection_source, 'connect') and not hasattr(connection_source, 'cursor'):
            # It's likely an SQLAlchemy engine
            self.connection = self.SQLAlchemyConnection(connection_source, config=self.config)
        elif hasattr(connection_source, 'cursor') or hasattr(connection_source, 'execute'):
            # It's likely a DB-API connection
            self.connection = self.DBAPIConnection(connection_source, config=self.config)
        else:
            # Try to determine it automatically
            self.connection = self._detect_connection_type(connection_source)
        
        # Register the connection with JupySQL's ConnectionManager
        self.ConnectionManager.set(self.connection, displaycon=False)
        
    def _create_config(self, config_dict=None):
        """Create a configuration object with sensible defaults"""
        class Config:
            autopandas = True
            autopolars = False
            autocommit = True
            feedback = False
            polars_dataframe_kwargs = {}
            style = "DEFAULT"
            autolimit = 0
            displaylimit = 0
            named_parameters = True
            
        # Update with user-provided config
        if config_dict:
            for key, value in config_dict.items():
                setattr(Config, key, value)
                
        return Config
    
    def _detect_connection_type(self, conn):
        """Attempt to detect the connection type automatically"""
        # Try to determine if it's a Spark session
        if hasattr(conn, 'sql') and hasattr(conn, 'catalog'):
            try:
                from sql.connection import SparkConnectConnection
                return SparkConnectConnection(conn)
            except ImportError:
                pass
            
        # Try SQLAlchemy then DB-API
        try:
            return self.SQLAlchemyConnection(conn, config=self.config)
        except Exception:
            try:
                return self.DBAPIConnection(conn, config=self.config)
            except Exception as e:
                raise ValueError(f"Could not determine connection type: {e}")
    
    def query(self, sql: str, params: Dict[str, Any] = None) -> pd.DataFrame:
        """
        Execute a single SQL query and return the results as a pandas DataFrame
        
        Parameters:
        -----------
        sql : str
            The SQL query to execute
        params : dict, optional
            Parameters to bind to the query
            
        Returns:
        --------
        pandas.DataFrame
            The result set as a DataFrame
        """
        # Handle query parameters if provided
        if params:
            sql = self._bind_params(sql, params)
            
        result = self.run_statements(self.connection, sql, self.config)
        
        # Return the result, converting to DataFrame if not already
        if not isinstance(result, pd.DataFrame):
            if hasattr(result, 'DataFrame'):
                return result.DataFrame()
            else:
                # Handle non-result queries (e.g., INSERT, UPDATE)
                return pd.DataFrame()
        return result
    
    def execute(self, sql: str, params: Dict[str, Any] = None) -> Any:
        """
        Execute one or more SQL statements and return the results
        For multiple statements, only the last result is returned
        
        Parameters:
        -----------
        sql : str
            The SQL statement(s) to execute
        params : dict, optional
            Parameters to bind to the query
            
        Returns:
        --------
        The result of the query, typically a pandas DataFrame or ResultSet
        """
        # Handle query parameters if provided
        if params:
            sql = self._bind_params(sql, params)
            
        return self.run_statements(self.connection, sql, self.config)
    
    def _bind_params(self, sql: str, params: Dict[str, Any]) -> str:
        """
        Bind parameters to the SQL query using JupySQL's approach
        If named_parameters is disabled, manually replaces placeholders
        """
        if not self.config.named_parameters:
            # Simple parameter binding
            for key, value in params.items():
                placeholder = f":{key}"
                if placeholder in sql:
                    if isinstance(value, str):
                        value = f"'{value}'"
                    sql = sql.replace(placeholder, str(value))
            return sql
        
        # With named_parameters enabled, JupySQL handles this automatically
        # We just need to add the parameters to the connection's namespace
        if hasattr(self.connection, '_user_ns'):
            self.connection._user_ns.update(params)
        else:
            # For SQLAlchemy connections, we can use bind parameters directly
            return sql  # Parameters will be passed to execute
            
        return sql
    
    def close(self):
        """Close the database connection"""
        if hasattr(self, 'connection') and self.connection:
            self.connection.close()
            
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def to_dataframe(self, result):
        """
        Convert a result to a DataFrame if it's not already
        """
        if isinstance(result, pd.DataFrame):
            return result
        elif hasattr(result, 'DataFrame'):
            return result.DataFrame()
        else:
            return pd.DataFrame()

    def to_polars(self, result):
        """
        Convert a result to a Polars DataFrame
        """
        try:
            import polars as pl
            
            if isinstance(result, pl.DataFrame):
                return result
            elif hasattr(result, 'PolarsDataFrame'):
                return result.PolarsDataFrame()
            elif isinstance(result, pd.DataFrame):
                return pl.from_pandas(result)
            else:
                return pl.DataFrame()
        except ImportError:
            raise ImportError("Polars is not installed. Please install it with: pip install polars")