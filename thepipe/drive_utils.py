import re
import json
import os
import tempfile
import io
from typing import Dict, List, Optional, Any, Tuple, BinaryIO
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from .core import Chunk

def extract_drive_id(url: str) -> Optional[str]:
    """Extract folder or file ID from Drive URL."""
    parsed_url = urlparse(url)
    
    # Handle folder URLs
    folder_match = re.search(r'folders/([a-zA-Z0-9_-]+)', parsed_url.path)
    if folder_match:
        return folder_match.group(1)
    
    # Handle all Google Doc types (docs, sheets, presentations)
    doc_match = re.search(r'/(?:document|presentation|spreadsheets)/d/([a-zA-Z0-9_-]+)', parsed_url.path)
    if doc_match:
        return doc_match.group(1)
    
    # Handle file URLs
    file_match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', parsed_url.path)
    if file_match:
        return file_match.group(1)
    
    # Handle direct links
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
            if response.status_code == 200:
                match = re.search(r'<title>(.*?)(?:\s*[-–]\s*Google\s+(?:Docs|Slides|Sheets))?</title>', 
                                response.text, 
                                re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        except:
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

def try_public_access(file_id: str, original_url: str = "", verbose: bool = False) -> Optional[Tuple[bytes, str]]:
    """Try to access file as public without authentication."""
    import requests

    # Determine type from original URL if available
    url_lower = original_url.lower()
    if '/spreadsheets/' in url_lower:
        urls_to_try = [
            (f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=csv", '.csv'),
            (f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=txt", '.txt'),  # fallback
        ]
    elif '/document/' in url_lower:
        urls_to_try = [
            (f"https://docs.google.com/document/d/{file_id}/export?format=docx", '.docx'),
            (f"https://docs.google.com/document/d/{file_id}/export?format=txt", '.txt'),  # fallback
        ]
    elif '/presentation/' in url_lower:
        urls_to_try = [
            (f"https://docs.google.com/presentation/d/{file_id}/export/pdf", '.pdf'),
            (f"https://docs.google.com/presentation/d/{file_id}/export/txt", '.txt'),  # fallback
        ]
    else:
        # Generic/unknown type - just try direct download
        urls_to_try = [(f"https://drive.google.com/uc?id={file_id}", '.bin')]

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    for url, extension in urls_to_try:
        try:
            if verbose:
                print(f"[thepipe] Trying public access URL: {url}")
            
            response = requests.get(url, headers=headers, allow_redirects=True)
            
            if verbose:
                print(f"[thepipe] Response status: {response.status_code}")
            
            if response.status_code != 200 or 'accounts.google.com' in response.url:
                continue
                
            if b'Google Drive - Error' in response.content[:1000]:
                continue

            if 'content-disposition' in response.headers:
                filename = response.headers['content-disposition']
                if verbose:
                    print(f"[thepipe] Found filename: {filename}")
                filename_match = re.search(r'filename="([^"]*)"', filename)
                if filename_match:
                    _, ext = os.path.splitext(filename_match.group(1))
                    if ext:
                        extension = ext.split(';')[0]  # Remove any encoding info

            if verbose:
                print(f"[thepipe] Successfully accessed file with extension {extension}")
            return response.content, extension

        except Exception as e:
            if verbose:
                print(f"[thepipe] Error trying URL {url}: {str(e)}")
            continue

    if verbose:
        print("[thepipe] Could not access file through public URLs")
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
    ai_extraction: bool = False,
    verbose: bool = False,
    options: Optional[Dict[str, Any]] = None
) -> List[Chunk]:
    """Process Google Drive content with public or authenticated access."""
    if verbose:
        print(f"[thepipe] Processing Drive content: {drive_url}")

    # Try to get filename first
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
                    texts=["This Google Drive file requires authentication.\n"
                          "Please provide service account credentials via options:\n"
                          '--options \'{"service_account_file": "path/to/credentials.json"}\'\n'
                          "Or provide the service account JSON directly in service_account_info"]
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
                texts=[f"Failed to process Google Drive file: {error_msg}"]
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
            ai_extraction=ai_extraction,
            verbose=verbose,
            local=True,
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
            except:
                pass