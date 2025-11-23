import fnmatch
from io import BytesIO
import io
import os
import re
from typing import Any, Dict, List, Optional, Set, Union
from urllib.parse import urlparse
from tld import get_fld
from tld.exceptions import TldDomainNotFound, TldBadUrl

import markdownify

from thepipe.core import HOST_IMAGES, Chunk, make_image_url

from PIL import Image

USER_AGENT_STRING: str = os.getenv("USER_AGENT_STRING", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3")

DRIVE_DOMAINS = ['drive.google.com','docs.google.com']

TWITTER_DOMAINS = ['twitter.com', 'x.com']

GIT_DOMAINS = ['github.com','gitlab.com','bitbucket.org','git.*',  '*/git',  '*/gitlab','*/gitea','*/gerrit','dev.azure.com','codecommit.*.amazonaws.com','sourceforge.net','codeberg.org','gitea.io']

SCRAPING_PROMPT = os.getenv("EXTRACTION_PROMPT", """An open source document is given. Output the entire extracted contents from the document in detailed markdown format.
Be sure to correctly format markdown for headers, paragraphs, lists, tables, menus, equations, full text contents, etc.
Always reply immediately with only markdown. Do not output anything else.""")

DEFAULT_AI_MODEL = os.getenv("DEFAULT_AI_MODEL", "gpt-4o-mini")

def extract_page_content(url: str, text_only: bool = False, verbose: bool = False, options: Optional[Dict[str, Any]] = None, include_output_images: bool = True) -> Chunk:
    from urllib.parse import urlparse
    from bs4 import BeautifulSoup
    from playwright.sync_api import sync_playwright
    import base64
    import requests
    
    texts = []
    images = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT_STRING)
        page = context.new_page()
        page.goto(url, wait_until='domcontentloaded')
        
        # Scroll to the bottom of the page to load dynamic content
        viewport_height = page.viewport_size['height']
        total_height = page.evaluate("document.body.scrollHeight")
        current_scroll_position = 0
        scrolldowns, max_scrolldowns = 0, 20  # Finite to prevent infinite scroll
        
        while current_scroll_position < total_height and scrolldowns < max_scrolldowns:
            page.wait_for_timeout(1000)  # Wait for dynamic content to load
            current_scroll_position += viewport_height
            page.evaluate(f"window.scrollTo(0, {current_scroll_position})")
            scrolldowns += 1
            total_height = page.evaluate("document.body.scrollHeight")
        
        # Extract HTML content
        html_content = page.content()
        
        # Convert HTML to Markdown
        soup = BeautifulSoup(html_content, 'html.parser')
        markdown_content = markdownify.markdownify(str(soup), heading_style="ATX")
        
        # Remove excessive newlines in the markdown
        markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)
        markdown_content = markdown_content.strip()

        texts.append(markdown_content)
        
        if include_output_images and not text_only:
            # Extract images from the page using heuristics
            for img in page.query_selector_all('img'):
                img_path = img.get_attribute('src')
                if not img_path:
                    continue
                if img_path.startswith('data:image'):
                    # Save base64 image to PIL Image
                    decoded_data = base64.b64decode(img_path.split(',')[1])
                    try:
                        image = Image.open(BytesIO(decoded_data))
                        images.append(image)
                    except Exception as e:
                        if verbose: print(f"[thepipe] Ignoring error loading image {img_path}: {e}")
                        continue  # Ignore incompatible image extractions
                else:
                    try:
                        image = Image.open(requests.get(img_path, stream=True).raw)
                        images.append(image)
                    except:
                        if 'https://' not in img_path and 'http://' not in img_path:
                            try:
                                while img_path.startswith('/'):
                                    img_path = img_path[1:]
                                path_with_schema = urlparse(url).scheme + "://" + img_path
                                image = Image.open(requests.get(path_with_schema, stream=True).raw)
                                images.append(image)
                            except:
                                try:
                                    path_with_schema_and_netloc = urlparse(url).scheme + "://" + urlparse(url).netloc + "/" + img_path
                                    image = Image.open(requests.get(path_with_schema_and_netloc, stream=True).raw)
                                    images.append(image)
                                except:
                                    if verbose: print(f"[thepipe] Ignoring error loading image {img_path}")
                                    continue  # Ignore incompatible image extractions
                        else:
                            if verbose: print(f"[thepipe] Ignoring error loading image {img_path}")
                            continue  # Ignore incompatible image extractions
                
        browser.close()
    
    text = "\n".join(texts).strip() if texts else ""
    return Chunk(path=url, text=text, images=images)

