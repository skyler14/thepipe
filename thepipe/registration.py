"""
Agent Registration Module

Handles self-registration of thepipe into various AI agent platforms:
- Claude Code (skills)
- Google Antigravity (actions)
- MCP Server (optional)
- stdout (manual copy-paste)
"""

from pathlib import Path
from typing import Optional
import json
import os
import sys

def _add_to_gitignore(base_dir: Path, pattern: str):
    """Add pattern to .gitignore if not already present"""
    gitignore_path = base_dir / ".gitignore"
    
    if gitignore_path.exists():
        existing = gitignore_path.read_text()
        if pattern not in existing:
            with open(gitignore_path, 'a') as f:
                f.write(f"\n# AI agent local files\n{pattern}\n")
    else:
        gitignore_path.write_text(f"# AI agent local files\n{pattern}\n")


def _get_thepipe_path() -> str:
    """Get the absolute path to invoke thepipe, independent of shell PATH.
    
    Uses sys.executable to determine the Python environment's bin directory,
    ensuring agents can invoke thepipe without relying on PATH resolution.
    
    Returns:
        Absolute path to the thepipe executable, OR a python -m invocation.
    """
    # PRIMARY: Use sys.executable to find thepipe in the same bin directory
    # This is the Pythonic equivalent of `which` for the current environment
    python_bin_dir = os.path.dirname(sys.executable)
    thepipe_in_env = os.path.join(python_bin_dir, 'thepipe')
    
    if os.path.exists(thepipe_in_env):
        return thepipe_in_env
    
    # FALLBACK: Avoid `python -m thepipe` because thepipe may not ship __main__.py
    return f'{sys.executable} -c "from thepipe import main; main()"'




