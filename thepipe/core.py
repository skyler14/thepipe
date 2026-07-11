import argparse
import base64
from io import BytesIO
import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import requests
from PIL import Image

try:
    from llama_index.core.schema import Document as _LlamaDocument
    from llama_index.core.schema import ImageDocument as _LlamaImageDocument
except ImportError:
    _LlamaDocument = None
    _LlamaImageDocument = None

Document = _LlamaDocument
ImageDocument = _LlamaImageDocument

# LLM provider info, defaults to openai
DEFAULT_AI_MODEL = os.getenv("DEFAULT_AI_MODEL", "gpt-4o")
DEFAULT_EMBEDDING_MODEL = os.getenv(
    "DEFAULT_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)

# for persistent images via filehosting
HOST_IMAGES = os.getenv("HOST_IMAGES", "false").lower() == "true"
HOST_URL = os.getenv("HOST_URL", "https://thepipe-api.up.railway.app")


def prepare_image(image: Image.Image) -> Image.Image:
    """Return an in-memory copy of ``image`` with its underlying resources closed."""
    try:
        image.load()
    except Exception:
        pass

    try:
        prepared_image = image.copy()
    except Exception:
        return image

    try:
        image.close()
    except Exception:
        pass

    return prepared_image


def _ensure_llama_index() -> Tuple[Any, Any]:
    global Document, ImageDocument, _LlamaDocument, _LlamaImageDocument
    if _LlamaDocument is not None and _LlamaImageDocument is not None:
        return _LlamaDocument, _LlamaImageDocument
    try:
        from llama_index.core.schema import Document as document_cls
        from llama_index.core.schema import ImageDocument as image_document_cls
    except ImportError as exc:
        raise ImportError(
            "LlamaIndex support is optional. Install it with "
            "`pip install thepipe-api[llama-index]` to use `Chunk.to_llamaindex`."
        ) from exc
    _LlamaDocument = document_cls
    _LlamaImageDocument = image_document_cls
    Document = document_cls
    ImageDocument = image_document_cls
    return document_cls, image_document_cls


def has_llama_index() -> bool:
    try:
        _ensure_llama_index()
        return True
    except ImportError:
        return False


class Chunk:
    def __init__(
        self,
        path: Optional[str] = None,
        text: Optional[str] = None,
        texts: Optional[List[str]] = None,  # Backward compatibility
        images: Optional[Iterable[Image.Image]] = None,
        audios: Optional[Iterable] = None,
        videos: Optional[Iterable] = None,
        meta: Optional[Dict[str, Any]] = None,
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

        self.images = [prepare_image(image) for image in images] if images else []
        self.audios = list(audios) if audios else []
        self.videos = list(videos) if videos else []
        self.meta = dict(meta) if meta is not None else None

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

    def to_llamaindex(self) -> Union[List[Any], List[Any]]:
        DocumentCls, ImageDocumentCls = _ensure_llama_index()
        document_text = self.text if self.text else ""
        metadata = dict(self.meta) if self.meta else {}
        if self.path:
            metadata["filepath"] = self.path

        # If we have PIL Image objects in self.images, convert them to base64 strings
        if self.images:
            image_docs: List[Any] = []
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
                    ImageDocumentCls(
                        text=document_text,
                        image=img_b64,
                        extra_info=metadata,
                    )
                )
            return image_docs

        # Fallback to plain text Document
        return [DocumentCls(text=document_text, extra_info=metadata)]

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

    def to_json(
        self,
        host_images: bool = False,
        text_only: bool = False,
        verbose: bool = False,
    ) -> Dict:
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
        if verbose and isinstance(self.meta, dict):
            data["meta"] = self.meta
        return _prune_json_data(data)

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
            path=data.get("path"),
            text=text,
            images=images,
            audios=data.get("audios"),
            videos=data.get("videos"),
            meta=data.get("meta"),
        )


def _prune_json_data(value: Any) -> Any:
    if isinstance(value, dict):
        pruned = {}
        for key, item in value.items():
            pruned_item = _prune_json_data(item)
            if pruned_item is None:
                continue
            if isinstance(pruned_item, (list, dict)) and len(pruned_item) == 0:
                continue
            pruned[key] = pruned_item
        return pruned

    if isinstance(value, list):
        return [_prune_json_data(item) for item in value]

    return value

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
    # Save the text and images to the outputs directory
    for i, chunk in enumerate(chunks):
        if chunk is None or (not chunk.text and not chunk.images):
            continue
        if chunk.path is not None:
            text += f"{chunk.path}:\n"
        if chunk.text:
            text += f"```\n{chunk.text}\n```\n"
        if not text_only and chunk.images:
            for j, image in enumerate(chunk.images):
                image.convert("RGB").save(f"{output_folder}/{i}_{j}.jpg")
    # Save the text
    with open(f"{output_folder}/prompt.txt", "w", encoding="utf-8") as file:
        file.write(text)
    if verbose:
        print(f"[thepipe] {calculate_tokens(chunks)} tokens saved to {output_folder}")
