"""
Code Relations Integration

Integrates the analyzer with scrape_directory, outputting standard Chunks.
Supports code_relations modes: limited, map, mapnn, mapall
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path
import logging

from ..core import Chunk
from .types import FileAnalysis, DependencyGraph, AnalysisResult
from .api import Analyzer, AnalyzerConfig, discover_files
from .digest import generate_file_digest

logger = logging.getLogger(__name__)


# Mode definitions
CODE_RELATIONS_MODES = {
    "auto",      # Intelligent default based on patterns and repo size
    "limited",   # Only selected files (include_patterns)
    "map",       # Digests for everything
    "mapnn",     # Digests + N_1/N_2 neighbor die-off
    "mapall",    # Full files for include_patterns, digests for rest
}

# Default thresholds for auto mode
DEFAULT_NF = 100  # File count threshold (code_nf)
DEFAULT_NT = 150000  # Token count threshold (code_nt)

# Default neighbor distances
DEFAULT_N1 = 3  # Include as digest (code_n1)
DEFAULT_N2 = 5  # Don't include beyond this (code_n2)


def process_code_relations(
    dir_path: str,
    include_patterns: Optional[List[str]] = None,
    mode: str = "mapnn",
    code_n1: int = DEFAULT_N1,
    code_n2: int = DEFAULT_N2,
    code_nf: int = DEFAULT_NF,
    code_nt: int = DEFAULT_NT,
    verbose: bool = False,
    options: Optional[Dict[str, Any]] = None,
) -> List[Chunk]:
    """
    Process a directory with code relationship analysis.
    
    Args:
        dir_path: Path to directory
        include_patterns: Glob patterns for primary files (returned as full code)
        mode: Processing mode
            - "auto": Intelligently pick based on patterns and repo size
            - "limited": Only include_patterns files
            - "map": Digests for everything
            - "mapnn": Digests + N_1/N_2 neighbor die-off
            - "mapall": Full for patterns, digests for rest
        code_n1: N_1 distance - include as digests (default: 3)
        code_n2: N_2 distance - cutoff, don't include (default: 5)
        code_nf: File count threshold for auto mode (default: 100)
        code_nt: Token count threshold for auto mode (default: 150000)
        verbose: Print progress
        options: Additional options
        
    Returns:
        List of Chunks (full code or digests)
    """
    if mode not in CODE_RELATIONS_MODES:
        logger.warning(f"Unknown mode '{mode}', defaulting to 'auto'")
        mode = "auto"
    
    dir_path = str(Path(dir_path).resolve())
    
    # Configure analyzer to discover ALL code files
    # include_patterns is used later to determine which are "primary"
    config = AnalyzerConfig(
        include_patterns=[
            "**/*.py", "**/*.js", "**/*.ts", "**/*.tsx", "**/*.jsx",
            "**/*.go", "**/*.rs", "**/*.c", "**/*.cpp", "**/*.h",
        ],  # Analyze all code files
        generate_digests=True,
        generate_semantic_tags=True,
    )
    
    # Step 1: Run analysis on ALL code files
    if verbose:
        print(f"Analyzing {dir_path}...")
    
    analyzer = Analyzer(dir_path, config)
    result = analyzer.analyze()
    
    if verbose:
        print(f"Found {result.total_files} files, {len(result.dependency_graph.edges)} dependencies")
    
    # Step 2: Sanity check - if files don't interact, just return them as-is
    if _files_dont_interact(result, include_patterns):
        if verbose:
            print("Files don't interact - returning as standalone scripts")
        return _return_standalone_files(dir_path, result, include_patterns)
    
    # Step 3: Determine which files to include and at what level
    primary_files, n1_files, n2_excluded = _categorize_files(
        result=result,
        include_patterns=include_patterns,
        mode=mode,
        code_n1=code_n1,
        code_n2=code_n2,
        dir_path=dir_path,
    )
    
    if verbose:
        print(f"Primary (full code): {len(primary_files)}, N_1 (digest): {len(n1_files)}, Excluded: {len(n2_excluded)}")
    
    # Step 4: Build chunks and track token counts
    chunks = []
    tokens_full = 0  # If we included everything as full code
    tokens_actual = 0  # What we're actually sending
    
    # Primary files: full code
    for filepath in primary_files:
        chunk = _file_to_chunk(dir_path, filepath, result, analyzer, as_digest=False)
        if chunk:
            chunks.append(chunk)
            chunk_tokens = _estimate_tokens(chunk.text)
            tokens_full += chunk_tokens
            tokens_actual += chunk_tokens
    
    # N_1 files: digests
    for filepath in n1_files:
        # Track what full code would have cost
        full_path = Path(dir_path) / filepath
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                full_content = f.read()
            tokens_full += _estimate_tokens(full_content)
        except Exception:
            pass
        
        chunk = _file_to_chunk(dir_path, filepath, result, analyzer, as_digest=True)
        if chunk:
            chunks.append(chunk)
            tokens_actual += _estimate_tokens(chunk.text)
    
    # Add excluded files to the "full" count for comparison
    for filepath in n2_excluded:
        full_path = Path(dir_path) / filepath
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                tokens_full += _estimate_tokens(f.read())
        except Exception:
            pass
    
    # Add a summary chunk with semantic tags
    if result.semantic_tags:
        summary = _build_summary_chunk(result, primary_files, n1_files, tokens_full, tokens_actual)
        chunks.insert(0, summary)
    
    # Verbose token savings message
    if verbose:
        savings = tokens_full - tokens_actual
        pct = (savings / tokens_full * 100) if tokens_full > 0 else 0
        print(f"\n📊 Token Analysis:")
        print(f"   Full codebase: ~{tokens_full:,} tokens")
        print(f"   With digests:  ~{tokens_actual:,} tokens")
        print(f"   💰 Saved: ~{savings:,} tokens ({pct:.1f}% reduction)")
    
    return chunks


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for code"""
    if not text:
        return 0
    return len(text) // 4


