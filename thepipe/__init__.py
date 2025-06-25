from __future__ import annotations

import os
import warnings
from typing import Optional

from openai import OpenAI

from .scraper import scrape_directory, scrape_file, scrape_url, scrape_database
from .core import parse_arguments, save_outputs, DEFAULT_AI_MODEL
from .file_utils import is_database_source
from .database_utils import parse_database_args

# OpenAI client factory
def create_openai_client(
    *,
    api_key: Optional[str],
    base_url: str,
    enable_vlm: bool,
) -> Optional[OpenAI]:
    if api_key:
        # Normal path – user gave an explicit key
        return OpenAI(api_key=api_key, base_url=base_url)

    if enable_vlm:
        # Old flag: fall back to env vars
        warnings.warn(
            "--ai-extraction is deprecated; "
            "please use --openai-api-key and --openai-model "
            "(and optionally --openai-base-url) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return OpenAI(base_url=base_url, api_key=os.getenv("OPENAI_API_KEY"))

    # AI extraction disabled
    return None

def main() -> None:
    """CLI entry point that handles both old and new functionality"""
    args = parse_arguments()
    
    # Process database-specific arguments if present
    parse_database_args(args)
    
    chunks = None
    
    # Instantiate the OpenAI client if requested
    openai_client = create_openai_client(
        api_key=getattr(args, 'openai_api_key', None),
        base_url=getattr(args, 'openai_base_url', 'https://api.openai.com/v1'),
        enable_vlm=getattr(args, 'ai_extraction', False),
    )
    
    # Check if this is a database operation
    if hasattr(args, 'db') and args.db is not None:
        # Process database query
        chunks = scrape_database(
            filepath=args.source,
            query=args.db_query if hasattr(args, 'db_query') else None,
            db_type=args.db_type if hasattr(args, 'db_type') else None,
            verbose=args.verbose,
            options=args.options
        )
    elif not args.source.startswith("http") and not os.path.isdir(args.source) and is_database_source(args.source):
        # Auto-detected database source
        chunks = scrape_database(
            filepath=args.source,
            verbose=args.verbose,
            options=args.options
        )
    elif args.source.startswith(("http://", "https://")):
        chunks = scrape_url(
            args.source,
            include_regex=getattr(args, 'include_regex', None),
            include_patterns=getattr(args, 'include_patterns', None),
            text_only=args.text_only,
            ai_extraction=args.ai_extraction,
            verbose=args.verbose,
            options=args.options,
            openai_client=openai_client,
            model=getattr(args, 'openai_model', DEFAULT_AI_MODEL),
        )
    elif os.path.isdir(args.source):
        chunks = scrape_directory(
            dir_path=args.source,
            include_regex=getattr(args, 'include_regex', None),
            include_patterns=getattr(args, 'include_patterns', None),
            verbose=args.verbose,
            ai_extraction=args.ai_extraction,
            text_only=args.text_only,
            options=args.options,
            openai_client=openai_client,
        )
    else:
        chunks = scrape_file(
            filepath=args.source,
            text_only=args.text_only,
            ai_extraction=args.ai_extraction,
            verbose=args.verbose,
            options=args.options,
            openai_client=openai_client,
            ai_model=getattr(args, 'openai_model', DEFAULT_AI_MODEL),
        )
    
    # Persist results
    save_outputs(chunks=chunks, verbose=args.verbose, text_only=args.text_only)

# Entry-point shim
if __name__ == "__main__":
    main()