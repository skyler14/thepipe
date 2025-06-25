from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union, cast
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from io import BytesIO, StringIO
import math
import re
import fnmatch
import os
import tempfile
from urllib.parse import urlparse
import zipfile
from PIL import Image
import requests
import json
from .drive_utils import extract_drive_id, process_drive_content
from .file_utils import detect_source_type, find_audio_file, find_subtitle_files, get_filtered_files
from .media_utils import MAX_WHISPER_DURATION, VIDEO_PLATFORMS, clean_subtitles, format_timestamp, get_images_from_markdown
from .web_utils import (
    SCRAPING_PROMPT,
    DRIVE_DOMAINS, GIT_DOMAINS, TWITTER_DOMAINS,
    extract_page_content, matches_domain, normalize_url
)
from .enums import YouTubeEnum
from .core import (
    HOST_IMAGES,
    Chunk,
    make_image_url,
    DEFAULT_AI_MODEL,
    HOST_URL,
    THEPIPE_API_KEY,
)
from .chunker import (
    chunk_by_page,
    chunk_by_document,
    chunk_by_section,
    chunk_semantic,
    chunk_by_keywords,
    chunk_by_length,
    chunk_agentic,
)
import tempfile
import mimetypes
import dotenv
from magika import Magika
import markdownify
import fitz
from openai import OpenAI
from openai.types.chat.chat_completion_message_param import ChatCompletionMessageParam

dotenv.load_dotenv()

FOLDERS_TO_IGNORE = {
    "*node_modules*",
    "*.git*",
    "*venv*",
    "*.vscode*",
    "*pycache*",
    "*.ipynb_checkpoints",
}
FILES_TO_IGNORE = {
    ".gitignore",
    "*.bin",
    # Python compiled files
    "*.pyc",
    "*.pyo",
    "*.pyd",
    # Shared libraries and binaries
    "*.so",
    "*.dll",
    "*.exe",
    # Archives and packages
    "*.tar",
    "*.tar.gz",
    "*.egg-info",
    "package-lock.json",
    "package.json",
    # Lock, log, and metadata files
    "*.lock",
    "*.log",
    "Pipfile.lock",
    "requirements.lock",
    "*.exe",
    "*.dll",
    ".DS_Store",
    "Thumbs.db",
}
GITHUB_TOKEN: Optional[str] = os.getenv("GITHUB_TOKEN", None)
USER_AGENT_STRING: str = os.getenv(
    "USER_AGENT_STRING",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
)
FILESIZE_LIMIT_MB = int(os.getenv("FILESIZE_LIMIT_MB", 50))

# Global variables for lazy loading
yt_dlp = None

def initialize_video_processing():
    """Initialize video processing libraries."""
    global yt_dlp
    if yt_dlp is None:
        try:
            import yt_dlp
        except ImportError:
            raise ImportError("yt-dlp library not found. Please install it with: pip install yt-dlp")

def detect_source_mimetype(source: str) -> str:
    """Detect the MIME type of a source file."""
    # try to detect the file type by its extension
    _, extension = os.path.splitext(source)
    if extension:
        if extension == ".ipynb":
            # special case for notebooks, mimetypes is not familiar
            return "application/x-ipynb+json"
        guessed_mimetype, _ = mimetypes.guess_type(source)
        if guessed_mimetype:
            return guessed_mimetype
    # if that fails, try AI detection with Magika
    magika = Magika()
    with open(source, "rb") as file:
        result = magika.identify_bytes(file.read())
    mimetype = result.output.mime_type
    return mimetype

