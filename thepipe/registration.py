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
    """Get the exact path to the thepipe executable"""
    import sys
    import shutil
    import os
    
    # First try to find thepipe in PATH
    thepipe_cmd = shutil.which('thepipe')
    if thepipe_cmd:
        return thepipe_cmd
    
    # Try to find thepipe in the same bin dir as python
    python_bin = os.path.dirname(sys.executable)
    thepipe_in_env = os.path.join(python_bin, 'thepipe')
    if os.path.exists(thepipe_in_env):
        return thepipe_in_env
    
    # Fall back to calling the module's main function directly
    return f"{sys.executable} -c \"from thepipe import main; main()\""




# Universal instruction template
INSTRUCTION_TEMPLATE = """# Tool: thepipe
**Description**: Extract clean markdown, text, images, and structured data from any file, URL, or database.

---

## ⚡⚡⚡ CRITICAL: ALWAYS USE CODE_RELATIONS FOR PROGRAMMING TASKS ⚡⚡⚡

**If the user's request involves code, repositories, or programming:**

```bash
# DEFAULT APPROACH - ALWAYS DO THIS FOR CODE
thepipe ./path/to/repo --options '{"code_relations": "auto"}' -f
```

**This gives:**
- 🔥 **90%+ token savings** - intelligent digests preserve structure
- 🔗 **Dependency mapping** - understands imports across files  
- 🏷️ **Semantic tagging** - identifies auth, database, API, testing code
- 📊 **Full context** - LLM understands entire codebase structure

**Supported Languages (with dependency resolution):**
| Language | Built-in | Dependency Mapping |
|----------|----------|-------------------|
| Python | ✅ Full | ✅ imports resolved |
| JavaScript/TypeScript | ✅ Full | ✅ imports resolved |
| Dart/Flutter | ✅ Full | ✅ imports resolved |
| Swift | ✅ Full | ✅ framework detection |
| Kotlin | ✅ Full | ✅ package detection |
| Ruby | ✅ Full | ✅ require_relative |
| Go, Rust, C/C++, Java | ✅ Full | ✅ imports resolved |
| +155 more | ✅ AST | Pattern-based |

> ⚠️ **WARNING**: Do NOT use generic patterns like `*.py` with `code_relations` mapping modes!
> This marks ALL files as primary (full code), defeating the 90% token savings.
> Either: (1) use NO include_patterns to let auto-mode decide, or (2) use specific 
> file patterns like `src/api/*.py` to focus on relevant files only.

**Correct Usage:**

```bash
# ✅ GOOD - Let auto-mode decide what to include
thepipe ./repo --options '{{"code_relations": "auto"}}' -f

# ✅ GOOD - Specific patterns for focused analysis
thepipe ./repo --include_patterns "src/core/*.py" "src/api/*.py" --options '{{"code_relations": "auto"}}' -f

# ❌ BAD - Generic *.py defeats token savings (all files become primary)
# thepipe ./repo --include_patterns "*.py" --options '{{"code_relations": "map"}}' -f
```

# GitHub repo with code analysis
thepipe https://github.com/user/repo --options '{{"code_relations": "auto"}}' -f
```

---

## Code Analysis Modes

| Mode | When to Use |
|------|-------------|
| `auto` | **DEFAULT - picks optimal strategy based on repo size** |
| `map` | Large repos (>100 files) - all files as digests |
| `mapnn` | Focused work - primary files full, neighbors as digests |
| `mapall` | Medium repos - primary full, rest as digests |
| `limited` | Only include_patterns files (no digests) |

---

## Core Capabilities
- **Code Analysis**: Dependency mapping, digests, semantic tagging (**USE THIS FOR CODE**)
- **Files**: PDFs, DOCX, PPTX, images, audio, video, spreadsheets, Jupyter notebooks
- **URLs**: Webpages, GitHub repos, YouTube (transcription), Google Drive
- **Databases**: SQL/natural language queries against DuckDB, SQLite, PostgreSQL, MySQL
- **Extraction**: JSON schema-based structured data extraction

---

## CLI Reference

### Basic Syntax
```bash
thepipe <source> [options]
```

### Source Types
| Source | Example |
|--------|---------|
| File | `thepipe document.pdf` |
| Directory | `thepipe ./src` |
| URL | `thepipe https://example.com` |
| GitHub | `thepipe https://github.com/user/repo` |
| YouTube | `thepipe https://youtube.com/watch?v=abc123` |
| Database | `thepipe data.db --db` |

---

## Output Options

| Flag | Description |
|------|-------------|
| (none) | Write to `outputs/prompt.txt` |
| `-f` or `-f md` | Stdout: Markdown with code fences |
| `-f text` | Stdout: Raw concatenated text |
| `-f json` | Stdout: Structured JSON array |
| `--verbose` | Print status messages |

---

## File Filtering

| Flag | Description |
|------|-------------|
| `--include_patterns "*.py" "*.ts"` | Glob patterns (recommended) |
| `--include_regex ".*\\.py$"` | Regex pattern |

**Examples:**
```bash
# Only Python files
thepipe ./src --include_patterns "*.py"

# Multiple patterns
thepipe ./project --include_patterns "*.py" "*.js" "*.tsx"

# Regex (alternative)
thepipe ./src --include_regex ".*\\.(py|js)$"
```

---

## Text Extraction Modes

| Flag | Description |
|------|-------------|
| `--text_only` | Extract text only (default method) |
| `--text_only transcribe` | Force local transcription (video/audio) |
| `--text_only ai` | Prefer AI-generated transcription |
| `--text_only uploaded` | Prefer uploaded captions |

---

## Database Mode

### Flags
```bash
thepipe <database> --db [query] [options]
```

| Usage | Description |
|-------|-------------|
| `--db` | Show schema + preview |
| `--db "SELECT * FROM users"` | Execute SQL query |
| `--db "What products sold most?"` | Natural language query (requires LLM) |

### Options (via `--options`)
```json
{
  "max_rows": 100,
  "schema_only": true,
  "preview": true,
  "llm_extractor": {
    "api_key": "...",
    "model": "gpt-4o"
  }
}
```

**Supported databases:** SQLite, DuckDB, PostgreSQL, MySQL, Parquet, CSV, Excel

---

## Code Analysis Mode

### Enable via `--options`
```bash
thepipe ./repo --options '{"code_relations": "MODE"}'
```

### Modes
| Mode | Description |
|------|-------------|
| `auto` | **Recommended**. Picks strategy based on repo size |
| `limited` | Only files matching `--include_patterns` |
| `map` | All files as token-efficient digests |
| `mapnn` | Primary files full code, neighbors as digests |
| `mapall` | Primary full, all others as digests |

### Parameters
| Option | Default | Description |
|--------|---------|-------------|
| `code_n1` | 3 | Neighbor depth for mapnn |
| `code_n2` | 5 | Cutoff depth for mapnn |
| `code_nf` | 100 | File count threshold for auto mode |
| `code_nt` | 150000 | Token threshold for auto mode |

**Example:**
```bash
thepipe ./project --include_patterns "src/**/*.py" --options '{
  "code_relations": "mapnn",
  "code_n1": 2,
  "code_n2": 4
}'
```

---

## OpenAI/LLM Options

| Flag | Default | Description |
|------|---------|-------------|
| `--openai-api-key KEY` | `$OPENAI_API_KEY` | API key |
| `--openai-base-url URL` | `https://api.openai.com/v1` | Custom endpoint |
| `--openai-model MODEL` | `gpt-4o` | Model for AI extraction |

---

## Advanced Options (via `--options` JSON)

### File Processing
```json
{
  "read_executable": true,
  "blacklist_files": [".gitignore"]
}
```

### GitHub
```json
{
  "github_token": "ghp_...",
  "gitignore": true
}
```

### Google Drive
```json
{
  "max_depth": 3,
  "service_account_file": "/path/to/creds.json"
}
```

### Cookies
```json
{
  "cookies": {
    "browser_type": "chrome",
    "show": "format"
  }
}
```

---

## Examples for AI Agents

**Task: "Analyze this Python project"**
```bash
thepipe ./src --include_patterns "*.py" --options '{"code_relations": "auto"}' -f
```

**Task: "Extract text from this PDF"**
```bash
thepipe document.pdf -f text
```

**Task: "Get data from this webpage"**
```bash
thepipe https://example.com/article -f
```

**Task: "Query this database"**
```bash
thepipe data.db --db "SELECT * FROM users LIMIT 10" -f json
```

**Task: "Transcribe this video"**
```bash
thepipe https://youtube.com/watch?v=abc123 --text_only -f
```

**Task: "Clone and analyze GitHub repo"**
```bash
thepipe https://github.com/user/repo --include_patterns "*.py" --options '{"code_relations": "map"}' -f
```

---

## Registration

Self-register thepipe with AI platforms:
```bash
thepipe --register agent   # Antigravity/Gemini
thepipe --register code    # Claude Code
thepipe --register help    # Show documentation
thepipe --register         # Output for manual copy-paste
```
"""


