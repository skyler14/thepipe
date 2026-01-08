---
description: Extract data from files, URLs, databases using thepipe. Use when asked to pipe, pipe in, or extract content from any source.
---

# Tool: thepipe
**Description**: Extract clean markdown, text, images, and structured data from any file, URL, or database.

> **FOR CODE/REPOS: ALWAYS use `--options '{"code_relations": "auto"}'`**
> This gives 90%+ token savings while preserving full code context via intelligent digests.

## Installation

**From PyPI (Recommended):**
```bash
pip install thepipe-api
```

**From Source (Development):**
```bash
git clone https://github.com/emcf/thepipe
cd thepipe
pip install -r requirements.txt
pip install -e .
```

## Invocation

**After PyPI install:**
```bash
thepipe
```

**If installed in conda environment:**
```bash
/opt/anaconda3/envs/thepipe/bin/thepipe
# or activate the environment first:
conda activate thepipe
thepipe
```

---

## Code Analysis (USE THIS FOR PROGRAMMING TASKS)

**When working with code directories or GitHub repos, ALWAYS enable code analysis:**

```bash
# Recommended for any programming project
thepipe ./repo --options '{"code_relations": "auto"}' -f

# GitHub repo with code analysis
thepipe https://github.com/user/repo --include_patterns "*.py" --options '{"code_relations": "auto"}' -f
```

**Why use code_relations?**
- **90%+ token savings** - digests preserve structure without full code
- **Dependency mapping** - understands imports and file relationships
- **Semantic tagging** - identifies auth, database, API, testing code
- **Intelligent context** - provides exactly what LLMs need to understand codebases

**Modes:**
| Mode | When to Use |
|------|-------------|
| `auto` | **Default choice** - picks optimal strategy |
| `map` | Large repos - all files as digests |
| `mapnn` | Focused work - primary files full, neighbors as digests |
| `mapall` | Medium repos - primary full, rest as digests |

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
| `--include_regex ".*\.py$"` | Regex pattern |

**Examples:**
```bash
# Only Python files
thepipe ./src --include_patterns "*.py"

# Multiple patterns
thepipe ./project --include_patterns "*.py" "*.js" "*.tsx"

# Regex (alternative)
thepipe ./src --include_regex ".*\.(py|js)$"
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

