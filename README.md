<div align="center">
  <a href="https://thepi.pe/">
    <img src="https://rpnutzemutbrumczwvue.supabase.co/storage/v1/object/public/assets/pipeline_small%20(1).png" alt="Pipeline Illustration" style="width:96px; height:72px; vertical-align:middle;">
    <h1>thepi.pe</h1>
  </a>
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
  <a href="https://thepi.pe/">
    <img src="https://img.shields.io/website?url=https%3A%2F%2Fthepipe-api.up.railway.app%2F&label=API%20status" alt="Website">
  </a>
</div>

### Extract clean data from tricky documents ⚡

thepi.pe is a package that can scrape clean markdown or accurately extract structured data from complex documents. It uses vision-language models (VLMs) under the hood, and works out-of-the-box with any LLM, VLM, or vector database. It can be used right away on a [hosted cloud](https://thepi.pe), or it can be run locally.

## Features 🌟

- Scrape clean markdown, tables, and images from any document or webpage
- Works out-of-the-box with LLMs, vector databases, and RAG frameworks
- AI-native filetype detection, layout analysis, and structured data extraction
- Accepts a wide range of sources, including PDFs, URLs, Word docs, Powerpoints, Python notebooks, GitHub repos, videos, audio, and more

## Get started in 5 minutes  🚀

thepi.pe can read a wide range of filetypes and web sources, so it requires a few dependencies. It also requires vision-language model inference for AI extraction features. For these reasons, we host an API that works out-of-the-box. For more detailed setup instructions, view the [docs](https://thepi.pe/docs-platform).

```bash
pip install thepipe-api
```

### Hosted API (Python)

You can get an API key by signing up for a free account at [thepi.pe](https://thepi.pe). It is completely free to try out. The, simply set the `THEPIPE_API_KEY` environment variable to your API key.

## Scrape Function

```python
from thepipe.scraper import scrape_file
from thepipe.core import chunks_to_messages
from openai import OpenAI

# scrape clean markdown
chunks = scrape_file(filepath="paper.pdf", ai_extraction=False)

# call LLM with scraped chunks
client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4o",
    messages=chunks_to_messages(chunks),
)
```

The output from thepi.pe is a list of chunks containing all content within the source document. These chunks can easily be converted to a prompt format that is compatible with any LLM or multimodal model with `thepipe.core.chunks_to_messages`, which gives the following format:
```json
[
  {
    "role": "user",
    "content": [
      {
        "type": "text",
        "text": "..."
      },
      {
        "type": "image_url",
        "image_url": {
          "url": "data:image/jpeg;base64,..."
        }
      }
    ]
  }
]
```

## Extract Function

The extract function allows you to extract structured data from documents. You can use it as follows:

```python
from thepipe.extract import extract_from_file

# Define your schema
schema = {
    "name": "string",
    "age": "int",
    "is_student": "bool"
}

# Extract data from the file
result = extract_from_file(
    file_path="document.pdf",
    schema=schema,
    ai_model="gpt-4o-mini",
    multiple_extractions=True
)

print(result)
```

### Local Installation (Python)

For a local installation, you can use the following command:

```bash
pip install thepipe-api[local]
```

You must have a local LLM server setup and running for AI extraction features. You can use any local LLM server that follows OpenAI format (such as [LiteLLM](https://github.com/BerriAI/litellm)) or a provider (such as [OpenRouter](https://openrouter.ai/) or [OpenAI](https://platform.openai.com/)). Next, set the `LLM_SERVER_BASE_URL` environment variable to your LLM server's endpoint URL and set `LLM_SERVER_API_KEY`. the `DEFAULT_AI_MODEL` environment variable can be set to your VLM of choice. For example, you would use `openai/gpt-4o-mini` if using OpenRouter or `gpt-4o-mini` if using OpenAI. 

For full functionality with media-rich sources, you will need to install the following dependencies:

```bash
apt-get update && apt-get install -y git ffmpeg tesseract-ocr
python -m playwright install --with-deps chromium
```

When using thepi.pe locally, be sure to append `local=True` to your function calls:

```python
chunks = scrape_url(url="https://example.com", local=True)
```

You can also use thepi.pe from the command line:
```bash
thepipe path/to/folder --include_regex .*\.tsx --local
```

## Supported File Types 📚

| Source              | Input types                                                    | Multimodal | Notes |
|--------------------------|----------------------------------------------------------------|---------------------|----------------------|
| Webpage                  | URLs starting with `http`, `https`, `ftp`                      | ✔️                  | Scrapes markdown, images, and tables from web pages. `ai_extraction` available for AI content extraction from the webpage's screenshot |
| PDF                      | `.pdf`                                                          | ✔️                  | Extracts page markdown and page images. `ai_extraction` available to use a VLM for complex or scanned documents |
| Word Document  | `.docx`                                                         | ✔️                  | Extracts text, tables, and images |
| PowerPoint     | `.pptx`                                                         | ✔️                  | Extracts text and images from slides |
| Video                    | `.mp4`, `.mov`, `.wmv`                                          | ✔️                  | Uses Whisper for transcription and extracts frames |
| Audio                    | `.mp3`, `.wav`                                                  | ✔️                  | Uses Whisper for transcription |
| Jupyter Notebook         | `.ipynb`                                                        | ✔️                  | Extracts markdown, code, outputs, and images |
| Spreadsheet              | `.csv`, `.xls`, `.xlsx`                                         | ❌                  | Converts each row to JSON format, including row index for each |
| Plaintext                | `.txt`, `.md`, `.rtf`, etc                                      | ❌                  | Simple text extraction |
| Image                    | `.jpg`, `.jpeg`, `.png`                                    | ✔️                  | Uses pytesseract for OCR in text-only mode |
| ZIP File                 | `.zip`                                                          | ✔️                  | Extracts and processes contained files |
| Directory                | any `path/to/folder`                                            | ✔️                  | Recursively processes all files in directory |
| YouTube Video (known issues)    | YouTube video URLs starting with `https://youtube.com` or `https://www.youtube.com`.  | ✔️   | Uses yt-dlp for video download and Whisper for transcription. For consistent extraction, a cookie can be passed via the --options flag as json mimicking the yt-dlp cookies standard to use the agent from your browser (local only for now) |
| Tweet                    | URLs starting with `https://twitter.com` or `https://x.com`    | ✔️                  | Uses unofficial API, may break unexpectedly |
| GitHub Repository        | GitHub repo URLs starting with `https://github.com` or `https://www.github.com` | ✔️       | Requires GITHUB_TOKEN environment variable |
| Google Drive        | Google Drive/Doc URL for doc, spreadsheet, presentation, or directory  | ✔️       | Only Public/shared by URL support currently |

## How it works 🛠️

thepi.pe uses computer vision models and heuristics to extract clean content from the source and process it for downstream use with [language models](https://en.wikipedia.org/wiki/Large_language_model), or [vision transformers](https://en.wikipedia.org/wiki/Vision_transformer). You can feed these messages directly into the model, or alternatively you can use `chunker.chunk_by_document`, `chunker.chunk_by_page`, `chunker.chunk_by_section`, `chunker.chunk_semantic` to chunk these messages for a vector database such as ChromaDB or a RAG framework. A chunk can be converted to LlamaIndex Document/ImageDocument with `.to_llamaindex`.

> ⚠️ **It is important to be mindful of your model's token limit.**
GPT-4o does not work with too many images in the prompt (see discussion [here](https://community.openai.com/t/gpt-4-vision-maximum-amount-of-images/573110/6)). To remedy this issue, either use an LLM with a larger context window, extract larger documents with `text_only=True`, or embed the chunks into vector database.

# Sponsors

<a href="https://cal.com/emmett-mcf/30min"><img alt="Book us with Cal.com" src="https://cal.com/book-with-cal-dark.svg" /></a>

Thank you to [Cal.com](https://cal.com/) for sponsoring this project.
