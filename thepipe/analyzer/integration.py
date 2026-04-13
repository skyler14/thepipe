"""
Code Relations Integration

Integrates the analyzer with scrape_directory, outputting standard Chunks.
Supports code_relations modes: limited, map, mapnn, mapall, mapnew
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path
import logging
import os
import shutil
import subprocess
import tempfile

from ..core import Chunk
from .types import FileAnalysis, DependencyGraph, AnalysisResult
from .api import Analyzer, AnalyzerConfig, discover_files
from .digest import generate_file_digest

logger = logging.getLogger(__name__)

# Auto-register language resolvers for non-built-in languages
from . import resolvers  # noqa: F401


# Mode definitions
CODE_RELATIONS_MODES = {
    "auto",      # Intelligent default based on patterns and repo size
    "limited",   # Only selected files (include_patterns)
    "map",       # Digests for everything
    "mapnn",     # Digests + N_1/N_2 neighbor die-off
    "mapall",    # Full files for include_patterns, digests for rest
    "mapnew",    # Diff map (old vs new)
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
    mode: str = "auto",
    code_n1: int = DEFAULT_N1,
    code_n2: int = DEFAULT_N2,
    code_nf: int = DEFAULT_NF,
    code_nt: int = DEFAULT_NT,
    code_old: Optional[str] = None,
    code_new: Optional[str] = None,
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
        code_old: Old git commit-ish for mapnew (default: HEAD)
        code_new: New git commit-ish for mapnew (default: working tree)
        verbose: Print progress
        options: Additional options
        
    Returns:
        List of Chunks (full code or digests)
    """
    if mode not in CODE_RELATIONS_MODES:
        logger.warning(f"Unknown mode '{mode}', defaulting to 'auto'")
        mode = "auto"

    if mode == "mapnew":
        return _process_mapnew(
            dir_path=dir_path,
            include_patterns=include_patterns,
            code_old=code_old,
            code_new=code_new,
            verbose=verbose,
            options=options or {},
        )
    
    dir_path = str(Path(dir_path).resolve())
    
    # UNIFIED FILE DISCOVERY: Use same logic as normal scraper
    from ..file_utils import get_filtered_files
    
    # Get all files using the same logic as scrape_directory.
    # Only restrict discovery for "limited" mode; other modes need full context.
    discover_patterns = include_patterns if mode == "limited" else None
    all_discovered_files = get_filtered_files(
        dir_path=dir_path,
        include_patterns=discover_patterns,
        verbose=verbose
    )
    
    # Filter to only code files that the analyzer can process
    # Import supported code extensions from ast_extractor (supports 165+ languages)
    from .ast_extractor import EXTENSION_TO_LANGUAGE
    CODE_EXTENSIONS = set(EXTENSION_TO_LANGUAGE.keys())
    
    # Filter discovered files to only code files
    code_files = [
        f for f in all_discovered_files
        if Path(f).suffix.lower() in CODE_EXTENSIONS
    ]
    
    if not code_files:
        if verbose:
            print("No code files found for analysis")
        return []
    
    # Configure analyzer to analyze discovered code files
    config = AnalyzerConfig(
        include_patterns=["**/*"],  # Analyze all discovered files
        generate_digests=True,
        generate_semantic_tags=True,
    )
    
    # Step 1: Run analysis on ALL code files
    if verbose:
        print(f"Analyzing {dir_path}...")
    
    analyzer = Analyzer(dir_path, config)
    # Convert absolute paths to relative paths for the analyzer
    relative_code_files = [
        str(Path(f).relative_to(dir_path)) for f in code_files
    ]
    result = analyzer.analyze(target_files=relative_code_files)
    
    if verbose:
        print(f"Found {result.total_files} files, {len(result.dependency_graph.edges)} dependencies")
    
    # Step 2: Sanity check - if files don't interact, just return them as-is
    if _files_dont_interact(result, include_patterns, mode):
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
        code_nf=code_nf,
        code_nt=code_nt,
        dir_path=dir_path,
    )
    
    if verbose:
        print(f"Primary (full code): {len(primary_files)}, N_1 (digest): {len(n1_files)}, Excluded: {len(n2_excluded)}")
    
    # Step 4: Build chunks and track token counts
    chunks = []
    tokens_full = 0  # If we included everything as full code
    tokens_actual = 0  # What we're actually sending
    
    # Primary files: full code
    for filepath in sorted(primary_files):
        chunk = _file_to_chunk(dir_path, filepath, result, analyzer, as_digest=False)
        if chunk:
            chunks.append(chunk)
            chunk_tokens = _estimate_tokens(chunk.text)
            tokens_full += chunk_tokens
            tokens_actual += chunk_tokens
    
    # N_1 files: digests
    for filepath in sorted(n1_files):
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
    for filepath in sorted(n2_excluded):
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