def register_stdout() -> str:
    """Generate markdown for manual copy-paste into chat interfaces"""
    return f"""# thepipe Installation Instructions

Copy the content below and paste it into your AI assistant's chat interface (ChatGPT, Claude.ai, Gemini, etc.) to enable thepipe capabilities.

---

{INSTRUCTION_TEMPLATE}

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
    
    # Get the actual thepipe command path
    thepipe_cmd = _get_thepipe_path()
    
    # Build skill content with proper frontmatter
    instructions = INSTRUCTION_TEMPLATE.replace("{thepipe_cmd}", thepipe_cmd)
    
    content = f"""---
name: thepipe
description: Extract data from files, URLs, databases. Use code_relations for code analysis with 90%+ token savings.
version: 1.0.0
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
    # Get the actual thepipe command path
    thepipe_cmd = _get_thepipe_path()
    
    # Use YAML frontmatter format like other Antigravity workflows
    # Inject the actual command path into the template
    instructions = INSTRUCTION_TEMPLATE.replace("{thepipe_cmd}", thepipe_cmd)
    workflow_content = f"""---
description: Extract data from files, URLs, databases using thepipe. Use when asked to pipe, pipe in, or extract content from any source.
---

{instructions}
"""
    workflow_file.write_text(workflow_content)
    
    # Create or update AGENTS.md (standard for AI agent discovery)
    agents_md = base_dir / "AGENTS.md"
    agents_md_updated = None
    
    if agents_md.exists():
        existing_content = agents_md.read_text()
        if "thepipe" not in existing_content:
            # Append thepipe section
            new_content = f"""{content}

## thepipe

Data extraction and document processing tool. Use for extracting content from files, URLs, and databases.

**Installation**: `pip install thepipe-api`

**Basic usage**: `thepipe <source> [-f md|text|json]`

See `.agent/workflows/thepipe.md` for detailed documentation.
"""
            agents_md.write_text(new_content)
            agents_md_updated = agents_md
    else:
        # Create new AGENTS.md with thepipe section
        agents_md_content = """# AGENTS.md

This file documents AI-callable tools available in this repository.

## thepipe

Data extraction and document processing tool. Use for extracting content from files, URLs, and databases.

**Installation**: `pip install thepipe-api`

**Basic usage**: `thepipe <source> [-f md|text|json]`

See `.agent/workflows/thepipe.md` for detailed documentation.
"""
        agents_md.write_text(agents_md_content)
        agents_md_updated = agents_md
    
    # Update auto-execute allowlist
    _add_to_allowlist(base_dir)
    
    # Add AGENTS.md to gitignore (user-local summary, not committed)
    # Note: .agent/workflows/ should be committed so agents can read it
    _add_to_gitignore(base_dir, "AGENTS.md")
    
    return workflow_file, agents_md_updated


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

3. **Manual/Chat**: Run `thepipe --register`
   - Outputs markdown to copy-paste into chat interfaces

4. **MCP Server**: Run `thepipe --register mcp` (coming soon)
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
