import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Set
from pathlib import Path
import json
import logging
import os
import requests
import tempfile
import io
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from .core import Chunk

logger = logging.getLogger(__name__)

@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    parent_folder: Optional[str]
    size: Optional[int] = None
    download_url: Optional[str] = None
    relative_path: Optional[str] = None

class DriveFolderCrawler:
    def __init__(self, max_depth: int = 3, verbose: bool = False):
        self.max_depth = max_depth
        self.verbose = verbose
        self.files: Dict[str, DriveFile] = {}
        self.folders: Set[str] = set()
        self.visited: Set[str] = set()

    def get_drive_api_key(self) -> Optional[str]:
        """Get Drive API key from config file or module."""
        try:
            # First try the module
            from .drive_api import DRIVE_API_KEY
            if DRIVE_API_KEY:
                if self.verbose:
                    print("[thepipe] Using Drive API key from module")
                return DRIVE_API_KEY
        except ImportError:
            if self.verbose:
                print("[thepipe] No Drive API key found in module")

        # Then try config file
        try:
            config_path = Path.home() / '.thepipe' / 'drive_api_key.json'
            if not config_path.exists():
                if self.verbose:
                    print("[thepipe] No Drive API key config file found at:", config_path)
                return None

            with open(config_path) as f:
                content = f.read().strip()
                if not content:
                    if self.verbose:
                        print("[thepipe] Drive API key config file is empty")
                    return None

                try:
                    config = json.loads(content)
                except json.JSONDecodeError:
                    if self.verbose:
                        print("[thepipe] Drive API key config file contains invalid JSON")
                    return None

                key = config.get('drive_api_key')
                if not key:
                    if self.verbose:
                        print("[thepipe] No 'drive_api_key' found in config")
                    return None

                if self.verbose:
                    print("[thepipe] Using Drive API key from config file")
                return key

        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error loading Drive API key config: {str(e)}")
                print("[thepipe] Please ensure your drive_api_key.json is properly configured")
                print("[thepipe] Run: echo '{\"drive_api_key\": \"YOUR_API_KEY\"}' > ~/.thepipe/drive_api_key.json")

        return None

    def get_folder_contents(self, folder_id: str, depth: int = 0) -> List[DriveFile]:
        """Get contents of a folder using Drive API v3."""
        if depth > self.max_depth or folder_id in self.visited:
            return []
                
        self.visited.add(folder_id)
        self.folders.add(folder_id)
        
        if self.verbose:
            print(f"[thepipe] Scanning folder: {folder_id} (depth {depth})")

        try:
            url = "https://www.googleapis.com/drive/v3/files"
            params = {
                'q': f"'{folder_id}' in parents",
                'key': 'AIzaSyCFGnys4kCyv9rRZ9hoDjpyfl1jviZYU9c'  # Hardcode working key for test
            }

            if self.verbose:
                # Print exact URL that would be used
                constructed_url = f"{url}?q='{folder_id}'+in+parents&key={params['key']}"
                print(f"[thepipe] Using URL: {constructed_url}")
                print(f"[thepipe] Curl equivalent: curl \"{constructed_url}\"")

            # Make request exactly like curl
            response = requests.get(url, params=params)
            
            if self.verbose:
                print(f"[thepipe] Response status: {response.status_code}")
                print(f"[thepipe] Response: {response.text[:200]}")  # First 200 chars of response

            if response.status_code != 200:
                raise Exception(f"API request failed with status {response.status_code}: {response.text}")

            data = response.json()
            files = data.get('files', [])
            
            if self.verbose:
                print(f"[thepipe] Found {len(files)} items in folder")

            for file in files:
                file_id = file.get('id')
                if not file_id:
                    continue
                    
                if file_id not in self.files and file_id not in self.folders:
                    mime_type = file.get('mimeType', 'unknown')
                    
                    if mime_type == 'application/vnd.google-apps.folder':
                        if depth < self.max_depth:
                            self.get_folder_contents(file_id, depth + 1)
                    else:
                        drive_file = DriveFile(
                            id=file_id,
                            name=file.get('name', 'Unnamed'),
                            mime_type=mime_type,
                            parent_folder=folder_id,
                            download_url=f"https://drive.google.com/uc?export=download&id={file_id}"
                        )
                        self.files[file_id] = drive_file
                        
                        if self.verbose:
                            print(f"[thepipe] Added file: {drive_file.name}")

        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error accessing Drive API: {str(e)}")
            raise

        if self.verbose:
            print(f"[thepipe] Found total of {len(self.files)} files in folder {folder_id}")

        return list(self.files.values())

    def process_files(self, output_dir: str = "drive_downloads") -> List[Chunk]:
        """Process all collected files."""
        chunks = []
        
        if self.verbose:
            print(f"[thepipe] Processing {len(self.files)} files from folder")
        
        for file_id, drive_file in self.files.items():
            try:
                if self.verbose:
                    print(f"[thepipe] Processing: {drive_file.name}")
                
                # Get file content
                content = self.download_file(file_id)
                if not content:
                    continue

                # Process with temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix=self.get_extension(drive_file)) as temp_file:
                    temp_file.write(content)
                    temp_path = temp_file.name

                try:
                    # Process file with scraper
                    from .scraper import scrape_file
                    file_chunks = scrape_file(
                        filepath=temp_path,
                        verbose=self.verbose
                    )
                    
                    # Update paths to include original drive path
                    for chunk in file_chunks:
                        chunk.path = f"drive://{file_id}/{drive_file.name}"
                    
                    chunks.extend(file_chunks)
                    
                finally:
                    # Clean up temporary file
                    try:
                        os.unlink(temp_path)
                    except Exception as e:
                        if self.verbose:
                            print(f"[thepipe] Warning: Could not remove temporary file {temp_path}: {e}")
                
            except Exception as e:
                if self.verbose:
                    print(f"[thepipe] Error processing {drive_file.name}: {e}")
                continue
        
        return chunks

    def download_file(self, file_id: str) -> Optional[bytes]:
        """Download a file from Drive."""
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        
        try:
            session = requests.Session()
            response = session.get(url, stream=True)
            
            if response.status_code == 200:
                # Handle download warning page
                if 'download_warning' in response.cookies:
                    token = response.cookies['download_warning']
                    response = session.get(f"{url}&confirm={token}", stream=True)
                    
                return response.content
                
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error downloading file {file_id}: {e}")
                
        return None

    def get_extension(self, drive_file: DriveFile) -> str:
        """Get file extension based on MIME type."""
        mime_to_ext = {
            'application/pdf': '.pdf',
            'text/plain': '.txt',
            'application/vnd.google-apps.document': '.txt',
            'application/vnd.google-apps.spreadsheet': '.csv',
            'application/vnd.google-apps.presentation': '.pdf'
        }
        
        # Try to get extension from filename first
        name_ext = os.path.splitext(drive_file.name)[1]
        if name_ext:
            return name_ext
            
        # Fall back to MIME type mapping
        return mime_to_ext.get(drive_file.mime_type, '.bin')
    
