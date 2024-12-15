import mimetypes
import os
from typing import List, Optional
from magika import Magika

FOLDERS_TO_IGNORE = ['*node_modules.*', '.*venv.*', '.*\.git.*', '.*\.vscode.*', '.*pycache.*']

FILES_TO_IGNORE = ['package-lock.json', '.gitignore', '.*\.bin', '.*\.pyc', '.*\.pyo', '.*\.exe', '.*\.dll', '.*\.ipynb_checkpoints']

FILESIZE_LIMIT_MB = os.getenv("FILESIZE_LIMIT_MB", 50)

def detect_source_type(source: str) -> str:
    # otherwise, try to detect the file type by its extension
    _, extension = os.path.splitext(source)
    if extension:
        if extension == '.ipynb':
            # special case for notebooks, mimetypes is not familiar
            return 'application/x-ipynb+json'
        guessed_mimetype = mimetypes.guess_type(source)[0]
        if guessed_mimetype:
            return guessed_mimetype
    # if that fails, try AI detection with Magika
    magika = Magika()
    with open(source, 'rb') as file:
        result = magika.identify_bytes(file.read())
    mimetype = result.output.mime_type
    return mimetype

def find_subtitle_files(directory: str, video_title: str) -> List[str]:
    subtitle_files = []
    for file in os.listdir(directory):
        if file.startswith(video_title) and file.endswith('.vtt'):
            subtitle_files.append(os.path.join(directory, file))
    return subtitle_files

def find_audio_file(directory: str, video_title: str) -> Optional[str]:
    for file in os.listdir(directory):
        if file.startswith(video_title) and file.endswith(('.mp3', '.m4a', '.wav')):
            return os.path.join(directory, file)
    return None

def find_video_file(directory: str, video_title: str) -> Optional[str]:
    for file in os.listdir(directory):
        if file.startswith(video_title) and file.endswith(('.mp4', '.webm', '.mkv')):
            return os.path.join(directory, file)
    return None
