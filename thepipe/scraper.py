import base64
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from io import BytesIO
import io
import math
import re
from typing import List, Dict, Any, Callable, Optional, Tuple, Generator, Union
import glob
import os
import tempfile
from urllib.parse import urlparse
import zipfile
from PIL import Image
import requests
import json
from .drive_utils import extract_drive_id, process_drive_content
from .file_utils import detect_source_type, find_audio_file, find_subtitle_files, find_video_file
from .media_utils import MAX_WHISPER_DURATION, VIDEO_PLATFORMS, clean_subtitles, format_timestamp, get_images_from_markdown
from .web_utils import (
    SCRAPING_PROMPT,
    DRIVE_DOMAINS, GIT_DOMAINS, TWITTER_DOMAINS,
    extract_page_content, matches_domain, normalize_url
)
from .enums import YouTubeEnum
from .core import Chunk, HOST_URL, THEPIPE_API_KEY, HOST_IMAGES, make_image_url
from .chunker import chunk_by_page

import tempfile
import dotenv
import markdownify
dotenv.load_dotenv()

FOLDERS_TO_IGNORE = ['*node_modules.*', '.*venv.*', '.*\.git.*', '.*\.vscode.*', '.*pycache.*']
FILES_TO_IGNORE = ['package-lock.json', '.gitignore', '.*\.bin', '.*\.pyc', '.*\.pyo', '.*\.exe', '.*\.dll', '.*\.ipynb_checkpoints']
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", None)
FILESIZE_LIMIT_MB = os.getenv("FILESIZE_LIMIT_MB", 50)
DEFAULT_AI_MODEL = os.getenv("DEFAULT_AI_MODEL", "gpt-4o-mini")

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
                
def scrape_file(filepath: str, ai_extraction: bool = False, text_only: bool = False, verbose: bool = False, local: bool = False, chunking_method: Optional[Callable] = chunk_by_page, ai_model: Optional[str] = DEFAULT_AI_MODEL, options: Optional[Dict[str, Any]] = None) -> List[Chunk]:

    if not local:
        with open(filepath, "rb") as f:
            response = requests.post(
                url=f"{HOST_URL}/scrape",
                headers={"Authorization": f"Bearer {THEPIPE_API_KEY}"},
                files={"files": (os.path.basename(filepath), f)},
                data={
                    "text_only": str(text_only).lower(),
                    "ai_extraction": str(ai_extraction).lower(),
                    "chunking_method": 
                        chunking_method.__name__,
                    'options': json.dumps(options) if options else None if chunking_method else None
                    ,
                },
            )
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            # each line is its own JSON object
            if not line.strip():
                continue  # skip blank lines
            data = json.loads(line)
            # If the server sent an error for this chunk, handle it
            if "error" in data:
                raise ValueError(f"Error scraping: {data['error']}")

        chunks = []
        for line in response.iter_lines():
            if line:
                data = json.loads(line)
                if "result" in data:
                    chunk = Chunk(
                        path=data["result"]["source"],
                        texts=[
                            content["text"]
                            for content in data["result"]["content"]
                            if content["type"] == "text"
                        ],
                        images=[
                            Image.open(
                                BytesIO(
                                    base64.b64decode(content["image_url"].split(",")[1])
                                )
                            )
                            for content in data["result"]["content"]
                            if content["type"] == "image_url"
                        ],
                    )
                    chunks.append(chunk)
        return chunks

    # returns chunks of scraped content from any source (file, URL, etc.)
    scraped_chunks = []
    source_type = detect_source_type(filepath)
    if source_type is None:
        if verbose:
            print(f"[thepipe] Unsupported source type: {filepath}")
        return scraped_chunks
    if verbose:
        print(f"[thepipe] Scraping {source_type}: {filepath}...")
    if source_type == 'application/pdf':
        scraped_chunks = scrape_pdf(file_path=filepath, ai_extraction=ai_extraction, text_only=text_only, verbose=verbose, ai_model=ai_model, options=options)
    elif source_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        scraped_chunks = scrape_docx(file_path=filepath, verbose=verbose, text_only=text_only,)
    elif source_type == 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
        scraped_chunks = scrape_pptx(file_path=filepath, verbose=verbose, text_only=text_only,)
    elif source_type.startswith('image/'):
        scraped_chunks = scrape_image(file_path=filepath, text_only=text_only,)
    elif source_type.startswith('application/vnd.ms-excel') or source_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        scraped_chunks = scrape_spreadsheet(file_path=filepath, source_type=source_type,)
    elif source_type == 'application/x-ipynb+json':
        scraped_chunks = scrape_ipynb(file_path=filepath, verbose=verbose, text_only=text_only,)
    elif source_type == 'application/zip' or source_type == 'application/x-zip-compressed':
        scraped_chunks = scrape_zip(file_path=filepath, verbose=verbose, ai_extraction=ai_extraction, text_only=text_only, local=local,)
    elif source_type.startswith('video/'):
        scraped_chunks = scrape_video(file_path=filepath, verbose=verbose, text_only=text_only, options=options)
    elif source_type.startswith('audio/'):
        scraped_chunks = scrape_audio(file_path=filepath, verbose=verbose, options=options)
    elif source_type.startswith('text/html'):
        scraped_chunks = scrape_html(file_path=filepath, verbose=verbose, text_only=text_only)
    elif source_type.startswith('text/'):
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
    file_path: str, verbose: bool = False, text_only: bool = False
) -> List[Chunk]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
        html_content = file.read()
    markdown_content = markdownify.markdownify(html_content, heading_style="ATX")
    if text_only:
        return [Chunk(path=file_path, texts=[markdown_content])]
    images = get_images_from_markdown(html_content)
    return [Chunk(path=file_path, texts=[markdown_content], images=images)]


