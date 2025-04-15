import os
from .scraper import scrape_file, scrape_url, scrape_directory, scrape_database  
from .core import parse_arguments, save_outputs
from .file_utils import is_database_source
from .database_utils import parse_database_args  

def main() -> None:
    args = parse_arguments()
    
    # Process database-specific arguments if present
    parse_database_args(args)
    
    chunks = None
    
    # Check if this is a database operation
    if hasattr(args, 'db') and args.db is not None:
        # Process database query
        chunks = scrape_database(filepath=args.source,query=args.db_query if hasattr(args, 'db_query') else None,db_type=args.db_type if hasattr(args, 'db_type') else None,verbose=args.verbose,local=args.local,options=args.options)
    elif not args.source.startswith("http") and not os.path.isdir(args.source) and is_database_source(args.source):
        # Auto-detected database source
        chunks = scrape_database(filepath=args.source,verbose=args.verbose,local=args.local,options=args.options)
    elif args.source.startswith("http") or args.source.startswith("www."):
        chunks = scrape_url(args.source, text_only=args.text_only, include_regex=args.include_regex, include_patterns=args.include_patterns, ai_extraction=args.ai_extraction,verbose=args.verbose, local=args.local, options=args.options)
    elif os.path.isdir(args.source):
        chunks = scrape_directory(args.source, include_regex=args.include_regex, include_patterns=args.include_patterns, verbose=args.verbose, ai_extraction=args.ai_extraction, text_only=args.text_only, local=args.local, options=args.options)
    else:
        chunks = scrape_file(args.source, text_only=args.text_only, ai_extraction=args.ai_extraction, verbose=args.verbose, local=args.local, options=args.options)
    save_outputs(chunks=chunks, verbose=args.verbose, text_only=args.text_only)
    
if __name__ == "__main__":
    main()