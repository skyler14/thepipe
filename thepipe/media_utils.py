from functools import lru_cache
import os
import re
import logging
import tempfile
import io
from typing import Dict, List, Optional, Any, Union, Tuple
import http.cookiejar
from PIL import Image
from urllib.parse import urlparse
import requests
from thepipe.core import Chunk

# Constants
MAX_WHISPER_DURATION = 600  # 10 minutes

VIDEO_PLATFORMS = {
    "youtube.com", "youtu.be", "netflix.com", "amazon.com/*/video", "primevideo.com",
    "hulu.com", "disneyplus.com", "vimeo.com", "twitch.tv",
    "tiktok.com", "dailymotion.com", "vevo.com", "kick.com",
    "crunchyroll.com", "peacocktv.com", "hbomax.com", "roku.com",
    "pluto.tv", "tubitv.com", "iqiyi.com", "v.qq.com",
    "youku.com", "bilibili.com", "brightcove.com", "wistia.com",
    "jwplayer.com", "kaltura.com", "panopto.com", "vidyard.com",
    "vk.com", "rutube.ru", "metacafe.com", "veoh.com",
    "ustream.tv", "livestream.com", "younow.com", "niconico.jp",
    "vlive.tv", "afreecatv.com", "odysee.com"
}

# Global variables for lazy loading
webvtt = None
ffmpeg = None
yt_dlp = None

def initialize_subtitle_libraries():
    """Initialize the subtitle processing libraries."""
    global webvtt
    if webvtt is None:
        try:
            import webvtt
        except ImportError:
            raise ImportError("webvtt-py library not found. Please install it with: pip install webvtt-py")

def initialize_ffmpeg():
    """Initialize FFmpeg library."""
    global ffmpeg
    if ffmpeg is None:
        try:
            import ffmpeg
        except ImportError:
            raise ImportError("ffmpeg-python library not found. Please install it with: pip install ffmpeg-python")
    return ffmpeg

def initialize_video_processing():
    """Initialize video processing libraries."""
    global yt_dlp
    if yt_dlp is None:
        try:
            import yt_dlp
        except ImportError:
            raise ImportError("yt-dlp library not found. Please install it with: pip install yt-dlp")
    return yt_dlp

@lru_cache(maxsize=1)
def get_subtitle_parser():
    """Get the subtitle parser instance with proper initialization."""
    initialize_subtitle_libraries()
    return webvtt

def timestamp_to_seconds(timestamp: str) -> float:
    """Convert timestamp to seconds."""
    try:
        time_parts = timestamp.split(':')
        if len(time_parts) == 3:  # HH:MM:SS.mmm
            h, m, s = time_parts
        elif len(time_parts) == 2:  # MM:SS.mmm
            m, s = time_parts
            h = '0'
        else:
            raise ValueError(f"Invalid timestamp format: {timestamp}")
        
        total = float(h) * 3600 + float(m) * 60 + float(s)
        return round(total, 3)  # Round to milliseconds
    except Exception as e:
        logging.warning(f"Error parsing timestamp {timestamp}: {str(e)}")
        return 0.0

def format_timestamp(seconds: float, chunk_index: int = 0, chunk_duration: int = 0) -> str:
    """Format a timestamp in HH:MM:SS.mmm format."""
    total_seconds = chunk_index * chunk_duration + seconds
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{int(seconds):02}.{milliseconds:03}"

def clean_text(text: str) -> str:
    """Clean and normalize text content."""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove multiple spaces
    text = ' '.join(text.split())
    # Remove stutters/repetitions
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text)
    # Clean up interrupted phrases
    text = re.sub(r'(\w+\s+\w+)\s+\1', r'\1', text)
    return text.strip()

def should_merge_entries(curr: Dict, next_: Dict) -> bool:
    """Determine if two entries should be merged based on various criteria."""
    # Don't merge if there's a significant gap
    if next_['start_seconds'] - curr['end_seconds'] > 0.1:
        return False
    
    # Merge if duration is too short
    if (curr['end_seconds'] - curr['start_seconds']) < 0.1:
        return True
        
    # Check for overlapping content
    curr_words = set(curr['text'].split())
    next_words = set(next_['text'].split())
    overlap = len(curr_words & next_words)
    
    # Merge if significant overlap
    if overlap / max(len(curr_words), len(next_words)) > 0.5:
        return True
        
    return False