def scrape_plaintext(file_path: str) -> List[Chunk]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
        text = file.read()
    return [Chunk(path=file_path, texts=[text])]

def scrape_directory(dir_path: str, include_regex: Optional[str] = None, include_patterns: Optional[List[str]] = None, verbose: bool = False, ai_extraction: bool = False, text_only: bool = False, local: bool = False, options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    extraction = []
    
    if include_patterns is not None:
        # Use glob patterns
        all_files = []
        for pattern in include_patterns:
            pattern_path = os.path.join(dir_path, '**', pattern)
            all_files.extend(glob.glob(pattern_path, recursive=True))
    elif include_regex is not None:
        # Use regex
        all_files = []
        for root, _, files in os.walk(dir_path):
            for file in files:
                file_path = os.path.join(root, file)
                if re.search(include_regex, file_path, re.IGNORECASE):
                    all_files.append(file_path)
    else:
        # Neither pattern nor regex specified, include all files
        all_files = []
        for root, _, files in os.walk(dir_path):
            for file in files:
                all_files.append(os.path.join(root, file))
    
    # Ensure we're only dealing with files
    all_files = [f for f in all_files if os.path.isfile(f)]
    
    if verbose:
        print(f"[thepipe] Found {len(all_files)} files to process in {dir_path}")
    
    with ThreadPoolExecutor() as executor:
        results = executor.map(
            lambda file_path: scrape_file(
                filepath=file_path,
                ai_extraction=ai_extraction,
                text_only=text_only,
                verbose=verbose,
                local=local,
                options=options,
            ),
            all_files,
        )
        for result in results:
            extraction.extend(result)
    
    return extraction

def scrape_zip(file_path: str, include_regex: Optional[str] = None, include_patterns: Optional[List[str]] = None, verbose: bool = False, ai_extraction: bool = False, text_only: bool = False, local: bool = False) -> List[Chunk]:
    chunks = []
    with tempfile.TemporaryDirectory() as temp_dir:
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(temp_dir)
        chunks =scrape_directory(dir_path=temp_dir, include_regex=include_regex, include_patterns=include_patterns, verbose=verbose, ai_extraction=ai_extraction, text_only=text_only, local=local)
    return chunks

def scrape_pdf(file_path: str, ai_extraction: Optional[bool] = False, text_only: Optional[bool] = False, ai_model: Optional[str] = DEFAULT_AI_MODEL, verbose: Optional[bool] = False, options: Optional[Dict[str, Any]] = None) -> List[Chunk]:    
    chunks = []
    MAX_PAGES = 128

    if ai_extraction:
        from collections import OrderedDict
        import concurrent.futures
        import fitz
        from openai import OpenAI

        with open(file_path, "rb") as f:
            pdf_bytes = f.read()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            num_pages = len(doc)

            if num_pages > MAX_PAGES:
                err = f"Error: PDF has {num_pages} pages (max is {MAX_PAGES} for AI extraction)."
                raise Exception(err)

            openrouter_client = OpenAI(
                base_url=os.environ.get("LLM_SERVER_BASE_URL"),
                api_key=os.environ["LLM_SERVER_API_KEY"],
            )

            def process_page(page_num):
                page = doc[page_num]
                text = page.get_text()
                pix = page.get_pixmap()
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": make_image_url(
                                    image, host_images=HOST_IMAGES
                                ),
                            },
                            {
                                "type": "text",
                                "text": f"```{text}```\n{SCRAPING_PROMPT}",
                            },
                        ],
                    },
                ]
                response = openrouter_client.chat.completions.create(
                    model=ai_model if ai_model else DEFAULT_AI_MODEL,
                    messages=messages,
                    temperature=0,
                )
                try:
                    llm_response = response.choices[0].message.content
                    if not llm_response:
                        raise Exception(
                            f"Failed to receive a message content from LLM Response: {response}"
                        )

                    # remove markdown codeboxes if they are present
                    llm_response = llm_response.strip()
                    if llm_response.startswith("```markdown"):
                        llm_response = llm_response[len("```markdown") :]
                    elif llm_response.startswith("```"):
                        llm_response = llm_response[len("```") :]
                    if llm_response.endswith("```"):
                        llm_response = llm_response[: -len("```")]

                    return page_num, llm_response, image
                except Exception as e:
                    raise ValueError(f"{e} (unable to read LLM response: {response})")

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(process_page, page_num)
                    for page_num in range(num_pages)
                ]
                page_results = OrderedDict()
                for future in concurrent.futures.as_completed(futures):
                    page_num, llm_response, image = future.result()
                    page_results[page_num] = (llm_response, image)

            chunks = []
            for page_num in sorted(page_results.keys()):
                llm_response, image = page_results[page_num]
                chunks.append(
                    Chunk(
                        path=file_path,
                        texts=[llm_response],
                        images=[] if text_only else [image],
                    )
                )

            return chunks
    else:
        # if not using AI extraction, for each page, extract markdown and (optionally) full page images
        import fitz

        doc = fitz.open(file_path)
        try:
            import pymupdf4llm

            md_reader = pymupdf4llm.helpers.pymupdf_rag.to_markdown(
                doc, page_chunks=True
            )
            for i, page in enumerate(doc):
                text = md_reader[i]["text"]
                # remove excessive newlines
                text = re.sub(r"\n{3,}", "\n\n", text)
                text = text.strip()
                if text_only:
                    chunks.append(Chunk(path=file_path, texts=[text]))
                else:
                    pix = page.get_pixmap()
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    chunks.append(Chunk(path=file_path, texts=[text], images=[img]))
            doc.close()
        except:
            # try with default pumupdf if pymupdf4llm fails
            for i in range(len(doc)):
                page = doc[i]
                text = page.get_text()
                if text_only:
                    chunks.append(Chunk(path=file_path, texts=[text]))
                else:
                    pix = page.get_pixmap()
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    chunks.append(Chunk(path=file_path, texts=[text], images=[img]))
            doc.close()
    return chunks


