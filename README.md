<div align="center">
  <a href="https://thepi.pe/">
    <img src="https://rpnutzemutbrumczwvue.supabase.co/storage/v1/object/public/assets/pipeline_small%20(1).png" alt="Pipeline Illustration" style="width:96px; height:72px; vertical-align:middle;">
    <h1>thepi.pe</h1>
  </a>
  <p><strong>Extract clean data from anything → Feed it to any LLM</strong></p>
  <a>
    <img src="https://github.com/emcf/thepipe/actions/workflows/python-ci.yml/badge.svg" alt="python-gh-action">
  </a>
    <a href="https://codecov.io/gh/emcf/thepipe">
    <img src="https://codecov.io/gh/emcf/thepipe/graph/badge.svg?token=OE7CUEFUL9" alt="codecov">
  </a>
  <a href="https://raw.githubusercontent.com/emcf/thepipe/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT license">
  </a>
  <a href="https://www.pepy.tech/projects/thepipe-api">
    <img src="https://static.pepy.tech/badge/thepipe-api" alt="PyPI">
  </a>
</div>

---

## What is thepipe? 

**thepipe** extracts clean markdown, images, and structured data from complex sources — PDFs, URLs, codebases, databases, and more. It works out-of-the-box with any LLM, VLM, or RAG pipeline.

### Key Features

| Feature | Description |
|---------|-------------|
| 📄 **Universal Extraction** | PDFs, DOCX, PPTX, images, audio, video, Jupyter notebooks, spreadsheets |
| 🌐 **Web & Cloud** | URLs, GitHub repos, YouTube transcription, Google Drive |
| 💾 **Databases** | PostgreSQL, MySQL, MariaDB, SQLite, DuckDB, JDBC URLs |
| 📊 **Data Formats** | Parquet, ORC, Feather/Arrow, CSV, JSONL, Excel |
| 🔍 **Code Analysis** | 90%+ token savings with intelligent digests & dependency mapping |
| 🤖 **Agent Mode** | Seamlessly integrate with AI coding assistants via named pipes |

---

## Quick Start

```bash
pip install thepipe-api
```

### Basic Usage

```python
from thepipe.scraper import scrape_file
from thepipe.core import chunks_to_messages

# Extract from any source
chunks = scrape_file("document.pdf")

# Ready for any LLM
messages = chunks_to_messages(chunks)
```

### CLI Usage

```bash
# Scrape a file
thepipe document.pdf -f

# Scrape a URL
thepipe https://example.com -f

# Scrape a codebase with intelligent analysis
thepipe ./my-project --options '{"code_relations": "auto"}' -f

# Query a database
thepipe "postgresql://user:pass@host/db" --db "SELECT * FROM users" -f
```

---

## 🔥 Code Analysis (90%+ Token Savings)

For codebases, use `code_relations` mode for intelligent digests:

```bash
thepipe ./repo --options '{"code_relations": "auto"}' -f
```

**Benefits:**
- 🔗 Dependency mapping across imports
- 🏷️ Semantic tagging (auth, database, API, testing)
- 📊 Full codebase context in minimal tokens
- 🌍 Supports Python, JS/TS, Dart, Swift, Kotlin, Ruby, Go, Rust, C/C++, Java, +155 more

### Persistent Code Graph Sidecar

For deeper graph queries, `code_relations: "graph"` can use a pinned
`codebase-memory-mcp` sidecar and projects the native SQLite graph back into
thepipe chunks plus `code-relations/v2` JSON:

```bash
thepipe ./repo --options '{"code_relations": "graph", "codegraph_binary": "/path/to/codebase-memory-mcp"}' -f json
```

To install from a release archive without keeping raw generated grammar source
in this repo, pass a local archive and checksum:

```bash
thepipe ./repo --options '{"code_relations": "graph", "codegraph_archive": "/path/to/codegraph.tar.gz", "codegraph_sha256": "SHA256"}' -f json
```

Graph mode stores repo-local databases under `.thepipe/codegraph/cache/`, adds
that cache to `.git/info/exclude` by default, and records a pointer in the
thepipe master registry. The sidecar binary is the compiled byproduct; the
large raw grammar checkout used to build it is not required at runtime.

Once a graph exists, use `codegraph_action` for bounded graph access without
emitting the full payload:

```bash
thepipe ./repo --options '{"code_relations": "graph", "codegraph_action": "entities", "codegraph_query": "main"}' -f json
thepipe ./repo --options '{"code_relations": "graph", "codegraph_action": "neighbors", "codegraph_entity": "main", "codegraph_direction": "outbound"}' -f json
thepipe ./repo --options '{"code_relations": "graph", "codegraph_library": "/path/to/libthepipe_codegraph.dylib", "codegraph_action": "search_graph", "codegraph_query": "main"}' -f json
thepipe ./repo --options '{"code_relations": "graph", "codegraph_library": "/path/to/libthepipe_codegraph.dylib", "codegraph_action": "query_graph", "codegraph_cypher": "MATCH (n) RETURN n LIMIT 20"}' -f json
```

Native graph actions available through the sidecar or shared library are
`index_repository`, `search_graph`, `query_graph`, `trace_path`,
`get_code_snippet`, `get_graph_schema`, `get_architecture`, `search_code`,
`list_projects`, `index_status`, `delete_project`, `detect_changes`,
`manage_adr`, and `ingest_traces`. SQL-facing work belongs to database mode;
codegraph mode uses graph actions and Cypher for graph-native queries.

Neighbor actions reject ambiguous short entity names. Use a qualified name when
multiple symbols share one name. They also suppress edges below
`codegraph_min_confidence` (default `0.5`) and stop traversing through nodes above
`codegraph_max_transit_degree` (default `25`). Suppressed-edge counts and pruned
hubs remain visible in the response. Set either option to `null` to disable that
filter.