# Universal instruction template - uses {thepipe_cmd} placeholder
INSTRUCTION_TEMPLATE = """# thepipe

Data extraction for files, URLs, databases. 165+ language AST support.

## Default Repo Mapping (important)

Start by scanning the entire repo to map structure and dependencies.
Use `code_relations: "map"` for first touch (digest-everything, ~95%+ token savings).
Do NOT use `mapnn` as a default first pass unless the user explicitly requests a scoped map
or prior context already reveals most of the file structure.

```bash
{thepipe_cmd} ./repo --options '{"code_relations": "map"}' -f
# Digest-everything pass for maximum token reduction
```

## Code Analysis (use for any programming task)

## Common Commands

```bash
# PDF/document
{thepipe_cmd} document.pdf -f

# Directory with filter
{thepipe_cmd} ./src --include_patterns "*.py" -f

# URL
{thepipe_cmd} https://example.com -f

# GitHub repo with code analysis + patterns
{thepipe_cmd} https://github.com/user/repo --include_patterns "src/**/*.py" "src/**/*.ts" --options '{"code_relations": "map"}' -f

# Video/audio text extraction
{thepipe_cmd} video.mp4 --text_only -f
```

## Output Formats

```bash
{thepipe_cmd} source -f        # markdown to stdout (default for humans)
{thepipe_cmd} source -f text   # raw text
{thepipe_cmd} source -f json   # JSON array (only if explicitly requested)
{thepipe_cmd} source           # writes to outputs/prompt.txt
```

## Code Analysis Modes

```bash
# map (default first pass) - all files as digests
{thepipe_cmd} ./repo --options '{"code_relations": "map"}' -f

# auto - use only if you explicitly want size-based heuristics
{thepipe_cmd} ./repo --options '{"code_relations": "auto"}' -f

# mapnn (use sparingly) - primary files full, neighbors as digests
{thepipe_cmd} ./repo --include_patterns "src/*.py" --options '{"code_relations": "mapnn"}' -f

# limited - only matched files, no digests
{thepipe_cmd} ./repo --include_patterns "*.py" --options '{"code_relations": "limited"}' -f

# mapnew - diff map (old vs new git revisions)
{thepipe_cmd} ./repo --options '{"code_relations": "mapnew"}' -f
```

## Code Analysis Parameters (`--options`)

```json
{
  "code_relations": "map",
  "code_n1": 3,
  "code_n2": 5,
  "code_old": "HEAD~1",
  "code_new": "",
  "code_nf": 100,
  "code_nt": 150000
}
```

- `code_n1`: Neighbor depth to include as digests in `mapnn`
- `code_n2`: Cutoff depth in `mapnn`
- `code_old`: Old git commit-ish for `mapnew` (default: HEAD)
- `code_new`: New git commit-ish for `mapnew` (default: working tree)
- `json_verbose`: Include imports, symbol spans, call graph, logical region hashes, and mapnew file/hunk preview metadata in JSON output (`-f json`)
- `code_nf`: File-count threshold used by `auto`
- `code_nt`: Token threshold used by `auto`

## File Filtering

```bash
# Glob patterns (recommended)
{thepipe_cmd} ./repo --include_patterns "*.py" "*.ts" -f

# Regex filter
{thepipe_cmd} ./repo --include_regex ".*\\.(py|ts)$" -f
```

## Text Extraction Modes

```bash
{thepipe_cmd} video.mp4 --text_only             -f  # default
{thepipe_cmd} video.mp4 --text_only transcribe  -f  # force local transcription
{thepipe_cmd} video.mp4 --text_only ai          -f  # prefer AI-generated transcript
{thepipe_cmd} video.mp4 --text_only uploaded    -f  # prefer uploaded captions
```

## Database Operations

```bash
# PostgreSQL query
{thepipe_cmd} "postgresql://user:pass@host:5432/db" --db "SELECT * FROM users" -f

# MySQL query
{thepipe_cmd} "mysql://user:pass@host:3306/db" --db "SELECT * FROM orders" -f

# SQLite file
{thepipe_cmd} data.db --db "SELECT * FROM table" -f

# SQLite with path syntax
{thepipe_cmd} "sqlite:///path/to/db.sqlite" --db "SELECT *" -f

# DuckDB
{thepipe_cmd} "duckdb:///path/to/db.duckdb" --db "SELECT *" -f

# Raw ODBC
{thepipe_cmd} "odbc://?connect=DRIVER%3DSQLite3%3BDatabase%3D%2Ftmp%2Fdemo.db" --db "SELECT * FROM orders" -f

# Show schema only (no query)
{thepipe_cmd} "postgresql://host/db" --db -f

# Parquet file as database
{thepipe_cmd} data.parquet --db "SELECT * FROM source_data WHERE col > 10" -f

# Parquet (recommended for local files)
# Parquet is the most native file-backed format for --db. Point thepipe directly
# at the .parquet file and query source_data, without wrapping in DuckDB.
{thepipe_cmd} /path/to/data.parquet --db "SELECT * FROM source_data LIMIT 10" -f

# CSV file as database  
{thepipe_cmd} data.csv --db "SELECT * FROM source_data LIMIT 100" -f

# Excel file as database
{thepipe_cmd} data.xlsx --db "SELECT * FROM source_data" -f
```

## Database Connection Formats

- PostgreSQL: `postgresql://user:pass@host:5432/db`
- MySQL: `mysql://user:pass@host:3306/db`
- MariaDB: `mariadb://user:pass@host:3306/db`
- SQLite: `sqlite:///path/to/database.db`
- DuckDB: `duckdb:///path/to/database.duckdb`
- Raw ODBC: `odbc://?connect=<urlencoded ODBC connection string>`
- MS SQL Server: `mssql://user:pass@host:1433/db`
- JDBC MySQL: `jdbc:mysql://host:3306/db` (auto-converted)
- JDBC PostgreSQL: `jdbc:postgresql://host:5432/db` (auto-converted)

## Data File View Names (`--db`)

- Parquet: `parquet_data`
- ORC: `orc_data`
- Feather/Arrow/IPC: `feather_data`
- JSONL/NDJSON: `jsonl_data`
- CSV: `csv_data`
- Excel: `excel_data`

## Common `--options` JSON

```json
{
  "read_executable": false,
  "blacklist_files": [".gitignore"],
  "github_token": "ghp_...",
  "gitignore": true,
  "max_depth": 3,
  "service_account_file": "/path/to/creds.json",
  "cookies": {
    "browser_type": "chrome",
    "show": "format"
  }
}
```

## OpenAI / LLM Flags

```bash
{thepipe_cmd} source --openai-api-key "$OPENAI_API_KEY" --openai-model gpt-4o -f
{thepipe_cmd} source --openai-base-url https://api.openai.com/v1 -f
```

## Agent Mode (LLM Delegation)

When thepipe needs LLM inference inside an agent, use `--llm-provider agent`.
Default to FIFO pipes for agentic runs unless a complex workflow truly needs CLI output:

```bash
{thepipe_cmd} data.pdf --extract '{"title": "str"}' --llm-provider agent
```

**How agent mode works:**

1. thepipe creates two named pipes (FIFOs) in `/tmp/thepipe_pipes/`
2. thepipe prints to stderr:
   ```
   <<<THEPIPE_LLM_QUERY>>>
   QUERY_PIPE: /tmp/thepipe_pipes/query_<pid>_<timestamp>
   RESPONSE_PIPE: /tmp/thepipe_pipes/response_<pid>_<timestamp>
   {json payload with messages}
   <<<END_QUERY>>>
   ```
3. Agent must:
   - Read the JSON from QUERY_PIPE (blocking read)
   - Execute LLM call with the messages array
   - Write response text to RESPONSE_PIPE
4. thepipe reads response and continues execution

**Agent response steps:**
```bash
# 1. Read query (blocks until thepipe writes)
cat /tmp/thepipe_pipes/query_<pid>_<timestamp>

# 2. Execute LLM call with messages from JSON

# 3. Write response
echo "your LLM response text" > /tmp/thepipe_pipes/response_<pid>_<timestamp>
```

## Registration

Install thepipe capability into AI agents:

```bash
# Antigravity/Gemini - creates .antigravity/workflows/thepipe.md
{thepipe_cmd} --register agent

# Claude Code - creates .claude/skills/thepipe/SKILL.md
{thepipe_cmd} --register code

# Stdout for copy-paste
{thepipe_cmd} --register
```

After registration, agents can invoke thepipe using the absolute paths shown in the generated files.
"""




