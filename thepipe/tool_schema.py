"""
Tool Schema for thepipe - Universal format for Claude and Gemini APIs.

This module outputs tool definitions compatible with:
- Claude API (Anthropic) - with `allowed_callers` for programmatic tool calling
- Gemini API (Google) - OpenAPI 3.0 subset format

Usage:
    # CLI
    thepipe --register-tools           # JSON output for Claude API
    thepipe --register-tools-gemini    # JSON output for Gemini API
    thepipe --register-tools-summary   # Human-readable summary
    
    # Python
    from thepipe.tool_schema import get_claude_tools, get_gemini_tools
"""

from typing import Any, Dict, List, Optional
import json


# ============================================================================
# TOOL DEFINITIONS (Universal)
# ============================================================================

TOOLS = [
    {
        "name": "scrape_file",
        "description": (
            "Extract clean markdown, text, and images from any file. "
            "Supports: PDF, DOCX, PPTX, images, videos, audio, Jupyter notebooks, "
            "spreadsheets (CSV/XLSX), ZIP archives, HTML, and plaintext. "
            "Returns structured chunks with path, text content, and optional images. "
            "For complex PDFs, pass an OpenAI client for VLM-enhanced extraction."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to the file to scrape"
                },
                "verbose": {
                    "type": "boolean",
                    "description": "Print progress messages"
                },
                "text_only": {
                    "type": "boolean",
                    "description": "Extract text only, suppress images"
                },
                "chunking_method": {
                    "type": "string",
                    "enum": ["chunk_by_page", "chunk_by_document", "chunk_by_section", 
                             "chunk_by_length", "chunk_semantic", "chunk_agentic"],
                    "description": "Method to split content into chunks"
                }
            },
            "required": ["filepath"]
        },
        "examples": [
            {"filepath": "paper.pdf"},
            {"filepath": "report.docx", "text_only": True},
            {"filepath": "slides.pptx", "chunking_method": "chunk_by_page"}
        ]
    },
    {
        "name": "scrape_url",
        "description": (
            "Extract content from any URL. Auto-detects: webpages (renders with Playwright), "
            "GitHub repos (clones and extracts), YouTube (transcribes with Whisper), "
            "Google Drive (downloads files), Twitter/X (extracts tweets). "
            "Returns chunks with text and images."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to scrape"
                },
                "include_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Glob patterns to filter files (for repos), e.g. ['*.py', '*.ts']"
                },
                "text_only": {
                    "type": "boolean",
                    "description": "Extract text only, suppress images"
                },
                "verbose": {
                    "type": "boolean",
                    "description": "Print progress messages"
                }
            },
            "required": ["url"]
        },
        "examples": [
            {"url": "https://en.wikipedia.org/wiki/Python"},
            {"url": "https://github.com/user/repo", "include_patterns": ["*.py"]},
            {"url": "https://youtube.com/watch?v=abc123", "text_only": True}
        ]
    },
    {
        "name": "scrape_directory",
        "description": (
            "Recursively scrape all files in a directory. Ignores node_modules, .git, "
            "venv, compiled files (.pyc, .dll, .exe). Use include_patterns to filter. "
            "For code repos, use code_relations to get intelligent digests with "
            "dependency mapping and ~90% token savings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dir_path": {
                    "type": "string",
                    "description": "Directory path to scrape"
                },
                "include_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Glob patterns to include files, e.g. ['*.py', '*.ts']"
                },
                "verbose": {
                    "type": "boolean",
                    "description": "Print progress and token savings"
                },
                "options": {
                    "type": "object",
                    "description": "Additional options as key-value pairs",
                    "properties": {
                        "code_relations": {
                            "type": "string",
                            "enum": ["limited", "map", "mapnn", "mapall"],
                            "description": (
                                "Code analysis mode: "
                                "'limited' = only requested files, "
                                "'map' = all files with digests, "
                                "'mapnn' = digests with N1/N2 neighbor cutoff (recommended), "
                                "'mapall' = full for patterns, digest for rest"
                            )
                        },
                        "code_n1": {
                            "type": "integer",
                            "description": "N1 neighbor depth - files within N1 hops included as digests (default: 3)"
                        },
                        "code_n2": {
                            "type": "integer",
                            "description": "N2 cutoff depth - files beyond N2 hops excluded (default: 5)"
                        }
                    }
                }
            },
            "required": ["dir_path"]
        },
        "examples": [
            {"dir_path": "./src", "include_patterns": ["*.py", "*.tsx"]},
            {"dir_path": ".", "include_patterns": ["src/*.py"], "options": {"code_relations": "mapnn"}},
            {"dir_path": ".", "include_patterns": ["main.py"], "options": {"code_relations": "mapnn", "code_n1": 2, "code_n2": 4}}
        ]
    },
    {
        "name": "scrape_database",
        "description": (
            "Query databases using SQL or natural language. Supports DuckDB, SQLite, "
            "PostgreSQL, MySQL. Can convert natural language to SQL using LLM. "
            "Use mode='schema' for structure, mode='preview' for sample data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Database file path or connection string"
                },
                "query": {
                    "type": "string",
                    "description": "SQL query or natural language question"
                },
                "db_type": {
                    "type": "string",
                    "enum": ["duckdb", "sqlite", "postgresql", "mysql"],
                    "description": "Database type (auto-detected if omitted)"
                },
                "mode": {
                    "type": "string",
                    "enum": ["schema", "preview", "query"],
                    "description": "Operation mode"
                }
            },
            "required": ["filepath"]
        },
        "examples": [
            {"filepath": "data.db", "mode": "schema"},
            {"filepath": "sales.duckdb", "query": "SELECT * FROM orders LIMIT 10"},
            {"filepath": "data.db", "query": "What were top 5 products last month?"}
        ]
    },
    {
        "name": "extract",
        "description": (
            "Extract structured data from chunks using a JSON schema. Uses LLM to parse "
            "content and return data matching the schema. Good for entities, receipts, forms."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chunks": {
                    "type": "array",
                    "description": "List of Chunk objects to extract from"
                },
                "schema": {
                    "type": "object",
                    "description": "JSON schema defining fields, e.g., {'name': 'string', 'amount': 'float'}"
                },
                "multiple_extractions": {
                    "type": "boolean",
                    "description": "Extract multiple items per chunk"
                }
            },
            "required": ["chunks", "schema"]
        },
        "examples": [
            {"schema": {"store_name": "string", "total": "float"}}
        ]
    },
    {
        "name": "chunks_to_messages",
        "description": (
            "Convert Chunks to OpenAI/Claude chat message format for use with LLM APIs. "
            "Each chunk becomes a message with text and optional images."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chunks": {
                    "type": "array",
                    "description": "List of Chunk objects"
                },
                "text_only": {
                    "type": "boolean",
                    "description": "Exclude images"
                },
                "include_paths": {
                    "type": "boolean",
                    "description": "Include file paths in messages"
                }
            },
            "required": ["chunks"]
        },
        "examples": [
            {"text_only": True},
            {"include_paths": True}
        ]
    }
]