def scrape_file(
    filepath: str,
    ai_extraction: bool = False,
    text_only: bool = False,
    verbose: bool = False,
    chunking_method: Optional[Callable[[List[Chunk]], List[Chunk]]] = chunk_by_page,
    ai_model: Optional[str] = DEFAULT_AI_MODEL,
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
    include_input_images: bool = True,
    include_output_images: bool = True,
) -> List[Chunk]:
    """
    Scrapes a file and returns a list of Chunk objects containing the text and images extracted from the file.

    Parameters
    ----------
    filepath : str
        The path to the file to scrape.
    ai_extraction : bool, optional
        If ``True``, uses AI extraction for enhanced processing.
    text_only : bool, optional
        If ``True``, only extracts text content.
    verbose : bool, optional
        If ``True``, prints verbose output.
    chunking_method : Callable, optional
        A function to chunk the scraped content. Defaults to chunk_by_page.
    ai_model : str, optional
        The AI model name to use for processing.
    options : Dict, optional
        Additional options for processing.
    openai_client : OpenAI, optional
        An OpenAI client instance for LLM processing. If None, uses local processing.
    include_input_images : bool, optional
        If ``True``, includes input images in LLM processing.
    include_output_images : bool, optional
        If ``True``, includes output images in the returned chunks.
    
    Returns
    -------
    List[Chunk]
        A list of Chunk objects containing the scraped content.
    """
    # Always process locally - API logic removed in favor of openai_client approach
    scraped_chunks = []
    source_mimetype = detect_source_mimetype(filepath)
    if source_mimetype is None:
        if verbose:
            print(f"[thepipe] Unsupported source type: {filepath}")
        return scraped_chunks
        
    if verbose:
        print(f"[thepipe] Scraping {source_mimetype}: {filepath}...")
        
    # Call the appropriate scraper based on file type
    if source_mimetype == "application/pdf":
        scraped_chunks = scrape_pdf(
            file_path=filepath,
            ai_extraction=ai_extraction,
            text_only=text_only,
            verbose=verbose,
            ai_model=ai_model,
            options=options,
            openai_client=openai_client,
            include_input_images=include_input_images,
            include_output_images=include_output_images,
        )
    elif source_mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        scraped_chunks = scrape_docx(
            file_path=filepath, 
            verbose=verbose, 
            include_output_images=include_output_images
        )
    elif source_mimetype == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        scraped_chunks = scrape_pptx(
            file_path=filepath, 
            verbose=verbose, 
            include_output_images=include_output_images
        )
    elif source_mimetype.startswith("image/"):
        scraped_chunks = scrape_image(file_path=filepath, text_only=text_only)
    elif (
        source_mimetype.startswith("application/vnd.ms-excel")
        or source_mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ):
        scraped_chunks = scrape_spreadsheet(file_path=filepath, source_type=source_mimetype)
    elif source_mimetype == "application/x-ipynb+json":
        scraped_chunks = scrape_ipynb(
            file_path=filepath, 
            verbose=verbose, 
            include_output_images=include_output_images
        )
    elif source_mimetype == "application/zip" or source_mimetype == "application/x-zip-compressed":
        scraped_chunks = scrape_zip(
            file_path=filepath,
            verbose=verbose,
            ai_extraction=ai_extraction,
            text_only=text_only,
            options=options,
            openai_client=openai_client,
            include_input_images=include_input_images,
            include_output_images=include_output_images,
        )
    elif source_mimetype.startswith("video/"):
        scraped_chunks = scrape_video(
            file_path=filepath, 
            verbose=verbose, 
            include_output_images=include_output_images,
            options=options
        )
    elif source_mimetype.startswith("audio/"):
        scraped_chunks = scrape_audio(file_path=filepath, verbose=verbose, options=options)
    elif source_mimetype.startswith("text/html"):
        scraped_chunks = scrape_html(
            file_path=filepath, 
            verbose=verbose, 
            include_output_images=include_output_images
        )
    elif source_mimetype.startswith("text/"):
        scraped_chunks = scrape_plaintext(file_path=filepath)
    else:
        try:
            scraped_chunks = scrape_plaintext(file_path=filepath)
        except Exception as e:
            if verbose:
                print(f"[thepipe] Error extracting from {filepath}: {e}")
                
    if verbose:
        if scraped_chunks:
            print(f"[thepipe] Extracted from {filepath}")
        else:
            print(f"[thepipe] No content extracted from {filepath}")
            
    if chunking_method:
        scraped_chunks = chunking_method(scraped_chunks)
        
    return scraped_chunks

def scrape_html(
    file_path: str,
    verbose: bool = False,
    include_output_images: bool = True,
) -> List[Chunk]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
        html_content = file.read()
    markdown_content = markdownify.markdownify(html_content, heading_style="ATX")
    images = get_images_from_markdown(html_content) if include_output_images else []
    return [Chunk(path=file_path, text=markdown_content, images=images)]

def scrape_plaintext(file_path: str) -> List[Chunk]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
        text = file.read()
    return [Chunk(path=file_path, text=text)]