def ai_extract_webpage_content(url: str, text_only: Optional[bool] = False, verbose: Optional[bool] = False, ai_model: Optional[str] = DEFAULT_AI_MODEL) -> Chunk:
    from playwright.sync_api import sync_playwright
    from openai import OpenAI

    #import modal
    #app_name = "scrape-ui"
    #function_name = "get_ui_layout_preds"
    #fn = modal.Function.lookup(app_name, function_name)
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=USER_AGENT_STRING)
        page = context.new_page()
        page.goto(url, wait_until='domcontentloaded')
        
        viewport_height = page.viewport_size['height']
        total_height = page.evaluate("document.body.scrollHeight")
        current_scroll_position = 0
        scrolldowns, max_scrolldowns = 0, 3
        images = []

        while current_scroll_position < total_height and scrolldowns < max_scrolldowns:
            page.wait_for_timeout(1000)
            screenshot = page.screenshot(full_page=False)
            img = Image.open(io.BytesIO(screenshot))
            images.append(img)

            current_scroll_position += viewport_height
            page.evaluate(f"window.scrollTo(0, {current_scroll_position})")
            scrolldowns += 1
            total_height = page.evaluate("document.body.scrollHeight")
        
        browser.close()

    if images:
        # Vertically stack the images
        total_height = sum(img.height for img in images)
        max_width = max(img.width for img in images)
        stacked_image = Image.new('RGB', (max_width, total_height))
        y_offset = 0
        for img in images:
            stacked_image.paste(img, (0, y_offset))
            y_offset += img.height

        # Process the stacked image with the UI model
        #figures = fn.remote(stacked_image)

        # Process the stacked image with VLM
        openrouter_client = OpenAI(
            base_url=os.environ["LLM_SERVER_BASE_URL"],
            api_key=os.environ["LLM_SERVER_API_KEY"],
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": make_image_url(stacked_image, host_images=HOST_IMAGES)},
                    {"type": "text", "text": SCRAPING_PROMPT},
                ]
            },
        ]
        response = openrouter_client.chat.completions.create(
            model=ai_model,
            messages=messages,
            temperature=0
        )
        llm_response = response.choices[0].message.content
        chunk = Chunk(path=url, text=llm_response, images=[stacked_image] if not text_only else [])
    else:
        raise ValueError("Model received 0 images from webpage")

    return chunk

def normalize_url(url: str) -> str:
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def matches_domain(url: str, domains: Union[List[str], Set[str]], verbose: bool = False) -> bool:

    try:
        # Get the main domain and full hostname
        domain = get_fld(url, fix_protocol=True, fail_silently=False)
        hostname = urlparse(url).netloc.lower()
        
        for pattern in domains:
            # Direct domain match
            if pattern == domain or pattern == hostname:
                return True
                
            # Pattern matching for wildcards
            if '*' in pattern:
                if fnmatch.fnmatch(hostname, pattern) or fnmatch.fnmatch(domain, pattern):
                    return True
        
        return False
        
    except (TldDomainNotFound, TldBadUrl) as e:
        if verbose:
            print(f"[thepipe] Domain parsing error: {str(e)}")
        return False
    except Exception as e:
        if verbose:
            print(f"[thepipe] Domain matching error: {str(e)}")
        return False