# TODO(mapnew-regions): Extend mapnew with region-aware implementation diffs.
# Current behavior compares old/new map text, which is intentionally structural:
# imports, class/type surfaces, signatures, and lightweight call edges. This is
# excellent for stable relation diffs, but it intentionally hides body-only
# edits. The next step is to preserve that token-efficient structural diff while
# also surfacing "implementation-only changes" for downstream review UIs and
# agents.
#
# Proposed data model (verbose JSON / internal metadata):
# - file imports: exact normalized import strings (internal + external context)
# - regions: stable logical units with explicit line spans
#   * module:top          -> top-level / import region
#   * class:<name>        -> class, struct, enum, protocol, extension, etc.
#   * func:<qualified>    -> function or method region
# - each region carries:
#   * id
#   * kind
#   * name / container
#   * start_line / end_line
#   * map_hash      -> normalized structural digest fingerprint
#   * content_hash  -> normalized source fingerprint
#
# Proposed mapnew algorithm:
# 1. Use `git diff --unified=0 <old> <new>` (or HEAD vs working tree) to get
#    changed hunk ranges cheaply without materializing raw full-repo diffs.
# 2. Join changed hunks against region spans from old/new analyses.
# 3. For each touched region:
#    - if map_hash changed: include in structural map diff as today
#    - if content_hash changed but map_hash did not: append a bottom section
#      such as "implementation-only changes" so agents know where to zoom in
# 4. Keep the current unified map diff as the primary artifact.
#
# Notes:
# - External library changes are reliably tracked at the import/dependency
#   level today. Body-level external API usage is only partially visible via
#   the lightweight per-file call graph, so region metadata should document
#   that limitation until we add resolved external-call tracking.
# - This same schema should be sufficient for downstream gradual diff previews:
#   chunked hunk application can target exact regions without reparsing raw map
#   text.
def _process_mapnew(
    dir_path: str,
    include_patterns: Optional[List[str]],
    code_old: Optional[str],
    code_new: Optional[str],
    verbose: bool,
    options: Dict[str, Any],
) -> List[Chunk]:
    """
    Build a unified diff between map outputs for two versions of the repo.

    Defaults:
      - old: HEAD
      - new: working tree
    """
    repo_root = _git_root(dir_path)
    if not repo_root:
        return [_mapnew_fallback_chunk("Not a git repo", dir_path, include_patterns, code_old, code_new)]

    old_ref = code_old or "HEAD"
    new_ref = code_new or None

    worktrees: List[str] = []
    try:
        old_dir = _create_worktree(repo_root, old_ref)
        worktrees.append(old_dir)

        if new_ref:
            new_dir = _create_worktree(repo_root, new_ref)
            worktrees.append(new_dir)
            new_label = new_ref
        else:
            new_dir = dir_path
            new_label = "working-tree"

        old_chunks = process_code_relations(
            dir_path=old_dir,
            include_patterns=include_patterns,
            mode="map",
            code_n1=options.get("code_n1", DEFAULT_N1),
            code_n2=options.get("code_n2", DEFAULT_N2),
            code_nf=options.get("code_nf", DEFAULT_NF),
            code_nt=options.get("code_nt", DEFAULT_NT),
            verbose=False,
        )
        new_chunks = process_code_relations(
            dir_path=new_dir,
            include_patterns=include_patterns,
            mode="map",
            code_n1=options.get("code_n1", DEFAULT_N1),
            code_n2=options.get("code_n2", DEFAULT_N2),
            code_nf=options.get("code_nf", DEFAULT_NF),
            code_nt=options.get("code_nt", DEFAULT_NT),
            verbose=False,
        )

        diff_text = _diff_chunk_outputs(
            old_chunks=old_chunks,
            new_chunks=new_chunks,
            old_label=old_ref,
            new_label=new_label,
        )
        header = [
            "# Code Relations Diff (mapnew)",
            f"# old: {old_ref}",
            f"# new: {new_label}",
            "",
        ]
        text = "\n".join(header + [diff_text])
        return [Chunk(
            path="mapnew.diff",
            text=text,
            meta={
                "artifact": "mapnew_diff",
                "virtual": True,
                "old_ref": old_ref,
                "new_ref": new_label,
            },
        )]
    except Exception as e:
        return [_mapnew_fallback_chunk(str(e), dir_path, include_patterns, code_old, code_new)]
    finally:
        for wt in worktrees:
            _remove_worktree(repo_root, wt)