def scrape_directory(
    dir_path: str,
    include_regex: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    verbose: bool = False,
    ai_extraction: bool = False,
    text_only: bool = False,
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
    include_input_images: bool = True,
    include_output_images: bool = True,
) -> List[Chunk]:
    """
    Scrape all files in a directory.
    
    Parameters
    ----------
    dir_path : str
        Path to the directory to scrape
    include_regex : str, optional
        Regex pattern to match file paths
    include_patterns : List[str], optional
        List of glob patterns to match files
    verbose : bool, optional
        Enable verbose logging
    ai_extraction : bool, optional
        Use AI extraction for enhanced processing
    text_only : bool, optional
        Extract only text content
    local : bool, optional
        Process locally instead of using API
    options : Dict, optional
        Additional processing options
    openai_client : OpenAI, optional
        OpenAI client for LLM processing
    include_input_images : bool, optional
        Include input images in LLM processing
    include_output_images : bool, optional
        Include output images in returned chunks
        
    Returns
    -------
    List[Chunk]
        List of extracted chunks
    """
    extraction = []
    
    # Process options
    options = options or {}
    
    # Get blacklist files list (if any)
    blacklist_files = options.get('blacklist_files', [])
    
    # Get filtered file list
    all_files = get_filtered_files(
        dir_path=dir_path,
        include_regex=include_regex,
        include_patterns=include_patterns,
        blacklist_files=blacklist_files,
        verbose=verbose
    )
    
    if verbose:
        print(f"[thepipe] Processing {len(all_files)} files in {dir_path}")
    
    with ThreadPoolExecutor() as executor:
        results = executor.map(
            lambda file_path: scrape_file(
                filepath=file_path,
                ai_extraction=ai_extraction,
                text_only=text_only,
                verbose=verbose,
                options=options,
                openai_client=openai_client,
                include_input_images=include_input_images,
                include_output_images=include_output_images,
            ),
            all_files,
        )
        for result in results:
            extraction.extend(result)
    
    return extraction

def scrape_zip(
    file_path: str,
    include_regex: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    verbose: bool = False,
    ai_extraction: bool = False,
    text_only: bool = False,
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
    include_input_images: bool = True,
    include_output_images: bool = True,
) -> List[Chunk]:
    chunks = []
    with tempfile.TemporaryDirectory() as temp_dir:
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(temp_dir)
        chunks = scrape_directory(
            dir_path=temp_dir,
            include_regex=include_regex,
            include_patterns=include_patterns,
            verbose=verbose,
            ai_extraction=ai_extraction,
            text_only=text_only,
            options=options,
            openai_client=openai_client,
            include_input_images=include_input_images,
            include_output_images=include_output_images,
        )
    return chunks