def _files_dont_interact(
    result: AnalysisResult,
    include_patterns: Optional[List[str]],
) -> bool:
    """
    Sanity check: do the selected files have any internal dependencies?
    
    If include_patterns files don't import each other or share dependencies,
    skip the relationship mapping entirely.
    """
    if not include_patterns:
        return False
    
    internal_edges = [e for e in result.dependency_graph.edges if not e.is_external]
    
    # If there are very few internal dependencies relative to files, they don't interact
    if len(internal_edges) < 2 and result.total_files > 1:
        return True
    
    return False


def _return_standalone_files(
    dir_path: str,
    result: AnalysisResult,
    include_patterns: Optional[List[str]],
) -> List[Chunk]:
    """Return files as standalone chunks without relationship processing"""
    chunks = []
    
    for filepath, analysis in result.files.items():
        full_path = Path(dir_path) / filepath
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            chunks.append(Chunk(path=filepath, text=content))
        except Exception:
            pass
    
    return chunks


def _categorize_files(
    result: AnalysisResult,
    include_patterns: Optional[List[str]],
    mode: str,
    code_n1: int,
    code_n2: int,
    dir_path: str,
) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Categorize files into primary (full code), N_1 (digest), and excluded.
    
    Returns:
        (primary_files, n1_files, excluded_files)
    """
    import fnmatch
    
    all_files = set(result.files.keys())
    primary_files: Set[str] = set()
    n1_files: Set[str] = set()
    excluded_files: Set[str] = set()
    
    # Identify primary files (match include_patterns)
    if include_patterns:
        for filepath in all_files:
            for pattern in include_patterns:
                if fnmatch.fnmatch(filepath, pattern):
                    primary_files.add(filepath)
                    break
    else:
        # No patterns means all files are primary (for "map" mode)
        primary_files = all_files.copy()
    
    # AUTO mode: intelligently pick strategy
    if mode == "auto":
        # Estimate total tokens
        total_tokens = sum(
            len(analysis.functions) * 50 + len(analysis.classes) * 100 + analysis.line_count
            for analysis in result.files.values()
        )
        
        if not include_patterns or len(include_patterns) == 0:
            # No filtering patterns → use "map" (all as digests)
            mode = "map"
        elif result.total_files > code_nf or total_tokens > code_nt:
            # Large repo with patterns → use "mapnn" to limit scope
            mode = "mapnn"
        else:
            # Small/medium repo with patterns → use "mapall"
            mode = "mapall"
    
    if mode == "limited":
        # Only return primary files
        excluded_files = all_files - primary_files
        return primary_files, set(), excluded_files
    
    if mode == "map":
        # Everything as digest, no primary files (unless patterns specified)
        if include_patterns:
            n1_files = all_files - primary_files
            return primary_files, n1_files, set()
        else:
            # No patterns = all files as digests
            return set(), all_files, set()
    
    if mode == "mapall":
        # Primary as full code, rest as digest, no N_2 cutoff
        n1_files = all_files - primary_files
        return primary_files, n1_files, set()
    
    # mode == "mapnn": Apply N_1/N_2 die-off
    graph = result.dependency_graph
    
    # Get neighbors at different depths
    n1_neighbors: Set[str] = set()
    for pf in primary_files:
        neighbors = graph.nearest_neighbors(pf, depth=code_n1)
        n1_neighbors.update(neighbors)
    
    n2_neighbors: Set[str] = set()
    for pf in primary_files:
        neighbors = graph.nearest_neighbors(pf, depth=code_n2)
        n2_neighbors.update(neighbors)
    
    # N_1 files: within N_1 distance but not primary
    n1_files = (n1_neighbors - primary_files) & all_files
    
    # Excluded: beyond N_2 distance
    excluded_files = all_files - primary_files - n1_files
    
    return primary_files, n1_files, excluded_files


def _file_to_chunk(
    dir_path: str,
    filepath: str,
    result: AnalysisResult,
    analyzer: Analyzer,
    as_digest: bool,
) -> Optional[Chunk]:
    """Convert a file to a Chunk (full code or digest)"""
    full_path = Path(dir_path) / filepath
    
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.debug(f"Failed to read {filepath}: {e}")
        return None
    
    if as_digest:
        # Use digest
        digest = analyzer.get_digest(filepath)
        if digest:
            text = f"# {filepath} (digest)\n{digest}"
        else:
            # Fallback to generating digest
            analysis = result.files.get(filepath)
            if analysis:
                digest = generate_file_digest(content, analysis)
                text = f"# {filepath} (digest)\n{digest}"
            else:
                text = f"# {filepath}\n[Could not generate digest]"
    else:
        # Full code
        text = f"# {filepath}\n{content}"
    
    return Chunk(path=filepath, text=text)


def _build_summary_chunk(
    result: AnalysisResult,
    primary_files: Set[str],
    n1_files: Set[str],
    tokens_full: int = 0,
    tokens_actual: int = 0,
) -> Chunk:
    """Build a summary chunk with semantic tags and file overview"""
    savings = tokens_full - tokens_actual
    pct = (savings / tokens_full * 100) if tokens_full > 0 else 0
    
    lines = [
        "# Code Relationship Summary",
        "",
        f"**Primary files (full code):** {len(primary_files)}",
        f"**Related files (digests):** {len(n1_files)}",
        f"**Total functions:** {result.total_functions}",
        f"**Total classes:** {result.total_classes}",
    ]
    
    if tokens_full > 0:
        lines.extend([
            "",
            f"**Token savings:** ~{savings:,} tokens saved ({pct:.0f}% reduction)",
        ])
    
    if result.semantic_tags:
        lines.append("")
        lines.append("## Semantic Tags")
        for tag, files in sorted(result.semantic_tags.items()):
            lines.append(f"- #{tag}: {len(files)} files")
    
    return Chunk(path="__summary__", text="\n".join(lines))


def get_code_relations_options() -> Dict[str, Any]:
    """
    Get default options for code_relations.
    
    Use in scrape_directory options:
        options = {"code_relations": "mapnn", "code_n1": 3, "code_n2": 5}
    """
    return {
        "code_relations": None,  # None, "limited", "map", "mapnn", "mapall"
        "code_n1": DEFAULT_N1,
        "code_n2": DEFAULT_N2,
    }