def get_images_from_markdown(text: str) -> List[Image.Image]:
    image_urls = re.findall(r"!\[.*?\]\((.*?)\)", text)
    images = []
    for url in image_urls:
        extension = os.path.splitext(urlparse(url).path)[1]
        if extension in {".jpg", ".jpeg", ".png"}:
            img = Image.open(requests.get(url, stream=True).raw)
        else:
            # ignore incompatible image extractions
            continue
        images.append(img)
    return images


def scrape_image(file_path: str, text_only: bool = False) -> List[Chunk]:
    import pytesseract

    img = Image.open(file_path)
    img.load()  # needed to close the file
    chunks = []
    if text_only:
        text = pytesseract.image_to_string(img)
        chunks.append(Chunk(path=file_path, texts=[text]))
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
        chunks.append(Chunk(path=file_path, texts=[item_json]))
    return chunks

def scrape_url(url: str, include_regex: Optional[str] = None, 
               include_patterns: Optional[List[str]] = None, 
               text_only: bool = False, ai_extraction: bool = False, 
               verbose: bool = False, local: bool = False, 
               chunking_method: Optional[Callable] = chunk_by_page, 
               options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
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

    # Normal scraping process
    if not local:
        endpoint = f"{HOST_URL}/scrape"
        headers = {"Authorization": f"Bearer {THEPIPE_API_KEY}"}
        data = {
            "text_only": str(text_only).lower(),
            "ai_extraction": str(ai_extraction).lower(),
            "chunking_method": chunking_method.__name__,
            "options": json.dumps(options) if options else None,
            "urls": url
        }
        
        response = requests.post(endpoint, headers=headers, data=data, stream=True)
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            # each line is its own JSON object
            if not line.strip():
                continue  # skip blank lines
            data = json.loads(line)
            # If the server sent an error for this chunk, handle it
            if "error" in data:
                raise ValueError(f"Error scraping: {data['error']}")

        chunks = []
        for line in response.iter_lines():
            if line:
                chunk_data = json.loads(line)
                chunks.append(chunk_data['result'])
    else:
        chunks = []
        try:
            if matches_domain(url, DRIVE_DOMAINS):
                if verbose:
                    print("[thepipe] Detected Google Drive/Docs URL, using drive scraper")
                chunks = scrape_drive(url, text_only=text_only,
                                    ai_extraction=ai_extraction,
                                    verbose=verbose, options=options)
            elif matches_domain(url,VIDEO_PLATFORMS):
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
                if file_extension in ['pdf', 'docx', 'txt', 'csv', 'xlsx']:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        file_path = os.path.join(temp_dir, os.path.basename(parsed_url.path))
                        response = requests.get(normalize_url(url))
                        if (FILESIZE_LIMIT_MB and 
                            int(response.headers.get('Content-Length', 0)) > FILESIZE_LIMIT_MB * 1024 * 1024):
                            raise ValueError(f"File size exceeds {FILESIZE_LIMIT_MB} MB limit.")
                        with open(file_path, 'wb') as file:
                            file.write(response.content)
                        chunks = scrape_file(filepath=file_path, ai_extraction=ai_extraction,
                                           text_only=text_only, verbose=verbose,
                                           local=local, chunking_method=chunking_method,
                                           options=options)
                else:
                    chunk = extract_page_content(url=url, text_only=text_only,
                                               verbose=verbose, options=options)
                    chunks = chunking_method([chunk])
                    if not any(chunk.texts for chunk in chunks) and not any(chunk.images for chunk in chunks):
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
    
def scrape_video(file_path: str, verbose: bool = False, text_only: bool = False) -> List[Chunk]:
    import whisper
    from moviepy.editor import VideoFileClip

    model = whisper.load_model("base")
    video = VideoFileClip(file_path)
    num_chunks = math.ceil(video.duration / MAX_WHISPER_DURATION)
    chunks = []
    # split the video into chunks of fixed duration
    # here, we transcribe each chunk and extract its frame
    try:
        for i in range(num_chunks):
            start_time = i * MAX_WHISPER_DURATION
            end_time = start_time + MAX_WHISPER_DURATION
            if end_time > video.duration:
                end_time = video.duration
            # get the frame in the middle of the chunk
            frame_time = (start_time + end_time) / 2
            frame = video.get_frame(frame_time)
            image = Image.fromarray(frame)
            # save the audio to a temporary file
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as temp_audio_file:
                audio_path = temp_audio_file.name
            audio = video.subclip(start_time, end_time).audio
            transcription = None
            # transcribe it
            if audio is not None:
                audio.write_audiofile(audio_path, codec="pcm_s16le")
                result = model.transcribe(audio=audio_path, verbose=verbose)
                # Format transcription with timestamps
                formatted_transcription = []
                for segment in result["segments"]:
                    start = format_timestamp(segment["start"], i, MAX_WHISPER_DURATION)
                    end = format_timestamp(segment["end"], i, MAX_WHISPER_DURATION)
                    formatted_transcription.append(
                        f"[{start} --> {end}]  {segment['text']}"
                    )
                transcription = "\n".join(formatted_transcription)
                os.remove(audio_path)
            texts = [transcription] if transcription else []
            images = [image] if not text_only else []
            if texts or images:
                chunks.append(Chunk(path=file_path, texts=texts, images=images))
    finally:
        video.close()
    return chunks

def scrape_youtube(url: str, text_only: Optional[Union[bool, str]] = None, verbose: bool = False, 
                   metadata_fields: Optional[List[YouTubeEnum]] = None, 
                   options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """Scrape content from a YouTube URL."""
    initialize_video_processing()
    if verbose:
        print("[thepipe] Initializing YouTube content extraction...")
    
    ydl_opts = {
        'quiet': not verbose,
        'ignoreerrors': True,
        'extract_flat': 'in_playlist',
        'outtmpl': '%(title)s.%(ext)s',
        'subtitlesformat': 'vtt',
        'skip_download': True,  # Always skip video download initially
    }

    if text_only == 'transcribe':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'skip_download': False,  # Need to download for transcription
        })
    else:
        ydl_opts.update({
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en', 'en-orig'],
        })

    if options and 'youtube' in options:
        ydl_opts.update(YouTubeEnum.process_options(options['youtube'], bool(text_only), verbose))

    chunks = []
    with tempfile.TemporaryDirectory() as temp_dir:
        ydl_opts['outtmpl'] = os.path.join(temp_dir, '%(title)s.%(ext)s')

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if 'entries' in info:  # It's a playlist
                    videos = info['entries']
                else:  # Single video
                    videos = [info]

                for video in videos:
                    video_chunks = process_video(ydl, video, temp_dir, text_only, verbose, metadata_fields)
                    chunks.extend(video_chunks)

        except Exception as e:
            if verbose:
                print(f"[thepipe] Error processing content: {str(e)}")
            chunks.append(Chunk(path=url, texts=[f"Error: Unable to process content. {str(e)}"]))

    return chunks