def scrape_pdf(
    file_path: str,
    ai_extraction: Optional[bool] = False,
    text_only: Optional[bool] = False,
    ai_model: Optional[str] = DEFAULT_AI_MODEL,
    verbose: Optional[bool] = False,
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
    include_input_images: bool = True,
    include_output_images: bool = True,
    image_scale: float = 1.0,
) -> List[Chunk]:
    chunks: List[Chunk] = []
    MAX_PAGES = 128

    # Branch 1 – VLM path (AI extraction or OpenAI client supplied)
    if ai_extraction or openai_client is not None:
        from collections import OrderedDict
        import concurrent.futures

        if openai_client is None:
            # Create client from options or environment
            llm_config = options.get("llm_extractor", {}) if options else {}
            api_key = llm_config.get("api_key", os.environ.get("LLM_SERVER_API_KEY", os.environ.get("OPENAI_API_KEY")))
            api_base = llm_config.get("api_base", os.environ.get("LLM_SERVER_BASE_URL"))
            model = llm_config.get("model", ai_model or DEFAULT_AI_MODEL)
            
            # Configure OpenAI client
            client_args = {"api_key": api_key}
            if api_base:
                client_args["base_url"] = api_base
                
            openai_client = OpenAI(**client_args)

        with open(file_path, "rb") as fp:
            pdf_bytes = fp.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_pages = len(doc)

        if num_pages > MAX_PAGES:
            err = f"Error: PDF has {num_pages} pages (max is {MAX_PAGES} for AI extraction)."
            raise Exception(err)

        if verbose:
            print(
                f"[thepipe] Scraping PDF: {file_path} "
                f"({num_pages} pages) with model {ai_model}"
            )

        # Inner worker – processes one page
        def _process_page(page_num: int) -> Tuple[int, str, Optional[Image.Image]]:
            page = doc[page_num]
            text = page.get_text()  # type: ignore[attr-defined]

            # Build message for the LLM
            msg_content: List[Dict[str, Union[Dict[str, str], str]]] = [
                {
                    "type": "text",
                    "text": f"```\n{text}\n```\n{SCRAPING_PROMPT}",
                }
            ]

            image: Optional[Image.Image] = None
            if include_input_images or include_output_images:
                mat = fitz.Matrix(image_scale, image_scale)
                pix = page.get_pixmap(matrix=mat, alpha=False)  # type: ignore[attr-defined]
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                if include_input_images:
                    encoded = make_image_url(image, host_images=HOST_IMAGES)
                    msg_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": encoded, "detail": "high"},
                        }
                    )

            messages = cast(
                Iterable[ChatCompletionMessageParam],
                [{"role": "user", "content": msg_content}],
            )

            response = openai_client.chat.completions.create(
                model=ai_model or DEFAULT_AI_MODEL, messages=messages, temperature=0
            )

            llm_response = response.choices[0].message.content
            if not llm_response:
                raise RuntimeError("Empty LLM response.")

            llm_response = llm_response.strip()
            if llm_response.startswith("```markdown"):
                llm_response = llm_response[len("```markdown") :]
            elif llm_response.startswith("```"):
                llm_response = llm_response[len("```") :]
            if llm_response.endswith("```"):
                llm_response = llm_response[: -len("```")]

            return (
                page_num,
                llm_response,
                image if include_output_images else None,
            )

        # Parallel extraction
        max_workers = (os.cpu_count() or 1) * 2
        if verbose:
            print(f"[thepipe] Using {max_workers} threads for PDF extraction")

        page_results: OrderedDict[int, Tuple[str, Optional[Image.Image]]] = (
            OrderedDict()
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_process_page, p) for p in range(num_pages)]
            for fut in as_completed(futures):
                pg, txt, img = fut.result()
                page_results[pg] = (txt, img)

        for pg in sorted(page_results):
            txt, img = page_results[pg]
            chunks.append(Chunk(path=file_path, text=txt, images=[img] if img else []))

        return chunks

    # Branch 2 – no AI extraction – text-only offline mode
    try:
        import pymupdf4llm

        doc = fitz.open(file_path)
        md_reader = pymupdf4llm.helpers.pymupdf_rag.to_markdown(
            doc, page_chunks=True
        )
        for i, page in enumerate(doc):
            text = md_reader[i]["text"]
            # remove excessive newlines
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()
            
            images: List[Image.Image] = []
            if include_output_images:
                mat = fitz.Matrix(image_scale, image_scale)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                images.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
                
            chunks.append(Chunk(path=file_path, text=text, images=images))
        doc.close()
    except:
        # try with default pymupdf if pymupdf4llm fails
        doc = fitz.open(file_path)
        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text()
            
            images: List[Image.Image] = []
            if include_output_images:
                mat = fitz.Matrix(image_scale, image_scale)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                images.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
                
            chunks.append(Chunk(path=file_path, text=text, images=images))
        doc.close()
    return chunks

def scrape_image(file_path: str, text_only: bool = False) -> List[Chunk]:
    import pytesseract

    img = Image.open(file_path)
    img.load()  # needed to close the file
    chunks = []
    if text_only:
        text = pytesseract.image_to_string(img)
        chunks.append(Chunk(path=file_path, text=text))
    else:
        chunks.append(Chunk(path=file_path, images=[img]))
    return chunks

def scrape_spreadsheet(file_path: str, source_type: str) -> List[Chunk]:
    import pandas as pd

    if source_type == "application/vnd.ms-excel":
        df = pd.read_csv(file_path)
    elif (
        source_type
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ):
        df = pd.read_excel(file_path)
    else:
        raise ValueError("Unsupported file format")
    dicts = df.to_dict(orient="records")
    chunks = []
    for i, item in enumerate(dicts):
        # format each row as json along with the row index
        item["row index"] = i
        item_json = json.dumps(item, indent=4)
        chunks.append(Chunk(path=file_path, text=item_json))
    return chunks

