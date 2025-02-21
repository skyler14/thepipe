from pyrclone import RCloneWrapper
from .buckets_config import BUCKET_CONFIGS, PREFIX_TO_SERVICE

def get_bucket_config(bucket_flag: str, url: str):
    """Resolve configuration using --bucket flag or URL prefix"""
    if bucket_flag:
        return BUCKET_CONFIGS[bucket_flag.lower()]
    
    for prefix, service in PREFIX_TO_SERVICE.items():
        if url.startswith(prefix):
            return BUCKET_CONFIGS[service]
    
    raise ValueError(f"Could not determine bucket type from URL: {url}")

def create_client(config: dict):
    """Create pre-configured RClone client"""
    return RCloneWrapper({'temp_bucket': config})