def register_stdout() -> str:
    """Generate markdown for manual copy-paste into chat interfaces"""
    instructions = INSTRUCTION_TEMPLATE.replace("{thepipe_cmd}", _get_thepipe_path())
    return f"""# thepipe Installation Instructions

Copy the content below and paste it into your AI assistant's chat interface (ChatGPT, Claude.ai, Gemini, etc.) to enable thepipe capabilities.

---

{instructions}

---

**Note**: This tool runs on your local machine. The AI will suggest `thepipe` commands for you to execute in your terminal.
"""


def register_claude_code(target_dir: Optional[str] = None) -> Path:
    """
    Register thepipe as a Claude Code skill.
    Creates .claude/skills/thepipe/SKILL.md with proper skill format.
    
    Skills are installed to:
    - Project-local: .claude/skills/thepipe/
    - Global: ~/.claude/skills/thepipe/
    """
    base_dir = Path(target_dir or os.getcwd())
    skill_dir = base_dir / ".claude" / "skills" / "thepipe"
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    skill_file = skill_dir / "SKILL.md"
    
    # Get the actual thepipe command path and Python executable
    thepipe_cmd = _get_thepipe_path()
    python_exe = sys.executable
    
    # Build skill content with proper frontmatter
    instructions = INSTRUCTION_TEMPLATE.replace("{thepipe_cmd}", thepipe_cmd)
    
    content = f"""---
name: thepipe
description: Extract data from files, URLs, databases. Use code_relations for code analysis with 90%+ token savings.
version: 1.0.0
---

# ENVIRONMENT INFO

> **CRITICAL**: Use these EXACT paths when invoking thepipe. Do NOT rely on shell PATH.

| Component | Absolute Path |
|-----------|---------------|
| **thepipe command** | `{thepipe_cmd}` |
| **Python executable** | `{python_exe}` |

---

# thepipe - Data Extraction & Code Analysis

**Invocation**: `{thepipe_cmd}`

---

## ⚡ CRITICAL: Before Re-running

If you have previously run thepipe on this codebase in this conversation:
1. Check if the output is still in context
2. Only re-run if you made significant code changes
3. Use `--include_patterns` to narrow scope if needed

---

{instructions}
"""
    
    skill_file.write_text(content)
    return skill_file