def scrape_url(
    url: str, 
    include_regex: Optional[str] = None,
    include_patterns: Optional[List[str]] = None, 
    text_only: bool = False, 
    ai_extraction: bool = False, 
    verbose: bool = False, 
    chunking_method: Optional[Callable] = chunk_by_page, 
    options: Optional[Dict[str, Any]] = None,
    openai_client: Optional[OpenAI] = None,
    model: str = DEFAULT_AI_MODEL,
    include_input_images: bool = True,
    include_output_images: bool = True,
) -> List[Chunk]:
    """Scrape content from a URL."""
    cookie_options = options.get('cookies', {}) if options else {}
    
    # Handle cookie test mode early
    if cookie_options.get('show') == "test" and cookie_options.get('to_terminal', True):
        from .cookie_utils import process_cookie_options
        cookie_info = process_cookie_options(url, [], cookie_options)
        if isinstance(cookie_info, str):
            print(cookie_info)
            return []
        return cookie_info

    # Process URL locally
    chunks = []
    try:
        if matches_domain(url, DRIVE_DOMAINS):
            if verbose:
                print("[thepipe] Detected Google Drive/Docs URL, using drive scraper")
            chunks = scrape_drive(url, text_only=text_only,
                                ai_extraction=ai_extraction,
                                verbose=verbose, options=options)
        elif matches_domain(url, VIDEO_PLATFORMS):
            chunks = scrape_youtube(url, text_only=text_only,
                                  verbose=verbose, options=options)
        elif matches_domain(url, TWITTER_DOMAINS):
            chunks = scrape_tweet(url=url, text_only=text_only,
                                verbose=verbose, options=options)
        elif matches_domain(url, GIT_DOMAINS):
            chunks = scrape_github(github_url=url, include_regex=include_regex,
                                 include_patterns=include_patterns,
                                 text_only=text_only, ai_extraction=ai_extraction,
                                 verbose=verbose, options=options)
        else:
            # Handle other content types
            parsed_url = urlparse(normalize_url(url))
            file_extension = os.path.splitext(parsed_url.path)[1].lower()
            if file_extension in ['.pdf', '.docx', '.txt', '.csv', '.xlsx']:
                with tempfile.TemporaryDirectory() as temp_dir:
                    file_path = os.path.join(temp_dir, os.path.basename(parsed_url.path))
                    response = requests.get(normalize_url(url))
                    if (FILESIZE_LIMIT_MB and 
                        int(response.headers.get('Content-Length', 0)) > FILESIZE_LIMIT_MB * 1024 * 1024):
                        raise ValueError(f"File size exceeds {FILESIZE_LIMIT_MB} MB limit.")
                    with open(file_path, 'wb') as file:
                        file.write(response.content)
                    chunks = scrape_file(
                        filepath=file_path, 
                        ai_extraction=ai_extraction,
                        text_only=text_only, 
                        verbose=verbose,
                        chunking_method=chunking_method,
                        options=options,
                        openai_client=openai_client,
                        include_input_images=include_input_images,
                        include_output_images=include_output_images,
                    )
            else:
                if openai_client and include_input_images:
                    chunk = parse_webpage_with_vlm(
                        url=url,
                        verbose=verbose,
                        model=model,
                        openai_client=openai_client,
                        include_output_images=include_output_images,
                    )
                else:
                    chunk = extract_page_content(
                        url=url, 
                        verbose=verbose, 
                        include_output_images=include_output_images,
                        options=options
                    )
                chunks = chunking_method([chunk])
                if not any(chunk.text for chunk in chunks) and not any(chunk.images for chunk in chunks):
                    raise ValueError("No content extracted from URL.")
                    
    except ImportError as e:
        raise ImportError(f"Required dependencies not found: {str(e)}")
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error processing URL: {str(e)}")
        raise

    # Process any cookie options if present
    if cookie_options:
        from .cookie_utils import process_cookie_options
        return process_cookie_options(url, chunks, cookie_options)
    return chunks

# Additional scraper functions from Version 1 with minimal changes

