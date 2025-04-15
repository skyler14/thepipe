import mimetypes
import os
import re
import glob
import fnmatch
from typing import List, Optional, Dict, Any, Union

# Simple patterns without escape sequences to avoid warnings
FOLDERS_TO_IGNORE = ['*node_modules/*', '.*venv/*', '.*git/*', '.*vscode/*', '.*pycache/*','.git/*']
FILES_TO_IGNORE = ['package-lock.json', '.gitignore', '.*bin', '.*pyc', '.*pyo', '.*exe', '.*dll', '.*ipynb_checkpoints']
DEFAULT_IGNORE_PATTERNS = ['**/.git/**','**/.github/**','**/.git/objects/**','**/.git/hooks/**','**/.git/logs/**','**/.git/refs/**','**/.git/info/**','**/.gitattributes','**/.gitignore','**/.gitmodules','**/node_modules/**', '**/node_modules/.bin/**', '**/node_modules/**/.*','**/node_modules/.package-lock.json','**/package-lock.json', '**/yarn.lock','**/__pycache__/**', '**/*.pyc', '**/*.pyo','**/venv/**', '**/.venv/**', '**/env/**','**/build/**', '**/dist/**', '**/outputs/**','**/.vscode/**', '**/.idea/**', '**/.cache/**','**/.DS_Store','**/*.eslintrc*','**/.nycrc','**/.npmignore','**/.editorconfig','**/.travis.yml','**/.zuul.yml','**/.gitkeep','**/.*config','**/.*cache','**/.*rc']
SKIP_DIRS = ['.git','node_modules','__pycache__','.venv','venv','env','build','dist','outputs','.vscode','.idea','.cache']
FILESIZE_LIMIT_MB = os.getenv("FILESIZE_LIMIT_MB", 50)

def detect_source_type(source: str) -> str:
    # otherwise, try to detect the file type by its extension
    _, extension = os.path.splitext(source)
    if extension:
        if extension == '.ipynb':
            # special case for notebooks, mimetypes is not familiar
            return 'application/x-ipynb+json'
        elif extension == '.ts':
            return 'text/'
        guessed_mimetype = mimetypes.guess_type(source)[0]
        if guessed_mimetype:
            return guessed_mimetype
    # if that fails, try AI detection with Magika
    from magika import Magika
    magika = Magika()
    with open(source, 'rb') as file:
        result = magika.identify_bytes(file.read())
    mimetype = result.output.mime_type
    return mimetype

def should_skip_dir(dir_path: str) -> bool:
    """
    Check if a directory should be skipped entirely.
    
    Args:
        dir_path: Path to directory
        
    Returns:
        True if the directory should be skipped
    """
    dir_name = os.path.basename(dir_path)
    return dir_name in SKIP_DIRS

def get_filtered_files(
    dir_path: str, 
    include_regex: Optional[str] = None,
    include_patterns: Optional[List[str]] = None,
    blacklist_files: Optional[List[str]] = None,
    ignore_patterns: List[str] = DEFAULT_IGNORE_PATTERNS,
    verbose: bool = False
) -> List[str]:
    all_files = []
    
    if include_patterns is not None:
        # Use provided glob patterns
        for pattern in include_patterns:
            pattern_path = os.path.join(dir_path, '**', pattern)
            matched = glob.glob(pattern_path, recursive=True)
            # Filter out files from directories that should be skipped
            filtered_matches = []
            for file_path in matched:
                # Skip files in directories we want to exclude
                parts = os.path.normpath(file_path).split(os.sep)
                if not any(skip_dir in parts for skip_dir in SKIP_DIRS):
                    filtered_matches.append(file_path)
            all_files.extend(filtered_matches)
    else:
        # Either regex or walk the directory
        for root, dirs, files in os.walk(dir_path):
            # Skip directories that should be excluded
            dirs[:] = [d for d in dirs if not should_skip_dir(os.path.join(root, d))]
            
            for file in files:
                file_path = os.path.join(root, file)
                if include_regex is None or re.search(include_regex, file_path, re.IGNORECASE):
                    all_files.append(file_path)
    
    # Files at this point should be all valid files (not directories)
    # and have passed basic filtering
    if verbose:
        print(f"[thepipe] Initially found {len(all_files)} files in {dir_path}")
    
    # Apply more detailed pattern matching using ignore_patterns
    excluded_files = set()
    
    # Process ignore patterns
    for file_path in all_files:
        rel_path = os.path.relpath(file_path, dir_path)
        # Convert to forward slashes for pattern matching
        rel_path_forward = rel_path.replace(os.sep, '/')
        
        # Check each pattern
        for pattern in ignore_patterns:
            # Convert pattern to use forward slashes
            pattern = pattern.replace(os.sep, '/')
            
            # Use fnmatch for glob-style pattern matching
            if fnmatch.fnmatch(rel_path_forward, pattern):
                excluded_files.add(file_path)
                break
    
    # Load blacklist patterns from files
    if blacklist_files:
        blacklist_patterns = []
        
        for blacklist_file in blacklist_files:
            blacklist_path = os.path.join(dir_path, blacklist_file)
            if os.path.exists(blacklist_path) and os.path.isfile(blacklist_path):
                if verbose:
                    print(f"[thepipe] Using blacklist file: {blacklist_path}")
                
                try:
                    with open(blacklist_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            # Skip empty lines and comments
                            if not line or line.startswith('#'):
                                continue
                                
                            # Process the pattern
                            if line.startswith('!'):
                                # Negation pattern - handle these specially
                                continue  # Simplify by skipping negation patterns
                                
                            # Standard pattern
                            if line.startswith('/'):
                                # Root-relative pattern
                                pattern = line[1:]
                            else:
                                # Match anywhere pattern
                                pattern = f"**/{line}"
                                
                            # Handle directory patterns (trailing slash)
                            if pattern.endswith('/'):
                                pattern += '**'
                                
                            blacklist_patterns.append(pattern)
                    
                    if verbose and blacklist_patterns:
                        print(f"[thepipe] Loaded {len(blacklist_patterns)} patterns from {blacklist_path}")
                        
                except Exception as e:
                    if verbose:
                        print(f"[thepipe] Error reading blacklist file {blacklist_file}: {e}")
                        
        # Apply blacklist patterns
        if blacklist_patterns:
            for file_path in all_files:
                if file_path in excluded_files:
                    continue  # Already excluded
                    
                rel_path = os.path.relpath(file_path, dir_path)
                rel_path_forward = rel_path.replace(os.sep, '/')
                
                for pattern in blacklist_patterns:
                    pattern = pattern.replace(os.sep, '/')
                    if fnmatch.fnmatch(rel_path_forward, pattern):
                        excluded_files.add(file_path)
                        break
    
    # Create final filtered list
    filtered_files = [f for f in all_files if f not in excluded_files]
    
    if verbose:
        ignored_count = len(all_files) - len(filtered_files)
        print(f"[thepipe] Excluded {ignored_count} files based on ignore patterns and blacklists")
        print(f"[thepipe] Final file count: {len(filtered_files)}")
    
    return filtered_files

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