def _git_root(dir_path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", dir_path, "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _create_worktree(repo_root: str, ref: str) -> str:
    worktree_dir = tempfile.mkdtemp(prefix="thepipe_mapnew_")
    try:
        subprocess.run(
            ["git", "-C", repo_root, "worktree", "add", "--detach", worktree_dir, ref],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        shutil.rmtree(worktree_dir, ignore_errors=True)
        raise
    return worktree_dir


def _remove_worktree(repo_root: str, worktree_dir: str) -> None:
    try:
        subprocess.run(
            ["git", "-C", repo_root, "worktree", "remove", "--force", worktree_dir],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        pass
    if os.path.isdir(worktree_dir):
        shutil.rmtree(worktree_dir, ignore_errors=True)


def _write_chunks_to_file(chunks: List[Chunk], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as handle:
        wrote_any = False
        for chunk in chunks:
            if not chunk.text:
                continue
            if wrote_any:
                handle.write("\n\n")
            handle.write(chunk.text)
            if not chunk.text.endswith("\n"):
                handle.write("\n")
            wrote_any = True


def _diff_chunk_outputs(
    old_chunks: List[Chunk],
    new_chunks: List[Chunk],
    old_label: str,
    new_label: str,
) -> str:
    with tempfile.TemporaryDirectory(prefix="thepipe_mapnew_diff_") as temp_dir:
        temp_path = Path(temp_dir)
        old_file = temp_path / "old.txt"
        new_file = temp_path / "new.txt"
        _write_chunks_to_file(old_chunks, old_file)
        _write_chunks_to_file(new_chunks, new_file)

        result = subprocess.run(
            ["diff", "-u", str(old_file), str(new_file)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode not in (0, 1):
            detail = result.stderr.strip() or result.stdout.strip() or "diff failed"
            raise RuntimeError(detail)

        if not result.stdout.strip():
            return "(no changes)"

        diff_lines = result.stdout.splitlines()
        if diff_lines and diff_lines[0].startswith("--- "):
            diff_lines[0] = f"--- old:{old_label}"
        if len(diff_lines) > 1 and diff_lines[1].startswith("+++ "):
            diff_lines[1] = f"+++ new:{new_label}"
        return "\n".join(diff_lines)


def _mapnew_fallback_chunk(
    error: str,
    dir_path: str,
    include_patterns: Optional[List[str]],
    code_old: Optional[str],
    code_new: Optional[str],
) -> Chunk:
    old_ref = code_old or "HEAD"
    new_ref = code_new or "(working tree)"
    include_args = ""
    if include_patterns:
        quoted_patterns = " ".join(f'"{pattern}"' for pattern in include_patterns)
        include_args = f" --include_patterns {quoted_patterns}"
    lines = [
        "# mapnew failed",
        f"Error: {error}",
        "",
        "## Manual fallback (git worktree)",
        "Use git worktrees to compare map outputs:",
        "",
        "```bash",
        f"git worktree add --detach /tmp/thepipe-old {old_ref}",
        f"# new: {new_ref}",
        f"git worktree add --detach /tmp/thepipe-new {code_new}" if code_new else f"# use current repo at {dir_path}",
        f"/opt/anaconda3/envs/thepipe/bin/thepipe /tmp/thepipe-old{include_args} --options '{{\"code_relations\":\"map\"}}' -f > /tmp/thepipe-old-map.txt",
        f"/opt/anaconda3/envs/thepipe/bin/thepipe /tmp/thepipe-new{include_args} --options '{{\"code_relations\":\"map\"}}' -f > /tmp/thepipe-new-map.txt" if code_new else f"/opt/anaconda3/envs/thepipe/bin/thepipe {dir_path}{include_args} --options '{{\"code_relations\":\"map\"}}' -f > /tmp/thepipe-new-map.txt",
        "diff -u /tmp/thepipe-old-map.txt /tmp/thepipe-new-map.txt",
        "```",
    ]
    return Chunk(
        path="mapnew-fallback.md",
        text="\n".join(lines),
        meta={
            "artifact": "mapnew_fallback",
            "virtual": True,
            "error": error,
            "old_ref": old_ref,
            "new_ref": new_ref,
        },
    )


def _files_dont_interact(
    result: AnalysisResult,
    include_patterns: Optional[List[str]],
    mode: str,
) -> bool:
    """
    Sanity check: do the selected files have any internal dependencies?
    
    If include_patterns files don't import each other or share dependencies,
    skip the relationship mapping entirely.
    """
    if mode in {"map", "mapnn", "mapall"}:
        return False
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
    code_nf: int,
    code_nt: int,
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
        # Map mode: ALL matched files become digests, no full code output
        # The include_patterns filter defines the universe of files to map
        if include_patterns:
            # Matched files become digests, everything else is excluded
            return set(), primary_files, all_files - primary_files
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

    analysis = result.files.get(filepath)
    return Chunk(
        path=filepath,
        text=text,
        meta=_analysis_to_meta(analysis) if analysis else None,
    )


def _analysis_to_meta(analysis: FileAnalysis) -> Dict[str, Any]:
    """Build verbose JSON metadata with line info for functions/classes/calls."""
    imports = []
    for imp in analysis.imports:
        if not imp:
            continue
        cleaned = " ".join(imp.strip().split())
        if cleaned:
            imports.append(cleaned)
    functions = [
        {"name": f.name, "start_line": f.start_line, "end_line": f.end_line}
        for f in analysis.functions
        if f.name
    ]
    classes = [
        {"name": c.name, "start_line": c.start_line, "end_line": c.end_line}
        for c in analysis.classes
        if c.name
    ]
    call_graph = [
        {"caller": e.caller, "callee": e.callee, "line": e.line}
        for e in analysis.call_graph
    ]
    imports = sorted(set(imports))
    functions.sort(key=lambda f: (f["name"], f["start_line"], f["end_line"]))
    classes.sort(key=lambda c: (c["name"], c["start_line"], c["end_line"]))
    call_graph.sort(key=lambda e: (e["caller"], e["callee"], e["line"]))
    return {
        "language": analysis.language,
        "line_count": analysis.line_count,
        "imports": imports,
        "imports_count": len(analysis.imports),
        "functions": functions,
        "classes": classes,
        "call_graph": call_graph,
    }


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

    This is the conservative programmatic default. Agent instructions may
    recommend `map` as a first pass when full repo structure is desired.

    Use in scrape_directory options:
        options = {"code_relations": "auto", "code_n1": 3, "code_n2": 5}
    """
    return {
        "code_relations": "auto",  # None, "limited", "map", "mapnn", "mapall"
        "code_n1": DEFAULT_N1,
        "code_n2": DEFAULT_N2,
        "code_old": None,
        "code_new": None,
    }