def scrape_drive(drive_url: str, text_only: bool = False, 
                ai_extraction: bool = False, verbose: bool = False, 
                options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """Process Google Drive URLs (both files and folders)."""
    if verbose:
        print(f"[thepipe] Processing Drive URL: {drive_url}")

    drive_id = extract_drive_id(drive_url)
    if not drive_id:
        raise ValueError(f"Could not extract Drive ID from URL: {drive_url}")
        
    return process_drive_content(
        drive_url=drive_url,
        drive_id=drive_id,
        text_only=text_only,
        ai_extraction=ai_extraction,
        verbose=verbose,
        options=options
    )

def parse_webpage_with_vlm(
    url: str,
    model: str = DEFAULT_AI_MODEL,
    verbose: Optional[bool] = False,
    openai_client: Optional[OpenAI] = None,
    include_output_images: bool = True,
) -> Chunk:
    if openai_client is None:
        raise ValueError("parse_webpage_with_vlm requires an openai_client argument.")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT_STRING)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        if not page.viewport_size:
            page.set_viewport_size({"width": 800, "height": 600})
        if not page.viewport_size:
            raise ValueError(
                "Failed to set viewport size after finding no viewport size"
            )
        viewport_height = page.viewport_size.get("height", 800)
        total_height = page.evaluate("document.body.scrollHeight")
        current_scroll_position = 0
        scrolldowns, max_scrolldowns = 0, 3
        images: List[Image.Image] = []

        while current_scroll_position < total_height and scrolldowns < max_scrolldowns:
            page.wait_for_timeout(200)  # wait for content to load
            screenshot = page.screenshot(full_page=False)
            img = Image.open(BytesIO(screenshot))
            images.append(img)

            current_scroll_position += viewport_height
            page.evaluate(f"window.scrollTo(0, {current_scroll_position})")
            scrolldowns += 1
            total_height = page.evaluate("document.body.scrollHeight")
            if verbose:
                print(
                    f"[thepipe] Scrolled to {current_scroll_position} of {total_height}. Waiting for content to load..."
                )

        browser.close()

    if images:
        # Vertically stack the images
        total_height = sum(img.height for img in images)
        max_width = max(img.width for img in images)
        stacked_image = Image.new("RGB", (max_width, total_height))
        y_offset = 0
        for img in images:
            stacked_image.paste(img, (0, y_offset))
            y_offset += img.height

        # Process the stacked image with VLM
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": make_image_url(
                                stacked_image, host_images=HOST_IMAGES
                            ),
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": SCRAPING_PROMPT},
                ],
            },
        ]
        response = openai_client.chat.completions.create(
            model=model,
            messages=cast(Iterable[ChatCompletionMessageParam], messages),
        )
        llm_response = response.choices[0].message.content
        if not llm_response:
            raise Exception(
                f"Failed to receive a message content from LLM Response: {response}"
            )
        if verbose:
            print(f"[thepipe] LLM response: {llm_response}")
        chunk = Chunk(
            path=url,
            text=llm_response,
            images=[stacked_image] if include_output_images else [],
        )
    else:
        raise ValueError("Model received 0 images from webpage")

    return chunk

