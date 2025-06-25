import argparse
import base64
from io import BytesIO
import json
import os
import re
import time
from typing import Dict, List, Optional, Union
import requests
from PIL import Image
from llama_index.core.schema import Document, ImageDocument

# LLM provider info, defaults to openai
DEFAULT_AI_MODEL = os.getenv("DEFAULT_AI_MODEL", "gpt-4o")
DEFAULT_EMBEDDING_MODEL = os.getenv(
    "DEFAULT_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)

# for persistent images via filehosting
HOST_IMAGES = os.getenv("HOST_IMAGES", "false").lower() == "true"
HOST_URL = os.getenv("THEPIPE_API_URL", "https://thepipe-api.up.railway.app")
THEPIPE_API_KEY = os.getenv("THEPIPE_API_KEY", None)

class Chunk:
    def __init__(
        self,
        path: Optional[str] = None,
        text: Optional[str] = None,
        texts: Optional[List[str]] = None,  # Backward compatibility
        images: Optional[List[Image.Image]] = None,
        audios: Optional[List] = None,
        videos: Optional[List] = None,
    ):
        self.path = path
        
        # Handle both text and texts for backward compatibility
        if text is not None and texts is not None:
            raise ValueError("Cannot specify both 'text' and 'texts'. Use 'text' for new code.")
        elif texts is not None:
            # Convert list to single string for backward compatibility
            self.text = "\n".join(texts) if texts else None
        else:
            self.text = text
            
        self.images = images or []
        self.audios = audios or []
        self.videos = videos or []

    # Backward compatibility property
    @property
    def texts(self) -> List[str]:
        """Backward compatibility property. Returns text split by newlines."""
        if self.text:
            return [self.text]
        return []

    def __repr__(self) -> str:
        parts = []
        if self.path is not None:
            parts.append(f"path={self.path!r}")
        if self.text:
            # Show a concise preview of the text
            snippet = self.text.replace("\n", " ")
            if len(snippet) > 50:
                snippet = snippet[:47] + "..."
            parts.append(f"text_snippet={snippet!r}")
        if self.images:
            parts.append(f"images_count={len(self.images)}")
        if self.audios:
            parts.append(f"audios_count={len(self.audios)}")
        if self.videos:
            parts.append(f"videos_count={len(self.videos)}")
        content = ", ".join(parts) or "empty"
        return f"Chunk({content})"

    def __str__(self) -> str:
        return self.__repr__()

    def to_llamaindex(self) -> Union[List[Document], List[ImageDocument]]:
        document_text = self.text if self.text else ""
        metadata = {"filepath": self.path} if self.path else {}

        # If we have PIL Image objects in self.images, convert them to base64 strings
        if self.images:
            image_docs: List[ImageDocument] = []
            for img in self.images:
                # Encode the image to JPEG (or use its original format if available)
                buffer = BytesIO()
                fmt = img.format or "JPEG"
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(buffer, format=fmt)
                img_bytes = buffer.getvalue()

                # Base64‑encode and build MIME type
                img_b64 = base64.b64encode(img_bytes).decode("utf-8")

                image_docs.append(
                    ImageDocument(
                        text=document_text,
                        image=img_b64,
                        extra_info=metadata,
                    )
                )
            return image_docs

        # Fallback to plain text Document
        return [Document(text=document_text, extra_info=metadata)]

    def to_message(
        self,
        text_only: bool = False,
        host_images: bool = False,
        max_resolution: Optional[int] = None,
        include_paths: Optional[bool] = False,
    ) -> Dict:
        message_text = ""
        message = {"role": "user", "content": []}
        image_urls = (
            [
                make_image_url(image, host_images, max_resolution)
                for image in self.images
            ]
            if self.images and not text_only
            else []
        )
        img_index = 0
        text = self.text if self.text else ""
        if host_images:

            def replace_image(match):
                nonlocal img_index
                if img_index < len(image_urls):
                    url = image_urls[img_index]
                    img_index += 1
                    return f"![image]({url})"
                return match.group(
                    0
                )  # If we run out of images, leave the original text

            # Replace markdown image references with hosted URLs
            text = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", replace_image, text)
        message_text += text + "\n\n"
        # clean up, add to message
        message_text = re.sub(r"\n{3,}", "\n\n", message_text).strip()
        # Wrap the text in a path html block if it exists
        if include_paths and self.path:
            message_text = f'<Document path="{self.path}">\n{message_text}\n</Document>'
        message["content"].append({"type": "text", "text": message_text})

        # Add remaining images that weren't referenced in the text
        for image_url in image_urls:
            message["content"].append({"type": "image_url", "image_url": image_url})

        return message

    def to_json(self, host_images: bool = False, text_only: bool = False) -> Dict:
        data = {
            "path": self.path,
            "text": self.text.strip() if self.text else "",
            "images": (
                [
                    make_image_url(image=image, host_images=host_images)
                    for image in self.images
                    if not text_only
                ]
                if self.images
                else []
            ),
            "audios": self.audios,
            "videos": self.videos,
        }
        return data

    @staticmethod
    def from_json(data: Dict, host_images: bool = False) -> "Chunk":
        images = []
        if "images" in data:
            for image_str in data["images"]:
                if host_images:
                    image_data = requests.get(image_str).content
                    image = Image.open(BytesIO(image_data))
                    images.append(image)
                else:
                    remove_prefix = image_str.replace("data:image/jpeg;base64,", "")
                    image_data = base64.b64decode(remove_prefix)
                    image = Image.open(BytesIO(image_data))
                    images.append(image)
        text = data["text"].strip() if "text" in data else None
        return Chunk(
            path=data["path"],
            text=text,
            images=images,
        )

def make_image_url(
    image: Image.Image, host_images: bool = False, max_resolution: Optional[int] = None
) -> str:
    if max_resolution:
        width, height = image.size
        if width > max_resolution or height > max_resolution:
            scale = max_resolution / max(width, height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            image = image.resize((new_width, new_height))
    if host_images:
        if not os.path.exists("images"):
            os.makedirs("images")
        image_id = f"{time.time_ns()}.jpg"
        image_path = os.path.join("images", image_id)
        if image.mode in ("P", "RGBA"):
            image = image.convert("RGB")
        image.save(image_path)
        return f"{HOST_URL}/images/{image_id}"
    else:
        buffered = BytesIO()
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(buffered, format="JPEG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return f"data:image/jpeg;base64,{img_str}"

def calculate_image_tokens(image: Image.Image, detail: str = "auto") -> int:
    width, height = image.size
    if detail == "low":
        return 85
    elif detail == "high":
        width, height = min(width, 2048), min(height, 2048)
        short_side = min(width, height)
        scale = 768 / short_side
        scaled_width = int(width * scale)
        scaled_height = int(height * scale)
        tiles = (scaled_width // 512) * (scaled_height // 512)
        return 170 * tiles + 85
    else:
        if width <= 512 and height <= 512:
            return 85
        else:
            return calculate_image_tokens(image, detail="high")

def calculate_tokens(chunks: List[Chunk], text_only: bool = False) -> int:
    n_tokens = 0
    for chunk in chunks:
        if chunk.text:
            n_tokens += len(chunk.text) / 4
        if chunk.images and not text_only:
            for image in chunk.images:
                try:
                    n_tokens += calculate_image_tokens(image)
                except Exception as e:
                    print(f"[thepipe] Error calculating tokens for an image: {str(e)}")
                    # Add a default token count for failed images
                    n_tokens += 85  # Minimum token count for an image
    return int(n_tokens)

def chunks_to_messages(
    chunks: List[Chunk],
    text_only: bool = False,
    host_images: bool = False,
    max_resolution: Optional[int] = None,
    include_paths: Optional[bool] = False,
) -> List[Dict]:
    return [
        chunk.to_message(
            text_only=text_only,
            host_images=host_images,
            max_resolution=max_resolution,
            include_paths=include_paths,
        )
        for chunk in chunks
    ]

def save_outputs(
    chunks: List[Chunk],
    output_folder: str = "outputs",
    verbose: bool = False,
    text_only: bool = False,
) -> None:
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    text = ""
    current_path = None
    page_number = 1

    def is_paginated_format(path: str) -> bool:
        """Check if the file format typically has pages."""
        return path.lower().endswith('.pdf')

    # First write: output with minimal headers
    for i, chunk in enumerate(chunks):
        if chunk is None or (not chunk.text and not chunk.images):
            continue

        # Only write path when it changes
        if chunk.path != current_path:
            current_path = chunk.path
            if current_path is not None:
                if text:  # Add spacing between documents
                    text += "\n"
                text += f"{current_path}\n\n"
            page_number = 1
        elif current_path and is_paginated_format(current_path):
            # Just add page number for PDFs
            text += f"\n{page_number}\n\n"
            page_number += 1

        if chunk.text:
            text += f"```\n{chunk.text}\n```\n"
            
        if chunk.images and not text_only:
            for j, image in enumerate(chunk.images):
                try:
                    image.convert("RGB").save(f"{output_folder}/{i}_{j}.jpg")
                except Exception as e:
                    if verbose:
                        print(f"[thepipe] Error saving image at index {j} in chunk {i}: {str(e)}")

    # Clean up excessive newlines and write
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    with open(f"{output_folder}/prompt.txt", "w", encoding="utf-8") as file:
        file.write(text)

    if verbose:
        try:
            # Attempt to calculate tokens using the original method
            token_count = calculate_tokens(chunks)
            print(f"[thepipe] Approximately {token_count} tokens saved to {output_folder}")
        except Exception as e:
            # If the original method fails, fall back to a simpler estimation
            total_chars = sum(len(chunk.text or "") for chunk in chunks)
            estimated_tokens = total_chars // 4  # Rough estimate: 1 token ≈ 4 characters
            print(f"[thepipe] Error calculating exact tokens: {str(e)}")
            print(f"[thepipe] Estimated {estimated_tokens} tokens saved to {output_folder} (based on character count)")
        print(f"[thepipe] Outputs saved to '{output_folder}' folder")

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process files or display cookies."
    )
    parser.add_argument(
        "source", type=str, help="The source file, directory, URL or database to process"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--include_regex', type=str, nargs='?', const='.*', default=None, 
                       help='Regex pattern to match in a directory. Use quotes for patterns with special characters.')
    group.add_argument('--include_patterns', type=str, nargs='+', default=None,
                       help='Glob patterns to match files in a directory (e.g., "*.tsx" "*.ts"). Use quotes for patterns with special characters.')
    parser.add_argument('--ai_extraction', action='store_true', help='Use ai_extraction to extract text from images.')
    parser.add_argument('--text_only', nargs='?', const='default', default=None, 
                        choices=['default', 'transcribe', 'ai', 'uploaded'],
                        help='Extract only text from the source. Video Options: default (try all methods), transcribe (force local transcription), ai (prefer AI-generated), uploaded (prefer uploaded)')
    parser.add_argument('--verbose', action='store_true', help='Print status messages.')
    parser.add_argument('--local', action='store_true', help='Use local processing instead of API.')
    parser.add_argument('--options', type=str, help='JSON string of type-specific options')
    parser.add_argument('--browser_type', type=str, choices=['chrome', 'firefox', 'edge', 'brave', 'safari'],
                       help='Specific browser to extract cookies from')
    parser.add_argument('--show_cookies', nargs='?', const='format', choices=['format', 'credentials'],
                       help='Display cookies instead of processing content. Use "credentials" for full cookie data.')
    parser.add_argument('--db', nargs='*',
        help='Database query. Format: --db ["query"] [db_type] [mode]. '
             'If empty, shows preview. Mode can be "schema" or "preview".')

    # OpenAI-related flags
    parser.add_argument(
        "--openai-api-key",
        dest="openai_api_key",
        default=os.getenv("OPENAI_API_KEY"),
        help="OpenAI API key.  If omitted, env variable OPENAI_API_KEY is used.",
    )
    parser.add_argument(
        "--openai-base-url",
        dest="openai_base_url",
        default="https://api.openai.com/v1",
        help="Base URL for the OpenAI API (default: https://api.openai.com/v1).",
    )
    parser.add_argument(
        "--openai-model",
        dest="openai_model",
        default=DEFAULT_AI_MODEL,
        help=f"Chat/VLM model to use (default: {DEFAULT_AI_MODEL}).",
    )

    args = parser.parse_args()
    
    # Process options
    if args.options and isinstance(args.options, str):
        try:
            args.options = json.loads(args.options)
        except json.JSONDecodeError:
            print("Error: Invalid JSON in options")
            exit(1)
    elif not hasattr(args, 'options') or args.options is None:
        args.options = {}
        
    return args