# ============================================================================
# FORMAT CONVERTERS
# ============================================================================

def get_claude_tools(programmatic: bool = True) -> List[Dict[str, Any]]:
    """
    Get tool definitions in Claude API format.
    
    Args:
        programmatic: If True, adds allowed_callers for programmatic tool calling
        
    Returns:
        List of tools ready for Claude's messages.create(tools=[...])
    """
    claude_tools = []
    
    for tool in TOOLS:
        claude_tool = {
            "name": f"thepipe_{tool['name']}",
            "description": tool["description"],
            "input_schema": tool["parameters"],
        }
        
        # Add examples if available
        if tool.get("examples"):
            claude_tool["input_examples"] = tool["examples"]
        
        # Add programmatic calling support
        if programmatic:
            claude_tool["allowed_callers"] = ["code_execution_20250825"]
        
        claude_tools.append(claude_tool)
    
    return claude_tools


def get_gemini_tools() -> List[Dict[str, Any]]:
    """
    Get tool definitions in Gemini API format (OpenAPI 3.0 subset).
    
    Returns:
        List of FunctionDeclarations for Gemini's tools parameter
    """
    gemini_tools = []
    
    for tool in TOOLS:
        gemini_tool = {
            "name": f"thepipe_{tool['name']}",
            "description": tool["description"],
            "parameters": tool["parameters"],
        }
        gemini_tools.append(gemini_tool)
    
    return gemini_tools


def get_openai_tools() -> List[Dict[str, Any]]:
    """
    Get tool definitions in OpenAI function calling format.
    
    Returns:
        List of tools for OpenAI's tools parameter
    """
    openai_tools = []
    
    for tool in TOOLS:
        openai_tool = {
            "type": "function",
            "function": {
                "name": f"thepipe_{tool['name']}",
                "description": tool["description"],
                "parameters": tool["parameters"],
            }
        }
        openai_tools.append(openai_tool)
    
    return openai_tools


# ============================================================================
# CLI OUTPUT FUNCTIONS
# ============================================================================

def get_claude_json(programmatic: bool = True, indent: int = 2) -> str:
    """Get Claude tool definitions as JSON string."""
    return json.dumps(get_claude_tools(programmatic), indent=indent)


def get_gemini_json(indent: int = 2) -> str:
    """Get Gemini tool definitions as JSON string."""
    return json.dumps(get_gemini_tools(), indent=indent)


def get_openai_json(indent: int = 2) -> str:
    """Get OpenAI tool definitions as JSON string."""
    return json.dumps(get_openai_tools(), indent=indent)


def print_summary():
    """Print human-readable summary of available tools."""
    print("\n" + "=" * 60)
    print("thepipe - Tool Definitions for AI Agent Integration")
    print("=" * 60)
    print("\nSupported APIs: Claude (Anthropic), Gemini (Google), OpenAI\n")
    
    for tool in TOOLS:
        print(f"  thepipe_{tool['name']}")
        # First line of description
        desc = tool["description"].split(".")[0] + "."
        print(f"    {desc}")
        
        # Required params
        required = tool["parameters"].get("required", [])
        if required:
            print(f"    Required: {', '.join(required)}")
        print()
    
    print("=" * 60)
    print("CLI Usage:")
    print("  thepipe --register-tools          # Claude format (with programmatic calling)")
    print("  thepipe --register-tools-gemini   # Gemini format")
    print("  thepipe --register-tools-openai   # OpenAI format")
    print("=" * 60 + "\n")


# For backwards compatibility with what I created before
def get_all_tools():
    """Alias for get_claude_tools for backwards compatibility."""
    return get_claude_tools()


def get_tool_definitions_json(indent: int = 2) -> str:
    """Alias for get_claude_json for backwards compatibility."""
    return get_claude_json(indent=indent)


def print_tool_summary():
    """Alias for print_summary for backwards compatibility."""
    print_summary()


if __name__ == "__main__":
    print_summary()