def process_video(ydl, video_info: Dict[str, Any], temp_dir: str, 
                 text_only: Optional[Union[bool, str]], verbose: bool, 
                 metadata_fields: Optional[List[YouTubeEnum]] = None) -> List[Chunk]:
    """Process a single video and extract content based on specified options."""
    video_chunks = []
    video_url = video_info.get('webpage_url') or video_info.get('url')
    if not video_url:
        if verbose:
            print(f"[thepipe] Skipping video with no URL")
        return video_chunks

    try:
        # Extract metadata
        metadata = YouTubeEnum.extract_metadata(video_info, metadata_fields)
        metadata_chunk = Chunk(path=video_url, texts=[YouTubeEnum.format_metadata(metadata)])
        video_chunks.append(metadata_chunk)

        if text_only == 'transcribe':
            # Direct transcription mode
            if verbose:
                print("[thepipe] Downloading audio for transcription...")
            ydl.params.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'skip_download': False
            })
            ydl.process_ie_result(video_info, download=True)
            audio_file = find_audio_file(temp_dir, video_info['title'])
            if audio_file:
                transcription_chunks = scrape_audio(audio_file, verbose=verbose)
                video_chunks.extend(transcription_chunks)
            else:
                if verbose:
                    print(f"[thepipe] Failed to download audio for transcription: {video_url}")
                video_chunks.append(Chunk(path=video_url, texts=["No transcription available"]))
                
        elif text_only in ['default', 'ai', 'uploaded']:
            if verbose:
                print("[thepipe] Attempting to extract subtitles...")
            # First try to get subtitles
            ydl.params.update({
                'writesubtitles': True,
                'writeautomaticsub': True,
                'skip_download': True
            })
            
            if text_only == 'ai':
                ydl.params['subtitleslangs'] = ['a.en,a.*', 'en,*']
            elif text_only == 'uploaded':
                ydl.params['subtitleslangs'] = ['en,*', 'a.en,a.*']
            else:
                ydl.params['subtitleslangs'] = ['en,*', 'a.en,a.*']
                
            try:
                ydl.process_ie_result(video_info, download=True)
                subtitle_files = find_subtitle_files(temp_dir, video_info['title'])
                
                if subtitle_files:
                    for subtitle_file in subtitle_files:
                        subtitle_chunks = clean_subtitles(subtitle_file, video_url, debug=verbose)
                        if subtitle_chunks:
                            video_chunks.extend(subtitle_chunks)
                            break
                
                # If no subtitles found and we're in default mode, fall back to transcription
                if not subtitle_files and text_only is 'default':
                    if verbose:
                        print("[thepipe] No subtitles found, falling back to transcription...")
                    # Update options for audio-only download
                    ydl.params.update({
                        'format': 'bestaudio/best',
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }],
                        'skip_download': False
                    })
                    ydl.process_ie_result(video_info, download=True)
                    audio_file = find_audio_file(temp_dir, video_info['title'])
                    if audio_file:
                        transcription_chunks = scrape_audio(audio_file, verbose=verbose)
                        video_chunks.extend(transcription_chunks)
                    else:
                        video_chunks.append(Chunk(path=video_url, texts=["No transcription available"]))
                elif not subtitle_files:
                    video_chunks.append(Chunk(path=video_url, texts=[f"No {text_only} subtitles available"]))
                    
            except Exception as e:
                if verbose:
                    print(f"[thepipe] Error processing subtitles: {str(e)}")
                video_chunks.append(Chunk(path=video_url, texts=[f"Error processing subtitles: {str(e)}"]))

    except Exception as e:
        if verbose:
            print(f"[thepipe] Error processing video {video_url}: {str(e)}")
        video_chunks.append(Chunk(path=video_url, texts=[f"Error: Unable to process video. {str(e)}"]))

    return video_chunks

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
    return [Chunk(path=file_path, texts=[transcription_text])]


