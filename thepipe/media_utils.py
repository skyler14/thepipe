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
    "vlive.tv", "afreecatv.com"
}

# Global variables for lazy loading
webvtt = None
ffmpeg = None

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
                    chunks.append(Chunk(path=video_url, texts=[formatted_text]))
        else:
            # For shorter entries, check if they contain multiple complete thoughts
            parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
            if len(parts) > 1:
                for part in parts:
                    if part.strip():
                        formatted_text = f"[{entry['start']} --> {entry['end']}]  {part.strip()}"
                        chunks.append(Chunk(path=video_url, texts=[formatted_text]))
            else:
                formatted_text = f"[{entry['start']} --> {entry['end']}]  {text}"
                chunks.append(Chunk(path=video_url, texts=[formatted_text]))

    if debug:
        with open("original_transcript.txt", "w", encoding="utf-8") as f:
            for caption in captions:
                f.write(f"[{caption.start} --> {caption.end}] {caption.text}\n")
        with open("cleaned_transcript.txt", "w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(f"{chunk.texts[0]}\n")

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

def find_subtitle_files(directory: str, video_title: str) -> List[str]:
    """Find subtitle files for a given video title."""
    subtitle_files = []
    for file in os.listdir(directory):
        if file.startswith(video_title) and file.endswith('.vtt'):
            subtitle_files.append(os.path.join(directory, file))
    return subtitle_files

def find_audio_file(directory: str, video_title: str) -> Optional[str]:
    """Find audio file for a given video title."""
    for file in os.listdir(directory):
        if file.startswith(video_title) and file.endswith(('.mp3', '.m4a', '.wav')):
            return os.path.join(directory, file)
    return None

def find_video_file(directory: str, video_title: str) -> Optional[str]:
    """Find video file for a given video title."""
    for file in os.listdir(directory):
        if file.startswith(video_title) and file.endswith(('.mp4', '.webm', '.mkv')):
            return os.path.join(directory, file)
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