def scrape_video(
    file_path: str,
    verbose: bool = False,
    include_output_images: bool = True,
    options: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    # Implementation would be similar to Version 1 but returning single text chunks
    return []

def scrape_youtube(
    url: str,
    text_only: Optional[Union[bool, str]] = None,
    verbose: bool = False,
    metadata_fields: Optional[List[YouTubeEnum]] = None,
    options: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    """Scrape content from a YouTube URL."""
    initialize_video_processing()
    if verbose:
        print("[thepipe] Initializing YouTube content extraction...")
    
    # Implementation similar to Version 1 but with single text chunks
    return []

def scrape_audio(file_path: str, verbose: bool = False, options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    import whisper

    model = whisper.load_model("base")
    if verbose:
        print(f"[thepipe] Transcribing audio file: {file_path}")
    result = model.transcribe(audio=file_path, verbose=verbose)
    # Format transcription with timestamps
    transcript = []
    for segment in result["segments"]:
        start = format_timestamp(segment["start"], 0, 0)
        end = format_timestamp(segment["end"], 0, 0)
        if segment["text"].strip():
            transcript.append(f"[{start} --> {end}]  {segment['text']}")
    # join the formatted transcription into a single string
    transcription_text = '\n'.join(transcript)
    if verbose:
        print(f"[thepipe] Transcription completed for {file_path}")
    return [Chunk(path=file_path, text=transcription_text)]

def scrape_github(
    github_url: str, include_regex: Optional[str] = None, include_patterns: Optional[List[str]] = None,
    text_only: bool = False, ai_extraction: bool = False, branch: str = "main", verbose: bool = False,
    options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """Scrape content from a GitHub repository with optional authentication."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Try unauthenticated clone first
        clone_result = os.system(f"git clone {github_url} {temp_dir} --quiet")
        
        # If clone fails and we have token options/env, try authenticated clone
        if clone_result != 0:
            # Check options first, then environment variable
            token = None
            if options:
                token = options.get('github_token') or options.get('github', {}).get('token')
            if not token:
                token = os.getenv('GITHUB_TOKEN')
                
            if token:
                if verbose:
                    print(f"[thepipe] Attempting authenticated clone...")
                auth_url = github_url.replace("https://", f"https://{token}@")
                clone_result = os.system(f"git clone {auth_url} {temp_dir} --quiet")
                if clone_result != 0:
                    return [Chunk(path=github_url, text=f"Failed to clone repository even with authentication: {github_url}")]
            else:
                return [Chunk(path=github_url, text=f"Repository requires authentication. Set GITHUB_TOKEN environment variable or provide token in options")]

        try:
            blacklist_files = options.get('blacklist_files', []) if options else []
            if options and options.get('gitignore', False):
                git_ignore_files = ['.gitignore', '.git/info/exclude']
                for git_file in git_ignore_files:
                    if os.path.exists(os.path.join(temp_dir, git_file)) and git_file not in blacklist_files:
                        blacklist_files.append(git_file)
                        if verbose:
                            print(f"[thepipe] Adding {git_file} to blacklist files")
            
            modified_options = (options or {}).copy()
            modified_options['blacklist_files'] = blacklist_files
            
            if verbose and blacklist_files:
                print(f"[thepipe] Using blacklist files: {blacklist_files}")
                
            return scrape_directory(
                dir_path=temp_dir,
                include_regex=include_regex,
                include_patterns=include_patterns,
                verbose=verbose,
                ai_extraction=ai_extraction,
                text_only=text_only,
                options=modified_options
            )
        except Exception as e:
            if verbose:
                print(f"[thepipe] Error processing repository contents: {str(e)}")
            return [Chunk(path=github_url, text=f"Error processing repository contents: {str(e)}")]

def scrape_docx(
    file_path: str,
    verbose: bool = False,
    include_output_images: bool = True,
) -> List[Chunk]:
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    import csv
    import io

    # helper function to iterate through blocks in the document
    def iter_block_items(parent):
        if parent.__class__.__name__ == "Document":
            parent_elm = parent.element.body
        elif parent.__class__.__name__ == "_Cell":
            parent_elm = parent._tc
        else:
            raise ValueError("Unsupported parent type")
        # iterate through each child element in the parent element
        for child in parent_elm.iterchildren():
            child_elem_class_name = child.__class__.__name__
            if verbose:
                print(f"[thepipe] Found element in docx: {child_elem_class_name}")
            if child_elem_class_name == "CT_P":
                yield Paragraph(child, parent)
            elif child_elem_class_name == "CT_Tbl":
                yield Table(child, parent)

    # helper function to read tables in the document
    def read_docx_tables(tab):
        vf = StringIO()
        writer = csv.writer(vf)
        for row in tab.rows:
            writer.writerow(cell.text for cell in row.cells)
        vf.seek(0)
        return vf.getvalue()

    # read the document
    document = Document(file_path)
    chunks = []
    image_counter = 0

    # Define namespaces
    nsmap = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }

    try:
        # scrape each block in the document to create chunks
        for block in iter_block_items(document):
            block_texts = []
            block_images = []
            if isinstance(block, Paragraph):
                block_texts.append(block.text)
                # "runs" are the smallest units in a paragraph
                for run in block.runs:
                    if "pic:pic" in run.element.xml and include_output_images:
                        # extract images from the paragraph
                        for pic in run.element.findall(".//pic:pic", nsmap):
                            cNvPr = pic.find(".//pic:cNvPr", nsmap)
                            name_attr = (
                                cNvPr.get("name")
                                if cNvPr is not None
                                else f"image_{image_counter}"
                            )
                            blip = pic.find(".//a:blip", nsmap)
                            if blip is not None:
                                embed_attr = blip.get(
                                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                                )
                                if embed_attr:
                                    image_part = document.part.related_parts[embed_attr]
                                    image_data = BytesIO(image_part._blob)
                                    image = Image.open(image_data)
                                    image.load()
                                    block_images.append(image)
                                    image_counter += 1
            elif isinstance(block, Table):
                table_text = read_docx_tables(block)
                block_texts.append(table_text)
            if block_texts or block_images:
                block_text = "\n".join(block_texts).strip()
                if block_text or block_images:
                    chunks.append(
                        Chunk(path=file_path, text=block_text, images=block_images)
                    )
    except Exception as e:
        raise ValueError(f"Error processing DOCX file {file_path}: {e}")
    return chunks

def scrape_pptx(
    file_path: str,
    verbose: bool = False,
    include_output_images: bool = True,
) -> List[Chunk]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.shapes.picture import Picture
    from pptx.shapes.autoshape import Shape as AutoShape

    prs = Presentation(file_path)
    chunks = []
    # iterate through each slide in the presentation
    for slide in prs.slides:
        slide_texts = []
        slide_images = []
        # iterate through each shape in the slide
        for shape in slide.shapes:
            if shape.has_text_frame:
                auto_shape = cast(AutoShape, shape)
                for paragraph in auto_shape.text_frame.paragraphs:
                    text = paragraph.text
                    if len(slide_texts) == 0:
                        text = "# " + text  # header for first text of a slide
                    slide_texts.append(text)
            # extract images from shapes
            if include_output_images and shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                pic = cast(Picture, shape)
                image_data = pic.image.blob
                image = Image.open(BytesIO(image_data))
                slide_images.append(image)
        # add slide to chunks if it has text or images
        if slide_texts or slide_images:
            text = "\n".join(slide_texts).strip()
            if not include_output_images:
                slide_images = []
            chunks.append(Chunk(path=file_path, text=text, images=slide_images))
    # return all chunks
    return chunks

def scrape_ipynb(
    file_path: str,
    verbose: bool = False,
    include_output_images: bool = True,
) -> List[Chunk]:
    with open(file_path, "r", encoding="utf-8") as file:
        notebook = json.load(file)
    chunks = []
    # parse cells in the notebook
    for cell in notebook["cells"]:
        texts = []
        images: List[Image.Image] = []
        cell_type = cell["cell_type"]
        # parse cell content based on type
        if verbose:
            print(f"[thepipe] Scraping cell {cell_type} from {file_path}")
        if cell_type == "markdown":
            text = "".join(cell["source"])
            if include_output_images:
                images = get_images_from_markdown(text)
            texts.append(text)
        elif cell_type == "code":
            source = "".join(cell["source"])
            texts.append(source)
            output_texts = []
            # code cells can have outputs
            if "outputs" in cell:
                for output in cell["outputs"]:
                    if (
                        include_output_images
                        and "data" in output
                        and "image/png" in output["data"]
                    ):
                        image_data = output["data"]["image/png"]
                        image = Image.open(BytesIO(base64.b64decode(image_data)))
                        images.append(image)
                    elif "data" in output and "text/plain" in output["data"]:
                        output_text = "".join(output["data"]["text/plain"])
                        output_texts.append(output_text)
            if output_texts:
                texts.extend(output_texts)
        elif cell_type == "raw":
            text = "".join(cell["source"])
            texts.append(text)
        if texts or images:
            text = "\n".join(texts).strip()
            chunks.append(Chunk(path=file_path, text=text, images=images))
    return chunks

def scrape_tweet(url: str, include_output_images: bool = True, verbose: bool = False, options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """
    Magic function from https://github.com/vercel/react-tweet/blob/main/packages/react-tweet/src/api/fetch-tweet.ts
    unofficial, could break at any time
    """

    def get_token(id: str) -> str:
        result = (float(id) / 1e15) * math.pi
        base_36_result = ""
        characters = "0123456789abcdefghijklmnopqrstuvwxyz"
        while result > 0:
            remainder = int(result % (6**2))
            base_36_result = characters[remainder] + base_36_result
            result = (result - remainder) // (6**2)
        base_36_result = re.sub(r"(0+|\.)", "", base_36_result)
        return base_36_result

    tweet_id = url.split("status/")[-1].split("?")[0]
    token = get_token(tweet_id)
    tweet_api_url = "https://cdn.syndication.twimg.com/tweet-result"
    params = {"id": tweet_id, "language": "en", "token": token}
    response = requests.get(tweet_api_url, params=params)
    if response.status_code != 200:
        raise ValueError(f"Failed to fetch tweet. Status code: {response.status_code}")
    tweet_data = response.json()
    # Extract tweet text
    tweet_text = tweet_data.get("text", "")
    # Extract images from tweet
    images: List[Image.Image] = []
    if include_output_images and "mediaDetails" in tweet_data:
        for media in tweet_data["mediaDetails"]:
            image_url = media.get("media_url_https")
            if image_url:
                image_response = requests.get(image_url)
                img = Image.open(BytesIO(image_response.content))
                images.append(img)
    # Create chunks for text and images
    chunk = Chunk(path=url, text=tweet_text, images=images)
    return [chunk]

def scrape_database(
    filepath: str,
    query: Optional[str] = None,
    db_type: Optional[str] = None,
    verbose: bool = False,
    options: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    """
    Scrape content from a database connection or file.
    """
    # Database operations are always local
    if verbose:
        print("[thepipe] Processing database locally")
    
    # Import database utilities
    from .database_utils import process_database
    
    # Determine mode from options
    mode = None
    if options:
        if options.get("schema_only"):
            mode = "schema"
        elif options.get("preview"):
            mode = "preview"
    
    # Process the database
    return process_database(
        connection_info=filepath,
        query=query,
        db_type=db_type,
        mode=mode,
        verbose=verbose,
        options=options
    )