def register_antigravity(target_dir: Optional[str] = None) -> tuple[Path, Optional[Path]]:
    """
    Register thepipe as an Antigravity workflow.
    Creates .antigravity/workflows/thepipe.md with YAML frontmatter
    
    Workflows can be triggered with /thepipe in the Antigravity chat.
    
    Returns:
        (workflow_file_path, agents_md_path or None)
    """
    base_dir = Path(target_dir or os.getcwd())
    
    # Create workflow file in correct Antigravity folder
    # Workspace workflows: .antigravity/workflows/
    # Global workflows: ~/.antigravity/workflows/
    workflows_dir = base_dir / ".antigravity" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    
    workflow_file = workflows_dir / "thepipe.md"
    
    # Get the actual thepipe command path and Python executable
    thepipe_cmd = _get_thepipe_path()
    python_exe = sys.executable
    
    # Use YAML frontmatter format like other Antigravity workflows
    # Inject the actual command path into the template
    instructions = INSTRUCTION_TEMPLATE.replace("{thepipe_cmd}", thepipe_cmd)
    workflow_content = f"""---
description: Extract data from files, URLs, databases using thepipe. Use when asked to pipe, pipe in, or extract content from any source.
---

# ENVIRONMENT INFO

> **CRITICAL**: Use these EXACT paths when invoking thepipe. Do NOT rely on shell PATH.

| Component | Absolute Path |
|-----------|---------------|
| **thepipe command** | `{thepipe_cmd}` |
| **Python executable** | `{python_exe}` |

---

{instructions}
"""
    workflow_file.write_text(workflow_content)
    
    # Create or update AGENTS.md (standard for AI agent discovery)
    agents_md = base_dir / "AGENTS.md"
    agents_md_updated = None
    
    if agents_md.exists():
        existing_content = agents_md.read_text(encoding="utf-8")
        if "thepipe" not in existing_content:
            # Append thepipe section
            new_content = f"""{existing_content.rstrip()}

## thepipe

Data extraction and document processing tool. Use for extracting content from files, URLs, and databases.

**Installation**: `pip install thepipe-api`

**Basic usage**: `thepipe <source> [-f md|text|json]` (default markdown; use `-f json` only if explicitly requested)

See `.antigravity/workflows/thepipe.md` for detailed documentation.
"""
            agents_md.write_text(new_content, encoding="utf-8")
            agents_md_updated = agents_md
    else:
        # Create new AGENTS.md with thepipe section
        agents_md_content = """# AGENTS.md

This file documents AI-callable tools available in this repository.

## thepipe

Data extraction and document processing tool. Use for extracting content from files, URLs, and databases.

**Installation**: `pip install thepipe-api`

**Basic usage**: `thepipe <source> [-f md|text|json]` (default markdown; use `-f json` only if explicitly requested)

See `.antigravity/workflows/thepipe.md` for detailed documentation.
"""
        agents_md.write_text(agents_md_content, encoding="utf-8")
        agents_md_updated = agents_md
    
    # Update auto-execute allowlist
    _add_to_allowlist(base_dir)
    
    return workflow_file, agents_md_updated


