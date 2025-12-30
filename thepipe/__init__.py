from __future__ import annotations

import argparse
import json
import os
import warnings
from typing import Optional

from openai import OpenAI

from .scraper import scrape_directory, scrape_file, scrape_url, scrape_database
from .core import save_outputs, DEFAULT_AI_MODEL
from .file_utils import is_database_source
from .database_utils import parse_database_args
from .scraper import scrape_directory, scrape_file, scrape_url


# Argument parsing
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
    parser.add_argument('--text_only', nargs='?', const='default', default=None, 
                        choices=['default', 'transcribe', 'ai', 'uploaded'],
                        help='Extract only text from the source. Video Options: default (try all methods), transcribe (force local transcription), ai (prefer AI-generated), uploaded (prefer uploaded)')
    parser.add_argument('--verbose', action='store_true', help='Print status messages.')
    parser.add_argument('--options', type=str, help='JSON string of type-specific options')
    parser.add_argument('--browser_type', type=str, choices=['chrome', 'firefox', 'edge', 'brave', 'safari'],
                       help='Specific browser to extract cookies from')
    parser.add_argument('--show_cookies', nargs='?', const='format', choices=['format', 'credentials'],
                       help='Display cookies instead of processing content. Use "credentials" for full cookie data.')
    parser.add_argument('--db', nargs='*',
        help='Database query. Format: --db ["query"] [db_type] [mode]. '
             'If empty, shows preview. Mode can be "schema" or "preview".')
    parser.add_argument('--output-format', '-f', dest='output_format',
        choices=['text', 'json', 'llm'], default='text',
        help='Output format: text (default, raw to stdout), json (structured), llm (message format for APIs)')

    # OpenAI-related flags
    parser.add_argument(
        "--openai-api-key",
        dest="openai_api_key",
        default=os.getenv("OPENAI_API_KEY"),
        help="OpenAI API key.  If omitted, env variable OPENAI_API_KEY is used.",
    )
    parser.add_argument(
        "--openai-base-url",
        default=os.getenv("OPENAI_API_BASE_URL") or "https://api.openai.com/v1",
        dest="openai_base_url",
        help="Base URL for the OpenAI API (default: https://api.openai.com/v1).",
    )

    parser.add_argument(
        "--inclusion_pattern",
        type=str,
        default=None,
        help="Regex pattern to match in a directory.",
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
            text_only=args.text_only,
            options=args.options,
            openai_client=openai_client,
        )
    else:
        chunks = scrape_file(
            text_only=args.text_only,
            filepath=args.source,
            verbose=args.verbose,
            options=args.options,
            openai_client=openai_client,
            model=args.openai_model,
        )
    
    # Output results based on format
    output_format = getattr(args, 'output_format', 'text')
    
    if output_format == 'text':
        # Raw text to stdout (default)
        for chunk in chunks:
            if chunk.text:
                print(chunk.text)
    elif output_format == 'json':
        # JSON array to stdout
        import json as json_module
        output = [c.to_json(text_only=args.text_only) for c in chunks]
        print(json_module.dumps(output, indent=2))
    elif output_format == 'llm':
        # LLM message format to stdout
        import json as json_module
        from .core import chunks_to_messages
        messages = chunks_to_messages(chunks, text_only=args.text_only)
        print(json_module.dumps(messages, indent=2))
    else:
        # Fallback to file output
        save_outputs(chunks=chunks, verbose=args.verbose, text_only=args.text_only)

# Entry-point shim
if __name__ == "__main__":
    main()
