# cookie_utils.py
import json
import sys
import os
import webbrowser
from typing import Dict, List, Optional, Any, Union, Tuple
from urllib.parse import urlparse
import rookiepy
from .core import Chunk
from .enums import BrowserType

def get_default_browser() -> Optional[Tuple[str, str]]:
    """Get the default browser type and path using webbrowser module."""
    try:
        browser = webbrowser.get()
        
        if hasattr(browser, "name"):
            browser_name = browser.name.lower()
            
            if hasattr(browser, "basename"):
                browser_path = browser.basename
                if sys.platform == 'win32' and not os.path.isfile(browser_path):
                    program_files = [
                        os.environ.get('PROGRAMFILES', 'C:\\Program Files'),
                        os.environ.get('PROGRAMFILES(X86)', 'C:\\Program Files (x86)')
                    ]
                    for pf in program_files:
                        potential_path = os.path.join(pf, browser_path)
                        if os.path.isfile(potential_path):
                            browser_path = potential_path
                            break
            else:
                args = getattr(browser, 'args', [''])
                browser_path = args[0] if args else ''
            
            browser_mapping = {
                'firefox': BrowserType.FIREFOX.value,
                'mozilla': BrowserType.FIREFOX.value,
                'chrome': BrowserType.CHROME.value,
                'google-chrome': BrowserType.CHROME.value,
                'chromium': BrowserType.CHROMIUM.value,
                'safari': BrowserType.SAFARI.value,
                'edge': BrowserType.EDGE.value,
                'msedge': BrowserType.EDGE.value,
                'brave': BrowserType.BRAVE.value
            }
            
            for key in browser_mapping:
                if key in browser_name or key in browser_path.lower():
                    return browser_mapping[key], browser_path
                
    except webbrowser.Error:
        pass
    
    return get_system_default_browser()

def get_system_default_browser() -> Optional[Tuple[str, str]]:
    """Get system default browser based on platform."""
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                            r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice') as key:
                prog_id = winreg.QueryValueEx(key, 'ProgId')[0]
                
                if 'Firefox' in prog_id:
                    return BrowserType.FIREFOX.value, r'C:\Program Files\Mozilla Firefox\firefox.exe'
                elif 'Chrome' in prog_id:
                    return BrowserType.CHROME.value, r'C:\Program Files\Google\Chrome\Application\chrome.exe'
                elif 'Edge' in prog_id:
                    return BrowserType.EDGE.value, r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
                elif 'Brave' in prog_id:
                    return BrowserType.BRAVE.value, r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe'
        except:
            pass
    elif sys.platform == 'darwin':
        try:
            import subprocess
            result = subprocess.run(['defaults', 'read', 'com.apple.LaunchServices/com.apple.launchservices.secure', 
                                'LSHandlers'], capture_output=True, text=True)
            output = result.stdout.lower()
            
            if 'firefox' in output:
                return BrowserType.FIREFOX.value, '/Applications/Firefox.app/Contents/MacOS/firefox'
            elif 'chrome' in output:
                return BrowserType.CHROME.value, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            elif 'safari' in output:
                return BrowserType.SAFARI.value, '/Applications/Safari.app/Contents/MacOS/Safari'
            elif 'brave' in output:
                return BrowserType.BRAVE.value, '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'
        except:
            pass
    return None

def get_domain_cookies(domain_or_url: str, browser_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get cookies for a specific domain using rookiepy.
    
    Args:
        domain_or_url: Either a domain (e.g., '.google.com') or full URL
        browser_type: Optional browser to use for cookie extraction
    """
    # If it's a URL, extract the domain
    if '://' in domain_or_url:
        domain = urlparse(domain_or_url).netloc
    else:
        # It's already a domain pattern
        domain = domain_or_url

    try:
        if browser_type:
            browser_func = getattr(rookiepy, browser_type.lower(), None)
            if not browser_func:
                raise ValueError(f"Unsupported browser type: {browser_type}")
            cookies = browser_func(domains=[domain])
        else:
            cookies = rookiepy.load(domains=[domain])
        return cookies
    except Exception as e:
        raise ValueError(f"Failed to get cookies for {domain}: {e}")
    
def process_cookie_options(url: str, chunks: List[Chunk], 
                       cookie_options: Optional[Dict[str, Any]] = None) -> Union[List[Chunk], str]:
    """
    Process cookie options and return appropriate response.
    
    Args:
        url: The URL to get cookies for
        chunks: Existing chunks from content extraction
        cookie_options: Dictionary containing:
            - show: "" for schema, "credentials" for values, "test" for test mode
            - browser_type: Optional browser to use
            - to_terminal: Whether to print test results to terminal instead of chunk
    """
    if not cookie_options:
        return chunks

    browser_type = cookie_options.get('browser_type')
    show_mode = cookie_options.get('show', '')
    to_terminal = cookie_options.get('to_terminal', True)

    try:
        cookies = get_domain_cookies(url, browser_type)
        
        if show_mode == "test":
            cookie_data = json.dumps(cookies, indent=2)
            if to_terminal:
                # Return as string for terminal output
                return f"Cookies for {url}:\n{cookie_data}"
            else:
                # Add as chunk
                chunks.append(Chunk(
                    path=f"{url}#cookie-test",
                    texts=[cookie_data]
                ))
        elif show_mode == "credentials":
            chunks.append(Chunk(
                path=f"{url}#cookies",
                texts=[json.dumps(cookies, indent=2)]
            ))
        elif show_mode == "":
            # Add schema as a chunk
            schema = """
type Cookie {
    name: String!           # Name of the cookie
    value: String          # Value stored in the cookie
    domain: String!        # Domain the cookie belongs to
    path: String!          # Path the cookie is valid for
    expires: Int          # Expiration timestamp
    secure: Boolean!      # Whether cookie requires HTTPS
    http_only: Boolean!   # Whether cookie is HTTP only
}

type CookieJar {
    cookies: [Cookie!]!    # List of cookies
    domain: String!        # Domain these cookies are from
    timestamp: String!     # When cookies were retrieved
}"""
            chunks.append(Chunk(
                path=f"{url}#cookie-schema",
                texts=[schema]
            ))

    except Exception as e:
        error_msg = f"Error retrieving cookies: {str(e)}"
        if show_mode == "test" and to_terminal:
            return error_msg
        chunks.append(Chunk(
            path=f"{url}#cookies-error",
            texts=[error_msg]
        ))

    return chunks