def register_codex(target_dir: Optional[str] = None) -> Path:
    """
    Register thepipe for Codex by writing AGENTS.md instructions.

    Creates or updates a marked block in AGENTS.md with absolute invocation paths.
    """
    base_dir = Path(target_dir or os.getcwd())
    agents_file = base_dir / "AGENTS.md"

    thepipe_cmd = _get_thepipe_path()
    python_exe = sys.executable

    begin_marker = "<!-- thepipe-codex:begin -->"
    end_marker = "<!-- thepipe-codex:end -->"

    block = f"""{begin_marker}
## thepipe

Use thepipe for file/URL/database extraction and codebase mapping.

### Absolute Paths (Codex)
- `thepipe`: `{thepipe_cmd}`
- `python`: `{python_exe}`

### Recommended Commands
```bash
{thepipe_cmd} ./repo --options '{{"code_relations": "auto"}}' -f
{thepipe_cmd} ./repo --options '{{"code_relations": "map"}}' -f
```

### Notes
- Use the absolute `thepipe` path above instead of relying on shell PATH.
- Prefer `code_relations` modes for programming/repo analysis tasks.
- Default output is markdown; use `-f json` only if explicitly requested.
- For agentic runs, prefer `--llm-provider agent` (FIFO pipes) unless a complex workflow needs CLI output.
{end_marker}
"""

    if agents_file.exists():
        existing = agents_file.read_text(encoding="utf-8")
        begin_count = existing.count(begin_marker)
        end_count = existing.count(end_marker)

        if begin_count == 0 and end_count == 0:
            if existing.endswith("\n\n"):
                sep = ""
            elif existing.endswith("\n"):
                sep = "\n"
            elif existing:
                sep = "\n\n"
            else:
                sep = ""
            updated = existing + sep + block + "\n"
        elif begin_count == 1 and end_count == 1:
            start = existing.index(begin_marker)
            end = existing.index(end_marker, start) + len(end_marker)
            prefix = existing[:start].rstrip()
            suffix = existing[end:]
            updated = (prefix + "\n\n" if prefix else "") + block
            if suffix:
                if not suffix.startswith("\n"):
                    updated += "\n"
                updated += suffix
            else:
                updated += "\n"
        else:
            raise ValueError(
                "AGENTS.md contains malformed or duplicate thepipe codex markers; "
                "please fix markers before re-running --register codex"
            )
    else:
        updated = block + "\n"

    agents_file.write_text(updated, encoding="utf-8")
    return agents_file


def _add_to_allowlist(base_dir: Path):
    """Add thepipe to auto-execute allowlist in .agent/config.json"""
    config_file = base_dir / ".agent" / "config.json"
    
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
        except json.JSONDecodeError:
            config = {}
    else:
        config = {}
    
    # Ensure structure exists
    if "terminal" not in config:
        config["terminal"] = {}
    if "auto_execute_allowlist" not in config["terminal"]:
        config["terminal"]["auto_execute_allowlist"] = []
    
    # Add thepipe if not present
    allowlist = config["terminal"]["auto_execute_allowlist"]
    if "thepipe" not in allowlist:
        allowlist.append("thepipe")
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(config, indent=2))


def register_help() -> str:
    """Generate self-discovery help text"""
    return """thepipe - Self-Registration Guide

I am thepipe, a data extraction and document processing tool.

To install my capabilities into your AI development environment:

1. **Claude Code**: Run `thepipe --register code`
   - Creates `.claude/skills/thepipe/` with skill definitions
   
2. **Google Antigravity**: Run `thepipe --register agent`
   - Creates `.antigravity/workflows/thepipe.md`
   - Updates `AGENTS.md` if present
   - Adds to auto-execute allowlist

3. **Codex**: Run `thepipe --register codex`
   - Creates or updates `AGENTS.md` with absolute thepipe/python paths

4. **Manual/Chat**: Run `thepipe --register`
   - Outputs markdown to copy-paste into chat interfaces

5. **MCP Server**: Run `thepipe --register mcp` (coming soon)
   - Registers as persistent Model Context Protocol server

After registration, you can call me directly without manual prompting in future conversations.
"""


def register_mcp(target_dir: Optional[str] = None, remote_url: Optional[str] = None) -> str:
    """
    Register thepipe as an MCP server.
    
    Args:
        target_dir: Target directory for config updates
        remote_url: Optional remote MCP server URL
    
    Returns:
        Instructions for completing MCP setup
    """
    # Placeholder for full MCP implementation
    return """MCP Registration: Not yet implemented

To manually configure thepipe as an MCP server:

1. Add to your MCP config file:
   - Antigravity: ~/.gemini/antigravity/mcp_config.json
   - Claude Desktop: ~/Library/Application Support/Claude/claude_desktop_config.json

2. Add this entry:
   {
     "thepipe": {
       "command": "thepipe",
       "args": ["mcp-start"],
       "env": {}
     }
   }

3. Restart your AI application

Note: Full MCP server implementation coming soon.
"""