def is_folder_url(url: str) -> bool:
    """Check if URL is a Drive folder."""
    return bool(re.search(r'drive\.google\.com/(?:drive/)?folders/|drive\.google\.com/drive/u/\d+/folders/', url))

def extract_drive_id(url: str) -> Optional[str]:
    """Extract folder or file ID from Drive URL."""
    parsed_url = urlparse(url)
    
    # Direct file links
    file_match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', parsed_url.path)
    if file_match:
        return file_match.group(1)
    
    # Folder links
    folder_match = re.search(r'folders/([a-zA-Z0-9_-]+)', parsed_url.path)
    if folder_match:
        return folder_match.group(1)
    
    # Google Doc types (docs, sheets, presentations)
    doc_match = re.search(r'/(?:document|presentation|spreadsheets)/d/([a-zA-Z0-9_-]+)', parsed_url.path)
    if doc_match:
        return doc_match.group(1)
    
    # Query parameter IDs (used in open/uc links)
    query_params = parse_qs(parsed_url.query)
    if 'id' in query_params:
        return query_params['id'][0]
    
    return None

def init_drive_service(service_account_info: Optional[Dict] = None, 
                      service_account_file: Optional[str] = None):
    """Initialize Google Drive service with provided credentials."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google Drive API libraries not found. Please install them with:\n"
            "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )

    SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

    try:
        if service_account_info:
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=SCOPES
            )
        elif service_account_file:
            credentials = service_account.Credentials.from_service_account_file(
                service_account_file,
                scopes=SCOPES
            )
        else:
            raise ValueError(
                "Authentication required. Please provide service account credentials via either:\n"
                "1. service_account_file: Path to JSON key file\n"
                "2. service_account_info: Dict containing service account credentials"
            )

        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        raise ValueError(f"Failed to initialize Drive service: {str(e)}")

def get_file_metadata(file_id: str) -> Optional[str]:
    """Get filename from Drive file without authentication."""
    import requests
    print(f"reading {file_id}")
    # Try to get filename from the public presentation/doc page
    urls_to_try = [
        f"https://docs.google.com/presentation/d/{file_id}/view",
        f"https://docs.google.com/document/d/{file_id}/view",
        f"https://docs.google.com/spreadsheets/d/{file_id}/view"
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    for url in urls_to_try:
        try:
            response = requests.get(url, headers=headers, allow_redirects=True)
            print(f"request to {url}")
            if response.status_code == 200:
                print(f"succeeded with {url}")
                print((response.text[0:300]))
                match = re.search(r'<title>(.*?)(?:\s*[-–]\s*Google\s+(?:Docs|Slides|Sheets))?</title>', 
                                response.text, 
                                re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        except (requests.RequestException, OSError) as e:
            logger.debug(f"Failed to fetch metadata from {url}: {e}")
            continue
            
    return None

def get_mime_type(file_metadata: Dict[str, Any]) -> Tuple[str, str]:
    mime_type = file_metadata['mimeType']
    
    # Handle Google Workspace files
    if mime_type.startswith('application/vnd.google-apps'):
        if 'document' in mime_type:
            return 'text/plain', '.txt'
        elif 'spreadsheet' in mime_type:
            return 'text/csv', '.csv'
        elif 'presentation' in mime_type:
            return 'text/plain', '.pdf'
        else:
            return 'text/plain', '.txt'
    
    # Handle regular files
    extension = os.path.splitext(file_metadata['name'])[1]
    if not extension:
        if 'text' in mime_type:
            extension = '.txt'
        elif 'pdf' in mime_type:
            extension = '.pdf'
        else:
            extension = '.bin'
            
    return mime_type, extension

def is_workspace_doc(url: str, mime_type: Optional[str] = None) -> bool:
    """
    Check if the file is a Google Workspace document that needs export.
    """
    workspace_patterns = {
        'document': 'application/vnd.google-apps.document',
        'presentation': 'application/vnd.google-apps.presentation',
        'spreadsheet': 'application/vnd.google-apps.spreadsheet'
    }
    
    # Check URL patterns first
    if any(f"/{doc_type}/" in url.lower() for doc_type in workspace_patterns.keys()):
        return True
        
    # Check MIME type if available
    if mime_type and any(mime_type.startswith(wtype) for wtype in workspace_patterns.values()):
        return True
        
    return False

def try_public_access(file_id: str, original_url: str = "", verbose: bool = False) -> Optional[Tuple[bytes, str]]:
    """Try to access file as public without authentication."""
    import requests
    import mimetypes
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*'
    }
    
    # Try to get file metadata first (includes MIME type)
    metadata_url = f"https://drive.google.com/file/d/{file_id}/view"
    try:
        response = requests.get(metadata_url, headers=headers)
        content = response.text.lower()
        
        # Try to detect file type from page content
        type_match = re.search(r'<div.*?data-mime-type=["\']([^"\']+)["\']', content)
        mime_type = type_match.group(1) if type_match else None
        
        if verbose and mime_type:
            print(f"[thepipe] Detected MIME type: {mime_type}")
    except Exception as e:
        if verbose:
            print(f"[thepipe] Could not detect MIME type: {e}")
        mime_type = None

    # For Workspace docs, use export
    if is_workspace_doc(original_url, mime_type):
        if verbose:
            print("[thepipe] Detected Google Workspace document, using export...")
        return try_export_url(file_id, original_url, verbose)

    # For regular files, try direct download
    download_urls = [
        f"https://drive.google.com/uc?id={file_id}&export=download",
        f"https://drive.google.com/uc?id={file_id}",
    ]

    session = requests.Session()
    
    for url in download_urls:
        try:
            if verbose:
                print(f"[thepipe] Attempting direct download: {url}")
            
            response = session.get(url, headers=headers, allow_redirects=True)
            
            # Handle download warning/confirmation page
            if 'quota exceeded' in response.text.lower():
                if verbose:
                    print("[thepipe] Download quota exceeded, trying alternative method...")
                continue
                
            if 'verify=t' in response.url or 'confirm=t' in response.url:
                if verbose:
                    print("[thepipe] Handling download confirmation...")
                # Extract confirmation token
                token_match = re.search(r'"([^"]+)"', response.text)
                if token_match:
                    confirm_token = token_match.group(1)
                    url = f"{url}&confirm={confirm_token}"
                    response = session.get(url, headers=headers)

            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                
                # Skip if we got an HTML error page
                if 'text/html' in content_type and 'google' in response.text.lower():
                    if verbose:
                        print("[thepipe] Received HTML error page, skipping...")
                    continue
                
                # Try to get filename and extension from headers
                cd = response.headers.get('content-disposition')
                if cd:
                    fname = re.findall("filename=(.+)", cd)
                    if fname:
                        filename = fname[0].strip('"')
                        extension = os.path.splitext(filename)[1]
                        if extension:
                            return response.content, extension
                
                # Fallback to mime type for extension
                extension = mimetypes.guess_extension(content_type)
                if not extension:
                    # Common mappings that might be missing
                    ext_map = {
                        'application/pdf': '.pdf',
                        'image/jpeg': '.jpg',
                        'image/png': '.png',
                        'application/zip': '.zip',
                        'text/plain': '.txt'
                    }
                    extension = ext_map.get(content_type, '.bin')
                
                return response.content, extension

        except Exception as e:
            if verbose:
                print(f"[thepipe] Download attempt failed: {str(e)}")
            continue

    # If all direct downloads fail, try export as last resort
    if verbose:
        print("[thepipe] Direct download failed, trying export as fallback...")
    return try_export_url(file_id, original_url, verbose)

def try_export_url(file_id: str, original_url: str, verbose: bool = False) -> Optional[Tuple[bytes, str]]:
    """Try to export Google Workspace document."""
    import requests

    if verbose:
        print("[thepipe] Attempting document export...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    export_formats = []
    if '/presentation/' in original_url:
        export_formats = [
            ('pdf', '.pdf'),
            ('txt', '.txt')
        ]
    elif '/document/' in original_url:
        export_formats = [
            ('docx', '.docx'),
            ('pdf', '.pdf'),
            ('txt', '.txt')
        ]
    elif '/spreadsheets/' in original_url:
        export_formats = [
            ('xlsx', '.xlsx'),
            ('csv', '.csv'),
            ('pdf', '.pdf')
        ]
    else:
        if verbose:
            print("[thepipe] Unknown document type")
        return None

    for format_type, extension in export_formats:
        url = f"https://docs.google.com/{urlparse(original_url).path.split('/')[1]}/d/{file_id}/export?format={format_type}"
        try:
            if verbose:
                print(f"[thepipe] Trying export as {format_type}: {url}")
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                return response.content, extension
        except Exception as e:
            if verbose:
                print(f"[thepipe] Export attempt failed: {str(e)}")
            continue

    return None

def download_file(file_id: str, service) -> Tuple[bytes, str]:
    """Download a file using the Drive API."""
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError:
        raise ImportError("Google API libraries not found")

    try:
        file_metadata = service.files().get(
            fileId=file_id,
            fields='name,mimeType,size'
        ).execute()
        
        mime_type, extension = get_mime_type(file_metadata)
        
        if file_metadata['mimeType'].startswith('application/vnd.google-apps'):
            request = service.files().export_media(
                fileId=file_id,
                mimeType=mime_type
            )
        else:
            request = service.files().get_media(fileId=file_id)
            
        file_handle = io.BytesIO()
        downloader = MediaIoBaseDownload(file_handle, request)
        
        done = False
        while not done:
            _, done = downloader.next_chunk()

        return file_handle.getvalue(), extension

    except Exception as e:
        raise ValueError(
            f"Failed to download file: {str(e)}\n"
            f"Please verify:\n"
            f"1. The file exists\n"
            f"2. You have permission to access it\n"
            f"3. Service account has proper access rights"
        )

def process_drive_content(
    drive_url: str,
    drive_id: str,
    text_only: bool = False,
    verbose: bool = False,
    options: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    """Process Google Drive content with public or authenticated access."""
    if verbose:
        print(f"[thepipe] Processing Drive content: {drive_url}")

    # Check if it's a folder first
    if is_folder_url(drive_url):
        if verbose:
            print(f"[thepipe] Detected Drive folder: {drive_url}")
        
        max_depth = options.get('max_depth', 3) if options else 3
        crawler = DriveFolderCrawler(max_depth=max_depth, verbose=verbose)
        
        # First get the contents
        folder_files = crawler.get_folder_contents(drive_id)
        
        if verbose:
            print(f"[thepipe] Found {len(folder_files)} files in folder")
        
        # Then process them
        return crawler.process_files()

    # Rest of the function remains the same for single file handling
    filename = get_file_metadata(drive_id)
    if filename and verbose:
        print(f"[thepipe] Found file name: {filename}")

    # Try public access
    public_result = try_public_access(drive_id, original_url=drive_url, verbose=verbose)
    if public_result:
        content, extension = public_result
    else:
        try:
            service = init_drive_service(
                service_account_info=options.get('service_account_info') if options else None,
                service_account_file=options.get('service_account_file') if options else None
            )
        except ValueError as e:
            if "Authentication required" in str(e):
                return [Chunk(
                    path=drive_url,
                    text="This Google Drive file requires authentication.\n"
                          "Please provide service account credentials via options:\n"
                          '--options \'{"service_account_file": "path/to/credentials.json"}\'\n'
                          "Or provide the service account JSON directly in service_account_info"
                )]
            raise

        try:
            content, extension = download_file(drive_id, service)
        except Exception as e:
            error_msg = str(e)
            if "File not found" in error_msg:
                error_msg = f"File not found. Please verify the file exists and you have permission to access it."
            elif "access not granted" in error_msg.lower():
                error_msg = f"Access denied. Please verify the service account has proper access rights."
                
            if verbose:
                print(f"[thepipe] Error processing Drive file: {error_msg}")
                
            return [Chunk(
                path=drive_url,
                text=f"Failed to process Google Drive file: {error_msg}"
            )]

    # Process the content
    from .scraper import scrape_file
    
    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file_path = temp_file.name
            temp_file.write(content)
            
        file_chunks = scrape_file(
            filepath=temp_file_path,
            text_only=text_only,
            verbose=verbose,
            options=options
        )
        
        # Construct clean file path
        base_path = f"drive://{drive_id}/{filename if filename else 'document'}{extension}"
        
        # Update paths for all chunks
        for chunk in file_chunks:
            chunk.path = base_path
        
        return file_chunks
        
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except OSError as e:
                logger.debug(f"Failed to delete temp file {temp_file_path}: {e}")