def scrape_github(
    github_url: str,include_regex: Optional[str] = None,include_patterns: Optional[List[str]] = None,
    text_only: bool = False,ai_extraction: bool = False,branch: str = "main",verbose: bool = False,
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
                    return [Chunk(path=github_url, texts=[f"Failed to clone repository even with authentication: {github_url}"])]
            else:
                return [Chunk(path=github_url, texts=[f"Repository requires authentication. Set GITHUB_TOKEN environment variable or provide token in options"])]

        try:
            return scrape_directory(
                dir_path=temp_dir,
                include_regex=include_regex,
                include_patterns=include_patterns,
                verbose=verbose,
                ai_extraction=ai_extraction,
                text_only=text_only,
                local=True
            )
        except Exception as e:
            if verbose:
                print(f"[thepipe] Error processing repository contents: {str(e)}")
            return [Chunk(path=github_url, texts=[f"Error processing repository contents: {str(e)}"])]
    
def scrape_docx(file_path: str, verbose: bool = False, text_only: bool = False) -> List[Chunk]:
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    import csv
    import io
    import weakref

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
            if child.__class__.__name__ == "CT_P":
                yield Paragraph(child, parent)
            elif child.__class__.__name__ == "CT_Tbl":
                yield Table(child, parent)

    # helper function to read tables in the document
    def read_docx_tables(tab):
        vf = io.StringIO()
        writer = csv.writer(vf)
        for row in tab.rows:
            writer.writerow(cell.text for cell in row.cells)
        vf.seek(0)
        return vf.getvalue()

    # read the document
    document = Document(file_path)

    # Define namespaces
    nsmap = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    chunks = []
    image_counter = 0

    try:
        # scrape each block in the document to create chunks
        for block in iter_block_items(document):
            block_texts = []
            block_images = []
            if isinstance(block, Paragraph):
                block_texts.append(block.text)
                if not text_only:
                    # "runs" are the smallest units in a paragraph
                    for run in block.runs:
                        if "pic:pic" in run.element.xml:
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
                                        image_part = document.part.related_parts[
                                            embed_attr
                                        ]
                                        image_data = io.BytesIO(image_part._blob)
                                        image = Image.open(image_data)
                                        image.load()
                                        block_images.append(image)  # Append the image directly, not a weak reference
                                        image_counter += 1
            elif isinstance(block, Table):
                table_text = read_docx_tables(block)
                block_texts.append(table_text)
            if block_texts or block_images:
                chunks.append(
                    Chunk(path=file_path, texts=block_texts, images=block_images)
                )

    finally:
        # Close any open image files
        for chunk in chunks:
            for img_ref in chunk.images:
                img = img_ref() if isinstance(img_ref, weakref.ReferenceType) else img_ref
                if img is not None:
                    try:
                        img.close()
                    except Exception as e:
                        if verbose:
                            print(f"[thepipe] Error closing image: {str(e)}")

    return chunks


def scrape_pptx(
    file_path: str, verbose: bool = False, text_only: bool = False
) -> List[Chunk]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(file_path)
    chunks = []
    # iterate through each slide in the presentation
    for slide in prs.slides:
        slide_texts = []
        slide_images = []
        # iterate through each shape in the slide
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text
                    if len(slide_texts) == 0:
                        text = "# " + text  # header for first text of a slide
                    slide_texts.append(text)
            # extract images from shapes
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and not text_only:
                image_data = shape.image.blob
                image = Image.open(BytesIO(image_data))
                slide_images.append(image)
        # add slide to chunks if it has text or images
        if slide_texts or slide_images:
            chunks.append(Chunk(path=file_path, texts=slide_texts, images=slide_images))
    # return all chunks
    return chunks


def scrape_ipynb(
    file_path: str, verbose: bool = False, text_only: bool = False
) -> List[Chunk]:
    with open(file_path, "r", encoding="utf-8") as file:
        notebook = json.load(file)
    chunks = []
    # parse cells in the notebook
    for cell in notebook["cells"]:
        texts = []
        images = []
        # parse cell content based on type
        if cell["cell_type"] == "markdown":
            text = "".join(cell["source"])
            if not text_only:
                images = get_images_from_markdown(text)
            texts.append(text)
        elif cell["cell_type"] == "code":
            source = "".join(cell["source"])
            texts.append(source)
            output_texts = []
            # code cells can have outputs
            if "outputs" in cell:
                for output in cell["outputs"]:
                    if (
                        "data" in output
                        and "image/png" in output["data"]
                        and not text_only
                    ):
                        image_data = output["data"]["image/png"]
                        image = Image.open(BytesIO(base64.b64decode(image_data)))
                        images.append(image)
                    elif "data" in output and "text/plain" in output["data"]:
                        output_text = "".join(output["data"]["text/plain"])
                        output_texts.append(output_text)
            if output_texts:
                texts.extend(output_texts)
        elif cell["cell_type"] == "raw":
            text = "".join(cell["source"])
            texts.append(text)
        if texts or images:
            chunks.append(Chunk(path=file_path, texts=texts, images=images))
    return chunks

def scrape_tweet(url: str, text_only: bool = False, verbose: bool = False) -> List[Chunk]:
    # magic function from https://github.com/vercel/react-tweet/blob/main/packages/react-tweet/src/api/fetch-tweet.ts
    # unofficial, could break at any time
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
    
    chunks = []
    main_chunk = Chunk(path=url, texts=[tweet_text])
    chunks.append(main_chunk)

    if not text_only:
        # Extract images from tweet
        images = []
        if "mediaDetails" in tweet_data:
            for media in tweet_data["mediaDetails"]:
                if media.get("type") == "photo":
                    image_url = media.get("media_url_https")
                    if image_url:
                        image_response = requests.get(image_url)
                        img = Image.open(BytesIO(image_response.content))
                        images.append(img)
                elif media.get("type") == "video":
                    video_url = media.get("video_info", {}).get("variants", [{}])[0].get("url")
                    if video_url:
                        video_chunks = scrape_youtube(video_url, text_only=text_only, verbose=verbose)
                        chunks.extend(video_chunks)

        main_chunk.images = images

    return chunks