Shared-library builds use the same graph contract through
`codegraph_library` or `codegraph_library_archive` plus
`codegraph_library_sha256`; the sidecar remains the portable fallback.

---

## 💾 Database Support

### Connection Formats

```bash
# PostgreSQL
thepipe "postgresql://user:pass@host:5432/db" --db "SELECT * FROM table"

# MySQL / MariaDB
thepipe "mysql://user:pass@host:3306/db" --db "SELECT * FROM table"

# JDBC URLs (auto-converted)
thepipe "jdbc:mysql://host:3306/db" --db "SELECT * FROM table"

# SQLite
thepipe "sqlite:///path/to/database.db" --db "SELECT * FROM table"

# DuckDB
thepipe "duckdb:///analytics.duckdb" --db "SELECT * FROM table"
```

### Data File Formats

| Format | Extensions | Backend |
|--------|------------|---------|
| Parquet | `.parquet`, `.parq` | DuckDB |
| ORC | `.orc` | DuckDB |
| Feather/Arrow | `.feather`, `.arrow`, `.ipc` | DuckDB |
| JSON Lines | `.jsonl`, `.ndjson` | DuckDB |
| CSV | `.csv` | DuckDB |
| Excel | `.xlsx`, `.xls` | Pandas → DuckDB |

```bash
# Query data files directly
thepipe data.parquet --db "SELECT * FROM parquet_data LIMIT 10"
thepipe logs.jsonl --db "SELECT * FROM jsonl_data WHERE level = 'error'"
```

---

## 🔌 Named Pipe (FIFO) Input

thepipe accepts named pipes as input sources — useful for streaming data:

```bash
# Create a FIFO
mkfifo /tmp/my_pipe

# thepipe reads from it (blocks until data arrives)
thepipe /tmp/my_pipe -f &

# Write data to the pipe
echo '{"key": "value"}' > /tmp/my_pipe
```

Content type is auto-detected via [Magika](https://github.com/google/magika).

---

## 🤖 Agent Mode (LLM Inference Delegation)

When running inside an AI coding assistant, thepipe can delegate LLM calls back to the host agent:

```bash
thepipe document.pdf --options '{"llm_provider": "agent"}' -f
```

**How it works:**
1. thepipe creates named pipes in `/tmp/thepipe_pipes/`
2. Outputs query with `<<<THEPIPE_LLM_QUERY>>>` markers
3. Agent reads query, executes LLM call, writes response
4. thepipe continues seamlessly

This avoids double API charges when running inside Antigravity, Claude Code, or similar tools.

---

## Supported Sources

| Source | Input Types | Multimodal |
|--------|-------------|------------|
| **Documents** | `.pdf`, `.docx`, `.pptx`, `.txt`, `.md` | ✔️ |
| **Spreadsheets** | `.csv`, `.xlsx`, `.xls` | ❌ |
| **Images** | `.jpg`, `.png`, `.gif` | ✔️ |
| **Audio/Video** | `.mp3`, `.wav`, `.mp4`, `.mov` | ✔️ |
| **Code** | `.py`, `.js`, `.ts`, `.java`, +155 more | ❌ |
| **Notebooks** | `.ipynb` | ✔️ |
| **Archives** | `.zip` | ✔️ |
| **Web** | `http://`, `https://` | ✔️ |
| **GitHub** | `github.com/user/repo` | ✔️ |
| **YouTube** | `youtube.com/watch?v=...` | ✔️ |
| **Databases** | SQL connection strings | ❌ |
| **Data Files** | `.parquet`, `.orc`, `.feather`, `.jsonl` | ❌ |
| **Named Pipes** | FIFOs (auto-detected) | ✔️ |

---

## LLM Integration

### OpenAI

```python
from openai import OpenAI
from thepipe.scraper import scrape_file
from thepipe.core import chunks_to_messages

client = OpenAI()
chunks = scrape_file("document.pdf")
messages = [{"role": "user", "content": "Summarize this document:"}]
messages += chunks_to_messages(chunks)

response = client.chat.completions.create(model="gpt-4o", messages=messages)
```

### LlamaIndex

```python
from thepipe.scraper import scrape_file

chunks = scrape_file("document.pdf")
documents = [chunk.to_llamaindex() for chunk in chunks]
```

---

## Environment Variables

```bash
# OpenAI / VLM
export OPENAI_API_KEY=sk-...
export DEFAULT_AI_MODEL=gpt-4o

# GitHub (for repo scraping)
export GITHUB_TOKEN=ghp_...

# Audio transcription limit (seconds)
export MAX_WHISPER_DURATION=600

# Image hosting
export HOST_IMAGES=true
```

---

## Installation Options

```bash
# Basic install
pip install thepipe-api

# Full install (video, audio, web scraping)
apt-get install -y ffmpeg
pip install thepipe-api[full]
python -m playwright install --with-deps chromium
```

---

## AI Registration

thepipe can self-register with your AI coding assistant, enabling it to call the tool directly.

```bash
# Register with Claude Code
thepipe --register code

# Register with Google Antigravity
thepipe --register agent

# Show all registration options
thepipe --register help

# Generate manual instructions for any chat interface (ChatGPT, Claude.ai, etc.)
thepipe --register
```

---

## Contributing

```bash
git clone https://github.com/emcf/thepipe.git
cd thepipe
pip install -r requirements.txt
python -m pytest tests/
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

## Sponsors

Support thepipe development: [Become a sponsor](mailto:emmett@thepi.pe)

<a href="https://cal.com/emmett-mcf/30min"><img alt="Book us with Cal.com" src="https://cal.com/book-with-cal-dark.svg" /></a>
