---
description: Extract data from files, URLs, databases using thepipe. Use when asked to pipe, pipe in, or extract content from any source.
---

# ENVIRONMENT INFO

> **CRITICAL**: Use these EXACT paths when invoking thepipe. Do NOT rely on shell PATH.

| Component | Absolute Path |
|-----------|---------------|
| **thepipe command** | `/opt/anaconda3/envs/thepipe/bin/thepipe` |
| **Python executable** | `/opt/anaconda3/envs/thepipe/bin/python` |

---

# thepipe

Data extraction for files, URLs, databases. 165+ language AST support.

## Code Analysis (use for any programming task)

```bash
/opt/anaconda3/envs/thepipe/bin/thepipe ./repo --options '{"code_relations": "auto"}' -f
# 90%+ token savings via intelligent digests
```

## Common Commands

```bash
# PDF/document
/opt/anaconda3/envs/thepipe/bin/thepipe document.pdf -f

# Directory with filter
/opt/anaconda3/envs/thepipe/bin/thepipe ./src --include_patterns "*.py" -f

# URL
/opt/anaconda3/envs/thepipe/bin/thepipe https://example.com -f

# GitHub repo with code analysis
/opt/anaconda3/envs/thepipe/bin/thepipe https://github.com/user/repo --options '{"code_relations": "auto"}' -f

# Video/audio text extraction
/opt/anaconda3/envs/thepipe/bin/thepipe video.mp4 --text_only -f
```

## Output Formats

```bash
/opt/anaconda3/envs/thepipe/bin/thepipe source -f        # markdown to stdout
/opt/anaconda3/envs/thepipe/bin/thepipe source -f text   # raw text
/opt/anaconda3/envs/thepipe/bin/thepipe source -f json   # JSON array
/opt/anaconda3/envs/thepipe/bin/thepipe source           # writes to outputs/prompt.txt
```

## Code Analysis Modes

```bash
# auto (default) - picks strategy based on repo size
/opt/anaconda3/envs/thepipe/bin/thepipe ./repo --options '{"code_relations": "auto"}' -f

# map - all files as digests (large repos)
/opt/anaconda3/envs/thepipe/bin/thepipe ./repo --options '{"code_relations": "map"}' -f

# mapnn - primary files full, neighbors as digests
/opt/anaconda3/envs/thepipe/bin/thepipe ./repo --include_patterns "src/*.py" --options '{"code_relations": "mapnn"}' -f

# limited - only matched files, no digests
/opt/anaconda3/envs/thepipe/bin/thepipe ./repo --include_patterns "*.py" --options '{"code_relations": "limited"}' -f
```

## Code Analysis Parameters (`--options`)

```json
{
  "code_relations": "mapnn",
  "code_n1": 3,
  "code_n2": 5,
  "code_nf": 100,
  "code_nt": 150000
}
```

- `code_n1`: Neighbor depth to include as digests in `mapnn`
- `code_n2`: Cutoff depth in `mapnn`
- `code_nf`: File-count threshold used by `auto`
- `code_nt`: Token threshold used by `auto`

## File Filtering

```bash
# Glob patterns (recommended)
/opt/anaconda3/envs/thepipe/bin/thepipe ./repo --include_patterns "*.py" "*.ts" -f

# Regex filter
/opt/anaconda3/envs/thepipe/bin/thepipe ./repo --include_regex ".*\.(py|ts)$" -f
```

## Text Extraction Modes

```bash
/opt/anaconda3/envs/thepipe/bin/thepipe video.mp4 --text_only             -f  # default
/opt/anaconda3/envs/thepipe/bin/thepipe video.mp4 --text_only transcribe  -f  # force local transcription
/opt/anaconda3/envs/thepipe/bin/thepipe video.mp4 --text_only ai          -f  # prefer AI-generated transcript
/opt/anaconda3/envs/thepipe/bin/thepipe video.mp4 --text_only uploaded    -f  # prefer uploaded captions
```

## Database Operations

```bash
# PostgreSQL query
/opt/anaconda3/envs/thepipe/bin/thepipe "postgresql://user:pass@host:5432/db" --db "SELECT * FROM users" -f

# MySQL query
/opt/anaconda3/envs/thepipe/bin/thepipe "mysql://user:pass@host:3306/db" --db "SELECT * FROM orders" -f

# SQLite file
/opt/anaconda3/envs/thepipe/bin/thepipe data.db --db "SELECT * FROM table" -f

# SQLite with path syntax
/opt/anaconda3/envs/thepipe/bin/thepipe "sqlite:///path/to/db.sqlite" --db "SELECT *" -f

# DuckDB
/opt/anaconda3/envs/thepipe/bin/thepipe "duckdb:///path/to/db.duckdb" --db "SELECT *" -f

# Show schema only (no query)
/opt/anaconda3/envs/thepipe/bin/thepipe "postgresql://host/db" --db -f

# Parquet file as database
/opt/anaconda3/envs/thepipe/bin/thepipe data.parquet --db "SELECT * FROM parquet_data WHERE col > 10" -f

# Parquet (recommended for local files)
# Parquet is the most native file-backed format for --db. Point thepipe directly
# at the .parquet file and query parquet_data, without wrapping in DuckDB.
/opt/anaconda3/envs/thepipe/bin/thepipe /path/to/data.parquet --db "SELECT * FROM parquet_data LIMIT 10" -f

# CSV file as database  
/opt/anaconda3/envs/thepipe/bin/thepipe data.csv --db "SELECT * FROM csv_data LIMIT 100" -f

# Excel file as database
/opt/anaconda3/envs/thepipe/bin/thepipe data.xlsx --db "SELECT * FROM excel_data" -f
```

## Database Connection Formats

- PostgreSQL: `postgresql://user:pass@host:5432/db`
- MySQL: `mysql://user:pass@host:3306/db`
- MariaDB: `mariadb://user:pass@host:3306/db`
- SQLite: `sqlite:///path/to/database.db`
- DuckDB: `duckdb:///path/to/database.duckdb`
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
/opt/anaconda3/envs/thepipe/bin/thepipe source --openai-api-key "$OPENAI_API_KEY" --openai-model gpt-4o -f
/opt/anaconda3/envs/thepipe/bin/thepipe source --openai-base-url https://api.openai.com/v1 -f
```

## Agent Mode (LLM Delegation)

When thepipe needs LLM inference inside an agent, use `--llm-provider agent`:

```bash
/opt/anaconda3/envs/thepipe/bin/thepipe data.pdf --extract '{"title": "str"}' --llm-provider agent
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
/opt/anaconda3/envs/thepipe/bin/thepipe --register agent

# Claude Code - creates .claude/skills/thepipe/SKILL.md
/opt/anaconda3/envs/thepipe/bin/thepipe --register code

# Stdout for copy-paste
/opt/anaconda3/envs/thepipe/bin/thepipe --register
```

After registration, agents can invoke thepipe using the absolute paths shown in the generated files.