def merge_entries(entries: List[Dict]) -> Dict:
    """Merge multiple entries intelligently."""
    if not entries:
        return None
        
    # Get unique words while preserving order
    seen_words = set()
    final_words = []
    for entry in entries:
        words = entry['text'].split()
        for word in words:
            if word not in seen_words:
                seen_words.add(word)
                final_words.append(word)
    
    return {
        'start': entries[0]['start'],
        'end': entries[-1]['end'],
        'start_seconds': entries[0]['start_seconds'],
        'end_seconds': entries[-1]['end_seconds'],
        'text': ' '.join(final_words)
    }

def clean_subtitles(subtitle_file: str, video_url: str, debug: bool = False) -> List[Chunk]:
    """Process and clean subtitle content from a subtitle file."""
    parser = get_subtitle_parser()
    captions = parser.read(subtitle_file)
    
    # Convert captions to dictionary entries with computed seconds
    entries = []
    for caption in captions:
        text = clean_text(caption.text)
        if text:  # Skip empty entries
            entries.append({
                'start': caption.start,
                'end': caption.end,
                'start_seconds': timestamp_to_seconds(caption.start),
                'end_seconds': timestamp_to_seconds(caption.end),
                'text': text
            })

    # Merge entries based on our criteria
    cleaned_entries = []
    current_group = []
    
    for i, entry in enumerate(entries):
        current_group.append(entry)
        
        # Check if we should start a new group
        if i == len(entries) - 1 or not should_merge_entries(entry, entries[i + 1]):
            if current_group:
                merged = merge_entries(current_group)
                if merged and merged['text']:  # Only add non-empty entries
                    cleaned_entries.append(merged)
            current_group = []

    # Convert to chunks, splitting long entries at logical points
    chunks = []
    for entry in cleaned_entries:
        text = entry['text']
        
        # Split very long entries at sentence boundaries
        if len(text) > 200:
            sentences = re.split(r'(?<=[.!?])\s+', text)
            for sentence in sentences:
                if sentence.strip():
                    formatted_text = f"[{entry['start']} --> {entry['end']}]  {sentence.strip()}"
                    chunks.append(Chunk(path=video_url, text=formatted_text))
        else:
            # For shorter entries, check if they contain multiple complete thoughts
            parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
            if len(parts) > 1:
                for part in parts:
                    if part.strip():
                        formatted_text = f"[{entry['start']} --> {entry['end']}]  {part.strip()}"
                        chunks.append(Chunk(path=video_url, text=formatted_text))
            else:
                formatted_text = f"[{entry['start']} --> {entry['end']}]  {text}"
                chunks.append(Chunk(path=video_url, text=formatted_text))

    if debug:
        with open("original_transcript.txt", "w", encoding="utf-8") as f:
            for caption in captions:
                f.write(f"[{caption.start} --> {caption.end}] {caption.text}\n")
        with open("cleaned_transcript.txt", "w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(f"{chunk.text}\n")

    return chunks

def get_images_from_markdown(text: str) -> List[Image.Image]:
    """Extract images from markdown text."""
    image_urls = re.findall(r"!\[.*?\]\((.*?)\)", text)
    images = []
    for url in image_urls:
        extension = os.path.splitext(urlparse(url).path)[1]
        if extension in {'.jpg', '.jpeg', '.png'}:
            img = Image.open(requests.get(url, stream=True).raw)
        else:
            # ignore incompatible image extractions
            continue
        images.append(img)
    return images

def find_audio_file(temp_dir, title, verbose=False):
    """Find extracted audio file with improved detection"""
    if verbose:
        print(f"[thepipe] Looking for audio file in: {temp_dir}")
        print(f"[thepipe] Expected title: {title}")
        print(f"[thepipe] Files in directory: {os.listdir(temp_dir)}")
    
    patterns = [f"{title}.mp3", f"{title}.m4a", f"{title}.wav", f"{title}.opus"]
    
    for pattern in patterns:
        file_path = os.path.join(temp_dir, pattern)
        if os.path.exists(file_path):
            if verbose:
                print(f"[thepipe] Found audio file: {pattern}")
            return file_path
    
    # Fallback: find any audio file
    audio_extensions = ['.mp3', '.m4a', '.wav', '.opus', '.aac']
    for file in os.listdir(temp_dir):
        if any(file.lower().endswith(ext) for ext in audio_extensions):
            file_path = os.path.join(temp_dir, file)
            if verbose:
                print(f"[thepipe] Found fallback audio file: {file}")
            return file_path
    
    return None

def is_subtitle_meaningful(subtitle_text: str) -> bool:
    """Check if subtitle content is meaningful."""
    # Remove timestamps and empty lines
    content_lines = [line.strip() for line in subtitle_text.split('\n') 
                     if line.strip() and not line.strip().replace('->', '').replace(':', '').isdigit()]
    
    # Check if there's meaningful content (more than just a few short words)
    return len(content_lines) > 3 and any(len(line.split()) > 3 for line in content_lines)

# FFmpeg-based video processing functions
def get_video_duration_ffmpeg(file_path: str) -> float:
    """Get video duration using ffprobe."""
    ffmpeg = initialize_ffmpeg()
    try:
        probe = ffmpeg.probe(file_path)
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        return float(video_info['duration'])
    except Exception as e:
        raise ValueError(f"Could not determine video duration: {str(e)}")

def extract_frame_ffmpeg(file_path: str, timestamp: float, verbose: bool = False) -> Optional[Image.Image]:
    """Extract a single frame from video at specified timestamp using ffmpeg."""
    ffmpeg = initialize_ffmpeg()
    try:
        # Extract frame to stdout as PNG
        out, err = (
            ffmpeg
            .input(file_path, ss=timestamp)
            .output('pipe:', vframes=1, format='image2', vcodec='png')
            .run(capture_stdout=True, capture_stderr=True, quiet=not verbose)
        )
        
        # Load image from bytes
        image = Image.open(io.BytesIO(out))
        return image
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error extracting frame at {timestamp}s: {str(e)}")
            if 'err' in locals() and err:
                print(f"[thepipe] FFmpeg error: {err.decode()}")
        return None

def extract_audio_segment_ffmpeg(file_path: str, start_time: float, duration: float, 
                                output_path: str, verbose: bool = False) -> bool:
    """Extract audio segment using ffmpeg."""
    ffmpeg = initialize_ffmpeg()
    try:
        (
            ffmpeg
            .input(file_path, ss=start_time, t=duration)
            .audio
            .output(output_path, acodec='pcm_s16le', ac=1, ar=16000)
            .overwrite_output()
            .run(quiet=not verbose)
        )
        return True
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error extracting audio segment: {str(e)}")
        return False

def has_video_stream(file_path: str) -> bool:
    """Check if file has a video stream."""
    ffmpeg = initialize_ffmpeg()
    try:
        probe = ffmpeg.probe(file_path)
        return any(s['codec_type'] == 'video' for s in probe['streams'])
    except Exception:
        return False

def has_audio_stream(file_path: str) -> bool:
    """Check if file has an audio stream."""
    ffmpeg = initialize_ffmpeg()
    try:
        probe = ffmpeg.probe(file_path)
        return any(s['codec_type'] == 'audio' for s in probe['streams'])
    except Exception:
        return False

# Helper functions for video processing (moved from scraper.py)

def process_video(ydl, video_info: Dict[str, Any], temp_dir: str, 
                      text_only: Optional[Union[bool, str]], verbose: bool, 
                      metadata_fields: Optional[List] = None) -> List[Chunk]:
    """Process a single video with comprehensive error handling and fallback mechanisms."""
    
    # CRITICAL FIX: Ensure video_info is always a dict
    if not isinstance(video_info, dict):
        if verbose:
            print(f"[thepipe] Invalid video_info type: {type(video_info)}")
        return []
    
    video_url = video_info.get('webpage_url') or video_info.get('url')
    if not video_url:
        if verbose:
            print("[thepipe] No URL found in video info")
        return []

    chunks = []
    
    try:
        # 1. Extract metadata first
        if metadata_fields or not text_only:
            chunks.extend(extract_metadata_chunk(video_info, video_url, metadata_fields, verbose))
        
        # 2. Try subtitle extraction first (unless explicitly transcribing)
        if text_only != 'transcribe':
            subtitle_chunks = try_subtitle_extraction(ydl, video_info, video_url, temp_dir, text_only, verbose)
            if subtitle_chunks:
                chunks.extend(subtitle_chunks)
                return chunks
        
        # 3. Fallback to transcription if no subtitles or explicitly requested
        if verbose:
            print("[thepipe] No subtitles found, falling back to transcription...")
        transcription_chunks = try_transcription(ydl, video_info, video_url, temp_dir, verbose)
        chunks.extend(transcription_chunks)
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error in process_video: {str(e)}")
        chunks.append(Chunk(path=video_url, text=f"Error processing video: {str(e)}"))
    
    return chunks

def extract_metadata_chunk(video_info: Dict[str, Any], video_url: str, 
                                metadata_fields: Optional[List], verbose: bool) -> List[Chunk]:
    """Safely extract metadata chunk with error handling."""
    try:
        if not isinstance(video_info, dict):
            return []
        
        # Import YouTubeEnum here to avoid circular imports
        from .enums import YouTubeEnum
        metadata = YouTubeEnum.extract_metadata(video_info, metadata_fields)
        return [Chunk(path=video_url, text=YouTubeEnum.format_metadata(metadata))]
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error extracting metadata: {str(e)}")
        return []

def try_subtitle_extraction(ydl, video_info: Dict[str, Any], video_url: str, 
                                 temp_dir: str, text_only: Optional[Union[bool, str]], verbose: bool) -> List[Chunk]:
    """Try to extract subtitles with comprehensive error handling."""
    
    if verbose:
        print("[thepipe] Attempting to extract subtitles...")
    
    try:
        if not isinstance(video_info, dict):
            return []
        
        # Import sanitize_filename here to avoid circular imports
        from .file_utils import sanitize_filename, find_subtitle_files
        safe_title = sanitize_filename(video_info.get('title', 'video'))
        
        # Configure subtitle preferences
        if text_only == 'ai':
            subtitle_langs = ['a.en', 'a.*']  # Prefer AI-generated
        elif text_only == 'uploaded':
            subtitle_langs = ['en', '*']  # Prefer human-uploaded
        else:  # default
            subtitle_langs = ['en', 'en-orig', 'a.en', 'a.*']  # Prefer human, fallback to AI
        
        # Update ydl options for subtitle extraction
        ydl.params.update({
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': subtitle_langs,
            'outtmpl': os.path.join(temp_dir, f'{safe_title}.%(ext)s'),
            'skip_download': True
        })
        
        # Process the video for subtitles
        ydl.process_ie_result(video_info, download=True)
        
        # Find and process subtitle files
        subtitle_files = find_subtitle_files(temp_dir, safe_title)
        
        if subtitle_files:
            for subtitle_file in subtitle_files:
                if verbose:
                    print(f"[thepipe] Processing subtitle file: {subtitle_file}")
                subtitle_chunks = clean_subtitles(subtitle_file, video_url, debug=verbose)
                if subtitle_chunks:
                    return subtitle_chunks
        
        return []
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Subtitle extraction failed: {str(e)}")
        return []

def try_transcription(ydl, video_info: Dict[str, Any], video_url: str, temp_dir: str, verbose: bool) -> List[Chunk]:
    """Try to transcribe audio with multiple fallback strategies."""
    
    if verbose:
        print("[thepipe] Attempting audio transcription...")
    
    try:
        # Step 1: Try audio-only download
        audio_file = try_audio_download(ydl, video_info, video_url, temp_dir, verbose)
        
        # Step 2: Fallback to smallest video + audio extraction
        if not audio_file:
            audio_file = try_video_download(ydl, video_url, temp_dir, verbose)
        
        # Step 3: Transcribe the audio file
        if audio_file and os.path.exists(audio_file):
            if verbose:
                print(f"[thepipe] Transcribing audio file: {audio_file}")
            return scrape_audio(audio_file, verbose=verbose)
        
        return [Chunk(path=video_url, text="No transcription available")]
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Transcription failed: {str(e)}")
        return [Chunk(path=video_url, text=f"Transcription error: {str(e)}")]

def try_audio_download(ydl, video_info: Dict[str, Any], video_url: str, temp_dir: str, verbose: bool) -> Optional[str]:
    """Try to download audio-only format safely."""
    
    try:
        if verbose:
            print("[thepipe] Trying audio-only download...")
        
        if not isinstance(video_info, dict):
            return None
        
        # Import sanitize_filename here to avoid circular imports
        from .file_utils import sanitize_filename
        safe_title = sanitize_filename(video_info.get('title', 'video'))
        
        # Create fresh ydl instance for audio download
        yt_dlp = initialize_video_processing()
        audio_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(temp_dir, f'{safe_title}.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': not verbose,
            'ignoreerrors': True,
        }
        
        with yt_dlp.YoutubeDL(audio_opts) as audio_ydl:
            audio_ydl.download([video_url])
        
        return find_audio_file(temp_dir, safe_title, verbose)
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Audio-only download failed: {str(e)}")
        return None

def try_video_download(ydl, video_url: str, temp_dir: str, verbose: bool) -> Optional[str]:
    """Download smallest video format and extract audio safely."""
    
    try:
        if verbose:
            print("[thepipe] Downloading smallest video format...")
        
        # Get fresh info for format selection
        yt_dlp = initialize_video_processing()
        with yt_dlp.YoutubeDL({'quiet': not verbose}) as info_ydl:
            info = info_ydl.extract_info(video_url, download=False)
            
        if not isinstance(info, dict):
            return None
            
        # Find smallest format
        formats = info.get('formats', [])
        if not formats:
            return None
            
        smallest_format = find_smallest_format(formats, verbose)
        if not smallest_format:
            smallest_format = 'worst'  # Final fallback
        
        # Import sanitize_filename here to avoid circular imports
        from .file_utils import sanitize_filename
        safe_title = sanitize_filename(info.get('title', 'video'))
        
        # Download with audio extraction
        video_opts = {
            'format': smallest_format,
            'outtmpl': os.path.join(temp_dir, f'{safe_title}.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': not verbose,
            'ignoreerrors': True,
        }
        
        with yt_dlp.YoutubeDL(video_opts) as video_ydl:
            video_ydl.download([video_url])
        
        return find_audio_file(temp_dir, safe_title, verbose)
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Video download failed: {str(e)}")
        return None

def find_smallest_format(formats: List[Dict], verbose: bool) -> Optional[str]:
    """Safely find the smallest video format."""
    try:
        smallest_format = None
        min_size = float('inf')
        
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
                
            # Skip audio-only formats
            if not fmt.get('height') and not fmt.get('width'):
                continue
            
            # Calculate size estimate
            size_estimate = (
                fmt.get('filesize') or 
                fmt.get('filesize_approx') or 
                (fmt.get('tbr', 0) * 1000) or
                (fmt.get('height', 999) * fmt.get('width', 999))
            )
            
            if size_estimate < min_size:
                min_size = size_estimate
                smallest_format = fmt.get('format_id')
        
        if verbose and smallest_format:
            print(f"[thepipe] Selected smallest format: {smallest_format}")
        
        return smallest_format
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error finding smallest format: {str(e)}")
        return None

def fallback_to_transcription(url: str, temp_dir: str, verbose: bool) -> List[Chunk]:
    """Final fallback: try direct transcription without yt-dlp metadata."""
    try:
        if verbose:
            print("[thepipe] Attempting direct transcription fallback...")
            
        # Simple download with minimal options
        yt_dlp = initialize_video_processing()
        simple_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(temp_dir, 'fallback_audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': not verbose,
            'ignoreerrors': True,
        }
        
        with yt_dlp.YoutubeDL(simple_opts) as fallback_ydl:
            fallback_ydl.download([url])
        
        # Find any audio file in temp directory
        for file in os.listdir(temp_dir):
            if file.lower().endswith(('.mp3', '.m4a', '.wav', '.opus')):
                audio_path = os.path.join(temp_dir, file)
                if verbose:
                    print(f"[thepipe] Found fallback audio: {audio_path}")
                return scrape_audio(audio_path, verbose=verbose)
        
        return []
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Fallback transcription failed: {str(e)}")
        return []

def scrape_audio(file_path: str, verbose: bool = False, options: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """Transcribe audio file using Whisper with robust error handling."""
    try:
        import whisper
    except ImportError:
        raise ImportError("whisper library not found. Please install it with: pip install openai-whisper")

    try:
        model = whisper.load_model("base")
        if verbose:
            print(f"[thepipe] Transcribing audio file: {file_path}")
        
        result = model.transcribe(audio=file_path, verbose=verbose)
        segments = result.get("segments", [])

        transcript: List[str] = []
        for segment in segments:
            start = format_timestamp(segment["start"], 0, 0)
            end = format_timestamp(segment["end"], 0, 0)
            if segment["text"].strip():
                transcript.append(f"[{start} --> {end}]  {segment['text']}")
        
        # Join the formatted transcription into a single string
        transcription_text = '\n'.join(transcript)
        if verbose:
            print(f"[thepipe] Transcription completed for {file_path}")
        return [Chunk(path=file_path, text=transcription_text)]
        
    except Exception as e:
        if verbose:
            print(f"[thepipe] Error transcribing audio: {str(e)}")
        return [Chunk(path=file_path, text=f"Error transcribing audio: {str(e)}")]