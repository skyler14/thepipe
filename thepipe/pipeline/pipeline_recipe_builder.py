# thepipe/pipeline/pipeline_recipe_builder.py
"""
LLM-powered pipeline recipe builder that analyzes source code and generates Ploomber pipeline specifications
Recipes are the executable units that get processed through the pipeline system
"""

import os
import json
import yaml
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

from ..core import Chunk
from openai import OpenAI


class PipelineRecipeBuilder:
    """Builds pipeline recipes from source code analysis using LLM"""
    
    def __init__(self, options: Optional[Dict[str, Any]] = None, verbose: bool = False):
        self.options = options or {}
        self.verbose = verbose
        self.openai_client = self._setup_llm_client()
    
    def _setup_llm_client(self) -> Optional[OpenAI]:
        """Setup OpenAI client for LLM interactions"""
        llm_config = self.options.get("llm_extractor", {})
        api_key = llm_config.get("api_key", os.environ.get("OPENAI_API_KEY"))
        api_base = llm_config.get("api_base", os.environ.get("OPENAI_API_BASE"))
        
        if not api_key:
            if self.verbose:
                print("[thepipe] No LLM API key found. Pipeline recipe generation will use heuristics only.")
            return None
        
        client_args = {"api_key": api_key}
        if api_base:
            client_args["base_url"] = api_base
            
        return OpenAI(**client_args)
    
    def build_recipe_from_chunks(self, source_chunks: List[Chunk], recipe_name: str) -> Dict[str, Any]:
        """
        Main method to build pipeline recipe specification from analyzed source chunks
        
        Args:
            source_chunks: List of chunks from thepipe analysis
            recipe_name: Name for the pipeline recipe
            
        Returns:
            Complete pipeline recipe specification dictionary
        """
        if self.verbose:
            print(f"[thepipe] Building pipeline recipe '{recipe_name}' from {len(source_chunks)} chunks")
        
        # Analyze chunks to understand the ETL structure
        analysis = self._analyze_etl_structure(source_chunks)
        
        # Generate pipeline components using LLM or heuristics
        if self.openai_client:
            recipe_spec = self._generate_recipe_with_llm(analysis, recipe_name)
        else:
            recipe_spec = self._generate_recipe_with_heuristics(analysis, recipe_name)
        
        # Add metadata
        recipe_spec["metadata"] = {
            "name": recipe_name,
            "created_at": datetime.now().isoformat(),
            "description": analysis.get("description", f"ETL pipeline recipe generated from source analysis"),
            "source_files": [chunk.path for chunk in source_chunks if chunk.path],
            "thepipe_version": "auto-generated"
        }
        
        # Generate the actual Ploomber YAML
        recipe_spec["pipeline_yaml"] = self._generate_pipeline_yaml(recipe_spec)
        
        # Generate client files
        recipe_spec["client_files"] = self._generate_client_files(recipe_spec)
        
        return recipe_spec
    
    def _analyze_etl_structure(self, chunks: List[Chunk]) -> Dict[str, Any]:
        """Analyze chunks to understand ETL patterns and structure"""
        analysis = {
            "files": [],
            "databases": [],
            "data_sources": [],
            "transformations": [],
            "outputs": [],
            "dependencies": [],
            "description": ""
        }
        
        for chunk in chunks:
            file_analysis = self._analyze_chunk(chunk)
            analysis["files"].append(file_analysis)
            
            # Aggregate findings
            analysis["databases"].extend(file_analysis.get("databases", []))
            analysis["data_sources"].extend(file_analysis.get("data_sources", []))
            analysis["transformations"].extend(file_analysis.get("transformations", []))
            analysis["outputs"].extend(file_analysis.get("outputs", []))
        
        # Deduplicate
        analysis["databases"] = list(set(analysis["databases"]))
        analysis["data_sources"] = list(set(analysis["data_sources"]))
        
        # Infer overall description
        if len(analysis["files"]) == 1:
            analysis["description"] = f"Single-file ETL pipeline recipe with {len(analysis['transformations'])} transformation steps"
        else:
            analysis["description"] = f"Multi-file ETL pipeline recipe with {len(analysis['files'])} components"
        
        return analysis
    
    def _analyze_chunk(self, chunk: Chunk) -> Dict[str, Any]:
        """Analyze individual chunk for ETL patterns"""
        file_analysis = {
            "path": chunk.path,
            "type": self._detect_file_type(chunk),
            "databases": [],
            "data_sources": [],
            "transformations": [],
            "outputs": []
        }
        
        if not chunk.text:
            return file_analysis
        
        text = chunk.text.lower()
        
        # Detect databases
        db_patterns = {
            "postgresql": ["postgresql://", "psycopg2", "postgres"],
            "mysql": ["mysql://", "pymysql", "mysql"],
            "sqlite": ["sqlite://", "sqlite3", ".db"],
            "duckdb": ["duckdb://", "duckdb", ".duckdb"],
            "snowflake": ["snowflake://", "snowflake-connector"],
            "bigquery": ["bigquery://", "google.cloud.bigquery"]
        }
        
        for db_type, patterns in db_patterns.items():
            if any(pattern in text for pattern in patterns):
                file_analysis["databases"].append(db_type)
        
        # Detect data sources
        data_patterns = [
            (".csv", "csv files"),
            (".parquet", "parquet files"), 
            (".json", "json files"),
            (".xlsx", "excel files"),
            ("api", "api endpoints"),
            ("http", "web sources")
        ]
        
        for pattern, description in data_patterns:
            if pattern in text:
                file_analysis["data_sources"].append(description)
        
        # Detect transformations
        transform_patterns = [
            ("select", "sql queries"),
            ("group by", "aggregations"),
            ("join", "table joins"),
            ("merge", "data merging"),
            ("drop", "data cleaning"),
            ("fillna", "missing value handling"),
            ("transform", "data transformations")
        ]
        
        for pattern, description in transform_patterns:
            if pattern in text:
                file_analysis["transformations"].append(description)
        
        # Detect outputs
        if any(pattern in text for pattern in ["to_sql", "to_csv", "to_parquet", "insert into", "create table"]):
            file_analysis["outputs"].append("database/file output")
        
        return file_analysis
    
    def _detect_file_type(self, chunk: Chunk) -> str:
        """Detect the type of file from chunk path and content"""
        if not chunk.path:
            return "unknown"
        
        path_lower = chunk.path.lower()
        
        if path_lower.endswith('.py'):
            return "python"
        elif path_lower.endswith('.sql'):
            return "sql"
        elif path_lower.endswith('.ipynb'):
            return "notebook"
        elif path_lower.endswith(('.yml', '.yaml')):
            return "yaml"
        elif any(ext in path_lower for ext in ['.csv', '.parquet', '.json', '.xlsx']):
            return "data"
        else:
            return "unknown"
    
    def _generate_recipe_with_llm(self, analysis: Dict[str, Any], recipe_name: str) -> Dict[str, Any]:
        """Generate pipeline recipe using LLM analysis"""
        if self.verbose:
            print("[thepipe] Using LLM to generate pipeline recipe specification")
        
        # Prepare analysis for LLM
        analysis_text = self._format_analysis_for_llm(analysis)
        
        # Define tools for structured output
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "generate_database_client",
                    "description": "Generate a database client configuration for pipeline recipe",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "client_name": {"type": "string", "description": "Name for the client function"},
                            "connection_uri": {"type": "string", "description": "SQLAlchemy connection URI"},
                            "db_type": {"type": "string", "enum": ["DuckDB", "PostgreSQL", "SQLite", "MySQL", "Other"]}
                        },
                        "required": ["client_name", "connection_uri", "db_type"]
                    }
                }
            },
            {
                "type": "function", 
                "function": {
                    "name": "define_pipeline_task",
                    "description": "Define a task in the pipeline recipe",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_name": {"type": "string", "description": "Unique name for the task"},
                            "source_file": {"type": "string", "description": "Path to source file"},
                            "task_class": {"type": "string", "enum": ["PythonCallable", "SQLScript", "SQLDump", "NotebookRunner"]},
                            "product_type": {"type": "string", "enum": ["table", "file", "view"]},
                            "product_name": {"type": "string", "description": "Name of output table/file"},
                            "upstream_tasks": {"type": "array", "items": {"type": "string"}},
                            "client_name": {"type": "string", "description": "Name of database client to use"}
                        },
                        "required": ["task_name", "source_file", "task_class", "product_type", "product_name"]
                    }
                }
            }
        ]
        
        prompt = f"""
You are an expert Ploomber pipeline architect. Analyze the following ETL code structure and generate a pipeline recipe specification.

Pipeline Recipe Name: {recipe_name}

Analysis:
{analysis_text}

Please analyze this structure and create a pipeline recipe by calling the appropriate tools:

1. For each unique database connection, call generate_database_client
2. For each logical processing step, call define_pipeline_task
3. Ensure tasks are properly ordered with correct dependencies
4. Use appropriate Ploomber task classes:
   - PythonCallable: For .py files with functions
   - SQLScript: For .sql files that create tables/views  
   - SQLDump: For queries that export data to files
   - NotebookRunner: For .ipynb files

Focus on creating a clean, maintainable pipeline recipe structure.
"""
        
        try:
            response = self.openai_client.chat.completions.create(
                model=self.options.get("llm_extractor", {}).get("model", "gpt-4"),
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
                tool_choice="auto"
            )
            
            # Process tool calls
            clients = []
            tasks = []
            
            if response.choices[0].message.tool_calls:
                for tool_call in response.choices[0].message.tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)
                    
                    if function_name == "generate_database_client":
                        clients.append(function_args)
                    elif function_name == "define_pipeline_task":
                        tasks.append(function_args)
            
            return {"clients": clients, "tasks": tasks, "llm_generated": True}
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] LLM generation failed: {e}, falling back to heuristics")
            return self._generate_recipe_with_heuristics(analysis, recipe_name)
    
    def _generate_recipe_with_heuristics(self, analysis: Dict[str, Any], recipe_name: str) -> Dict[str, Any]:
        """Generate pipeline recipe using rule-based heuristics when LLM is not available"""
        if self.verbose:
            print("[thepipe] Using heuristic analysis to generate pipeline recipe specification")
        
        clients = []
        tasks = []
        
        # Generate database clients based on detected databases
        for i, db_type in enumerate(set(analysis["databases"])):
            client_name = f"get_{db_type.lower()}_client"
            
            # Generate appropriate connection URI
            if db_type.lower() == "duckdb":
                connection_uri = "duckdb:///data/warehouse.db"
            elif db_type.lower() == "sqlite":
                connection_uri = "sqlite:///data/database.db"
            elif db_type.lower() == "postgresql":
                connection_uri = "postgresql://user:pass@localhost:5432/db"
            else:
                connection_uri = f"{db_type.lower()}://localhost/db"
            
            clients.append({
                "client_name": client_name,
                "connection_uri": connection_uri,
                "db_type": db_type
            })
        
        # Generate tasks based on file analysis
        for i, file_info in enumerate(analysis["files"]):
            if not file_info["path"]:
                continue
            
            file_type = file_info["type"]
            base_name = Path(file_info["path"]).stem.replace("_", "-").replace(" ", "-")
            
            # Determine task class based on file type and content
            if file_type == "python":
                task_class = "PythonCallable"
                product_type = "table"
            elif file_type == "sql":
                # Heuristic: if CREATE in content, it's SQLScript, else SQLDump
                task_class = "SQLScript" if "create" in str(file_info.get("transformations", [])) else "SQLDump"
                product_type = "table" if task_class == "SQLScript" else "file"
            elif file_type == "notebook":
                task_class = "NotebookRunner"
                product_type = "table"
            else:
                continue  # Skip non-executable files
            
            # Generate task
            task = {
                "task_name": f"{base_name}-{i}" if i > 0 else base_name,
                "source_file": file_info["path"],
                "task_class": task_class,
                "product_type": product_type,
                "product_name": f"{base_name}_output",
                "upstream_tasks": [] if i == 0 else [f"{Path(analysis['files'][i-1]['path']).stem}-{i-1}"] if i > 0 else []
            }
            
            # Assign client if database operations detected
            if file_info["databases"] and clients:
                task["client_name"] = clients[0]["client_name"]
            
            tasks.append(task)
        
        return {"clients": clients, "tasks": tasks, "llm_generated": False}
    
    def _format_analysis_for_llm(self, analysis: Dict[str, Any]) -> str:
        """Format analysis results for LLM consumption"""
        text = f"Description: {analysis['description']}\n\n"
        
        text += "Files analyzed:\n"
        for file_info in analysis["files"]:
            text += f"- {file_info['path']} ({file_info['type']})\n"
            if file_info['databases']:
                text += f"  Databases: {', '.join(file_info['databases'])}\n"
            if file_info['transformations']:
                text += f"  Operations: {', '.join(file_info['transformations'])}\n"
        
        text += f"\nDatabases detected: {', '.join(analysis['databases'])}\n"
        text += f"Data sources: {', '.join(analysis['data_sources'])}\n"
        
        return text
    
    def _generate_pipeline_yaml(self, recipe_spec: Dict[str, Any]) -> str:
        """Generate the Ploomber pipeline.yaml content for recipe"""
        yaml_spec = {}
        
        # Add clients section
        if recipe_spec.get("clients"):
            clients_config = {}
            for client in recipe_spec["clients"]:
                # Map client to task classes that will use it
                clients_config["SQLScript"] = f"clients.{client['client_name']}"
                clients_config["SQLDump"] = f"clients.{client['client_name']}"
                clients_config["GenericSQLRelation"] = f"clients.{client['client_name']}"
            yaml_spec["clients"] = clients_config
        
        # Add tasks section
        tasks = []
        for task in recipe_spec.get("tasks", []):
            task_def = {
                "source": task["source_file"],
                "name": task["task_name"]
            }
            
            # Add product
            if task["product_type"] == "table":
                task_def["product"] = [None, task["product_name"], "table"]
            else:
                task_def["product"] = f"output/{task['product_name']}.csv"
            
            # Add upstream dependencies
            if task.get("upstream_tasks"):
                task_def["upstream"] = task["upstream_tasks"]
            
            tasks.append(task_def)
        
        yaml_spec["tasks"] = tasks
        
        return yaml.dump(yaml_spec, default_flow_style=False, sort_keys=False)
    
    def _generate_client_files(self, recipe_spec: Dict[str, Any]) -> Dict[str, str]:
        """Generate the clients.py file content for pipeline recipe"""
        files = {}
        
        if not recipe_spec.get("clients"):
            return files
        
        client_content = "from ploomber.clients import SQLAlchemyClient\nimport os\n\n"
        
        for client in recipe_spec["clients"]:
            client_content += f"""
def {client['client_name']}():
    \"\"\"Client for {client['db_type']} database.\"\"\"
    # Connection URI - in production, load from environment variables
    uri = os.environ.get('{client['client_name'].upper()}_URI', '{client['connection_uri']}')
    return SQLAlchemyClient(uri)
"""
        
        files["clients.py"] = client_content
        
        return files