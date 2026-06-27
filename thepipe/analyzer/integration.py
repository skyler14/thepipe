"""
Code Relations Integration

Integrates the analyzer with scrape_directory, outputting standard Chunks.
Supports code_relations modes: limited, map, mapnn, mapall, mapnew
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile

from ..core import Chunk
from .types import FileAnalysis, DependencyGraph, AnalysisResult
from .api import Analyzer, AnalyzerConfig, discover_files
from .digest import DigestGenerator, generate_file_digest

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


# TODO(mapnew-regions): Remaining work after the first region-aware pass:
# - make module regions discontiguous instead of using the whole-file fallback
# - add resolved external API usage tracking beyond imports/lightweight calls
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
        changed_files = _git_diff_files(
            repo_root=repo_root,
            old_ref=old_ref,
            new_ref=new_ref,
        )
        if new_ref is None:
            _merge_untracked_files(
                repo_root=repo_root,
                changed_files=changed_files,
                new_chunks=new_chunks,
            )
        diff_files = _build_mapnew_file_payloads(
            old_chunks=old_chunks,
            new_chunks=new_chunks,
            changed_files=changed_files,
        )
        implementation_notes = _implementation_only_change_notes(
            diff_files=diff_files,
        )
        header = [
            "# Code Relations Diff (mapnew)",
            f"# old: {old_ref}",
            f"# new: {new_label}",
            "",
        ]
        body_parts = [diff_text]
        if implementation_notes:
            body_parts.extend([
                "",
                "## Implementation-Only Changes",
                *[f"- {note}" for note in implementation_notes],
            ])
        text = "\n".join(header + body_parts)
        return [Chunk(
            path="mapnew.diff",
            text=text,
            meta={
                "artifact": "mapnew_diff",
                "virtual": True,
                "old_ref": old_ref,
                "new_ref": new_label,
                "changed_files_count": len(diff_files),
                "implementation_only_regions": len(implementation_notes),
                "files": diff_files,
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


def _git_diff_files(
    repo_root: str,
    old_ref: str,
    new_ref: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    cmd = [
        "git", "-C", repo_root, "diff", "--unified=0", "--find-renames", "--no-ext-diff",
        old_ref,
    ]
    if new_ref:
        cmd.append(new_ref)
    cmd.append("--")
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or result.stdout.strip() or "git diff failed"
        raise RuntimeError(detail)

    files: Dict[str, Dict[str, Any]] = {}
    current: Optional[Dict[str, Any]] = None

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        current["path"] = current.get("new_path") or current.get("old_path")
        if current["path"]:
            files[current["path"]] = current
        current = None

    for line in result.stdout.splitlines():
        if line.startswith("diff --git "):
            flush_current()
            current = {
                "path": None,
                "old_path": None,
                "new_path": None,
                "status": "modified",
                "hunks": [],
            }
            continue

        if current is None:
            continue

        if line.startswith("new file mode "):
            current["status"] = "added"
            current["old_path"] = None
            continue
        if line.startswith("deleted file mode "):
            current["status"] = "deleted"
            current["new_path"] = None
            continue
        if line.startswith("rename from "):
            current["status"] = "renamed"
            current["old_path"] = line[len("rename from "):].strip() or current.get("old_path")
            continue
        if line.startswith("rename to "):
            current["new_path"] = line[len("rename to "):].strip() or current.get("new_path")
            continue
        if line.startswith("--- "):
            current["old_path"] = _parse_patch_header_path(line, marker="--- ", prefix="a/")
            continue
        if line.startswith("+++ "):
            current["new_path"] = _parse_patch_header_path(line, marker="+++ ", prefix="b/")
            continue
        if not line.startswith("@@"):
            continue

        header = line.split("@@")[1].strip()
        old_part, new_part = header.split()
        old_start, old_count = _parse_unified_range(old_part)
        new_start, new_count = _parse_unified_range(new_part)
        current["hunks"].append({
            "old_start": old_start,
            "old_end": None if old_count == 0 else old_start + old_count - 1,
            "new_start": new_start,
            "new_end": None if new_count == 0 else new_start + new_count - 1,
        })
    flush_current()
    return files


def _parse_patch_header_path(line: str, marker: str, prefix: str) -> Optional[str]:
    path = line[len(marker):].split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith(prefix):
        path = path[len(prefix):]
    return path


def _merge_untracked_files(
    repo_root: str,
    changed_files: Dict[str, Dict[str, Any]],
    new_chunks: List[Chunk],
) -> None:
    new_map = _chunk_map(new_chunks)
    for path in _git_untracked_files(repo_root):
        if path in changed_files:
            continue
        chunk = new_map.get(path)
        if chunk is None:
            continue
        line_count = _source_line_count(Path(repo_root) / path)
        if line_count == 0:
            line_count = _chunk_line_count(chunk)
        changed_files[path] = {
            "path": path,
            "old_path": None,
            "new_path": path,
            "status": "added",
            "untracked": True,
            "hunks": [{
                "old_start": 0,
                "old_end": None,
                "new_start": 1 if line_count else 0,
                "new_end": line_count if line_count else None,
            }],
        }


def _git_untracked_files(repo_root: str) -> List[str]:
    result = subprocess.run(
        ["git", "-C", repo_root, "ls-files", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _chunk_line_count(chunk: Chunk) -> int:
    if isinstance(chunk.meta, dict) and isinstance(chunk.meta.get("line_count"), int):
        return chunk.meta["line_count"]
    if not chunk.text:
        return 0
    return len(chunk.text.splitlines())


def _source_line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except Exception:
        return 0


def _parse_unified_range(token: str) -> Tuple[int, int]:
    token = token[1:]  # drop leading +/- marker
    if "," in token:
        start_str, count_str = token.split(",", 1)
        return int(start_str), int(count_str)
    return int(token), 1


def _chunk_map(chunks: List[Chunk]) -> Dict[str, Chunk]:
    return {
        chunk.path: chunk
        for chunk in chunks
        if chunk.path and chunk.path != "__summary__"
    }


def _build_mapnew_file_payloads(
    old_chunks: List[Chunk],
    new_chunks: List[Chunk],
    changed_files: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    old_map = _chunk_map(old_chunks)
    new_map = _chunk_map(new_chunks)
    diff_files: List[Dict[str, Any]] = []

    for filepath in sorted(changed_files):
        file_diff = changed_files[filepath]
        old_path = file_diff.get("old_path")
        new_path = file_diff.get("new_path")
        old_chunk = old_map.get(old_path or "")
        new_chunk = new_map.get(new_path or "")
        if old_chunk is None and new_chunk is None:
            continue

        region_changes = _collect_region_changes(
            filepath=filepath,
            hunks=file_diff.get("hunks", []),
            old_chunk=old_chunk,
            new_chunk=new_chunk,
        )
        implementation_only_regions = [
            region for region in region_changes
            if region["change"] == "implementation_only"
        ]

        diff_files.append({
            "path": filepath,
            "old_path": old_path,
            "new_path": new_path,
            "status": file_diff.get("status", "modified"),
            "untracked": bool(file_diff.get("untracked", False)),
            "hunks": file_diff.get("hunks", []),
            "map_changed": _chunk_map_hash(old_chunk) != _chunk_map_hash(new_chunk),
            "old": _chunk_snapshot(old_chunk),
            "new": _chunk_snapshot(new_chunk),
            "region_changes": region_changes,
            "implementation_only_regions": implementation_only_regions,
        })

    return diff_files


def _implementation_only_change_notes(diff_files: List[Dict[str, Any]]) -> List[str]:
    notes: List[str] = []
    for file_diff in diff_files:
        for region in file_diff.get("implementation_only_regions", []):
            note = region.get("note")
            if isinstance(note, str) and note:
                notes.append(f"{file_diff['path']}: {note}")
    return notes


def _chunk_map_hash(chunk: Optional[Chunk]) -> Optional[str]:
    if chunk is None:
        return None
    if isinstance(chunk.meta, dict):
        region_rows = []
        for region in chunk.meta.get("regions", []):
            if not isinstance(region, dict):
                continue
            region_kind = region.get("kind")
            region_rows.append({
                "id": region.get("id"),
                "kind": region_kind,
                "container": region.get("container"),
                "map_hash": None if region_kind == "module" else region.get("map_hash"),
            })
        region_rows.sort(key=lambda row: (row["id"] or "", row["kind"] or ""))

        call_rows = []
        for entry in chunk.meta.get("call_graph", []):
            if not isinstance(entry, dict):
                continue
            call_rows.append({
                "caller": entry.get("caller"),
                "callee": entry.get("callee"),
            })
        call_rows.sort(key=lambda row: (row["caller"] or "", row["callee"] or ""))

        shape = {
            "imports": chunk.meta.get("imports", []),
            "regions": region_rows,
            "calls": call_rows,
        }
        if region_rows or call_rows or shape["imports"]:
            return _stable_hash(str(shape))
    if not chunk.text:
        return None
    return _stable_hash(chunk.text)


def _chunk_snapshot(chunk: Optional[Chunk]) -> Optional[Dict[str, Any]]:
    if chunk is None:
        return None
    return {
        "path": chunk.path,
        "digest": chunk.text,
        "map_hash": _chunk_map_hash(chunk),
        "meta": dict(chunk.meta) if isinstance(chunk.meta, dict) else None,
    }


def _collect_region_changes(
    filepath: str,
    hunks: List[Dict[str, Optional[int]]],
    old_chunk: Optional[Chunk],
    new_chunk: Optional[Chunk],
) -> List[Dict[str, Any]]:
    old_regions = _region_map(old_chunk)
    new_regions = _region_map(new_chunk)
    touched_ids = _touched_region_ids(hunks, old_regions, new_regions)
    changes: List[Dict[str, Any]] = []
    for region_id in touched_ids:
        old_region = old_regions.get(region_id)
        new_region = new_regions.get(region_id)
        change = _region_change_kind(old_region, new_region)
        if change is None:
            continue
        changes.append(_region_change_payload(
            filepath=filepath,
            region_id=region_id,
            change=change,
            hunks=hunks,
            old_region=old_region,
            new_region=new_region,
        ))
    return changes


def _region_change_kind(
    old_region: Optional[Dict[str, Any]],
    new_region: Optional[Dict[str, Any]],
) -> Optional[str]:
    if old_region and new_region:
        if old_region["map_hash"] != new_region["map_hash"]:
            return "structural"
        if old_region["content_hash"] != new_region["content_hash"]:
            return "implementation_only"
        return None
    if new_region:
        return "added"
    if old_region:
        return "removed"
    return None


def _region_change_payload(
    filepath: str,
    region_id: str,
    change: str,
    hunks: List[Dict[str, Optional[int]]],
    old_region: Optional[Dict[str, Any]],
    new_region: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    region = new_region or old_region or {}
    changed_ranges = {
        "old": _intersect_region_ranges(hunks, old_region, "old"),
        "new": _intersect_region_ranges(hunks, new_region, "new"),
    }
    range_summary = _format_region_change_ranges(changed_ranges)
    label = _region_label(region)
    note = _region_change_note(change, label, range_summary)
    return {
        "id": region_id,
        "path": filepath,
        "change": change,
        "kind": region.get("kind", "region"),
        "name": region.get("name"),
        "qualified_name": region.get("qualified_name"),
        "container": region.get("container"),
        "old_start_line": old_region.get("start_line") if old_region else None,
        "old_end_line": old_region.get("end_line") if old_region else None,
        "new_start_line": new_region.get("start_line") if new_region else None,
        "new_end_line": new_region.get("end_line") if new_region else None,
        "old_map_hash": old_region.get("map_hash") if old_region else None,
        "new_map_hash": new_region.get("map_hash") if new_region else None,
        "old_content_hash": old_region.get("content_hash") if old_region else None,
        "new_content_hash": new_region.get("content_hash") if new_region else None,
        "changed_ranges": changed_ranges,
        "range_summary": range_summary,
        "label": label,
        "note": note,
    }


def _region_map(chunk: Optional[Chunk]) -> Dict[str, Dict[str, Any]]:
    if chunk is None or not isinstance(chunk.meta, dict):
        return {}
    regions = chunk.meta.get("regions")
    if not isinstance(regions, list):
        return {}
    return {
        region["id"]: region
        for region in regions
        if isinstance(region, dict) and isinstance(region.get("id"), str)
    }


def _touched_region_ids(
    hunks: List[Dict[str, Optional[int]]],
    old_regions: Dict[str, Dict[str, Any]],
    new_regions: Dict[str, Dict[str, Any]],
) -> List[str]:
    touched: List[str] = []
    seen: Set[str] = set()
    for hunk in hunks:
        old_matches = _matching_regions(old_regions, hunk["old_start"], hunk["old_end"])
        new_matches = _matching_regions(new_regions, hunk["new_start"], hunk["new_end"])
        non_module_matches = [
            region for region in (old_matches + new_matches)
            if region.get("kind") != "module"
        ]

        matches = list(non_module_matches)
        include_module = False
        if non_module_matches:
            include_module = (
                _has_uncovered_hunk_lines(hunk["old_start"], hunk["old_end"], old_matches)
                or _has_uncovered_hunk_lines(hunk["new_start"], hunk["new_end"], new_matches)
            )
        elif old_regions.get("module:top") or new_regions.get("module:top"):
            include_module = True

        if include_module:
            module_region = old_regions.get("module:top") or new_regions.get("module:top")
            if module_region:
                matches.append(module_region)

        for region in matches:
            region_id = region["id"]
            if region_id in seen:
                continue
            seen.add(region_id)
            touched.append(region_id)
    return touched


def _has_uncovered_hunk_lines(
    start_line: Optional[int],
    end_line: Optional[int],
    matches: List[Dict[str, Any]],
) -> bool:
    if start_line is None or end_line is None:
        return False

    non_module_matches = [region for region in matches if region.get("kind") != "module"]
    if not non_module_matches:
        return True

    covered: List[Tuple[int, int]] = []
    for region in non_module_matches:
        region_start = region.get("start_line")
        region_end = region.get("end_line")
        if not isinstance(region_start, int) or not isinstance(region_end, int):
            continue
        overlap_start = max(start_line, region_start)
        overlap_end = min(end_line, region_end)
        if overlap_start <= overlap_end:
            covered.append((overlap_start, overlap_end))

    if not covered:
        return True

    covered.sort()
    cursor = start_line
    for covered_start, covered_end in covered:
        if cursor < covered_start:
            return True
        cursor = max(cursor, covered_end + 1)
        if cursor > end_line:
            return False
    return cursor <= end_line


def _matching_regions(
    regions: Dict[str, Dict[str, Any]],
    start_line: Optional[int],
    end_line: Optional[int],
) -> List[Dict[str, Any]]:
    if start_line is None or end_line is None:
        return []
    matches = []
    for region in regions.values():
        region_start = region.get("start_line")
        region_end = region.get("end_line")
        if not isinstance(region_start, int) or not isinstance(region_end, int):
            continue
        if region_start <= end_line and start_line <= region_end:
            matches.append(region)
    matches.sort(key=lambda region: (region["end_line"] - region["start_line"], region["start_line"]))
    return matches


def _region_label(region: Dict[str, Any]) -> str:
    kind = region.get("kind", "region")
    name = region.get("qualified_name") or region.get("name") or region.get("id")
    if kind == "module":
        return "module top-level"
    return f"{kind} `{name}`"


def _format_region_change_ranges(
    changed_ranges: Dict[str, List[Dict[str, int]]],
) -> str:
    parts = []
    old_ranges = changed_ranges.get("old", [])
    new_ranges = changed_ranges.get("new", [])
    if old_ranges:
        parts.append(f"old lines {', '.join(_render_line_ranges(old_ranges))}")
    if new_ranges:
        parts.append(f"new lines {', '.join(_render_line_ranges(new_ranges))}")
    return " / ".join(parts) if parts else "near touched lines"


def _intersect_region_ranges(
    hunks: List[Dict[str, Optional[int]]],
    region: Optional[Dict[str, Any]],
    side: str,
) -> List[Dict[str, int]]:
    if region is None:
        return []
    region_start = region.get("start_line")
    region_end = region.get("end_line")
    if not isinstance(region_start, int) or not isinstance(region_end, int):
        return []

    rendered: List[Dict[str, int]] = []
    for hunk in hunks:
        start = hunk.get(f"{side}_start")
        end = hunk.get(f"{side}_end")
        if start is None or end is None:
            continue
        overlap_start = max(region_start, start)
        overlap_end = min(region_end, end)
        if overlap_start > overlap_end:
            continue
        rendered.append({"start": overlap_start, "end": overlap_end})
    return rendered


def _render_line_ranges(ranges: List[Dict[str, int]]) -> List[str]:
    rendered = []
    for item in ranges:
        start = item["start"]
        end = item["end"]
        rendered.append(str(start) if start == end else f"{start}-{end}")
    return rendered


def _region_change_note(change: str, label: str, range_summary: str) -> str:
    if change == "implementation_only":
        return f"{label} changed internally at {range_summary}; map unchanged"
    if change == "structural":
        return f"{label} changed structurally at {range_summary}"
    if change == "added":
        return f"{label} added"
    if change == "removed":
        return f"{label} removed"
    return f"{label} changed"


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
        meta=_analysis_to_meta(
            analysis,
            content,
            _dependencies_for_file(result, filepath),
        ) if analysis else None,
    )


def _analysis_to_meta(
    analysis: FileAnalysis,
    content: str,
    dependencies: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
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
    regions = _build_regions(analysis, content, imports)
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
        "regions": regions,
        "call_graph": call_graph,
        "dependencies": dependencies or [],
    }


def _build_regions(
    analysis: FileAnalysis,
    content: str,
    imports: List[str],
) -> List[Dict[str, Any]]:
    generator = DigestGenerator(content, analysis.language)
    source_bytes = content.encode("utf-8", errors="replace")
    classes = sorted(
        [cls for cls in analysis.classes if cls.name],
        key=lambda cls: ((cls.name or ""), cls.start_line, cls.end_line),
    )
    functions = sorted(
        [func for func in analysis.functions if func.name],
        key=lambda func: ((func.name or ""), func.start_line, func.end_line),
    )
    class_counts: Dict[str, int] = {}
    func_counts: Dict[str, int] = {}
    regions: List[Dict[str, Any]] = []

    module_map_text = generator.module_index(analysis).content
    regions.append(_region_entry(
        region_id="module:top",
        kind="module",
        name="top",
        qualified_name="top",
        container=None,
        start_line=1,
        end_line=max(1, analysis.line_count),
        map_text=module_map_text,
        content_text=content,
    ))

    class_ids: Dict[Tuple[int, int, str], str] = {}
    for cls in classes:
        base = f"class:{cls.name}"
        class_counts[base] = class_counts.get(base, 0) + 1
        region_id = base if class_counts[base] == 1 else f"{base}@{cls.start_line}"
        methods = [
            func for func in functions
            if cls.start_line <= func.start_line <= cls.end_line
        ]
        class_ids[(cls.start_line, cls.end_line, cls.name or "")] = region_id
        map_text = generator.class_structure(cls, methods).content
        content_text = _slice_region_content(source_bytes, cls.start_byte, cls.end_byte)
        regions.append(_region_entry(
            region_id=region_id,
            kind="class",
            name=cls.name or "UnknownClass",
            qualified_name=cls.name or "UnknownClass",
            container=None,
            start_line=cls.start_line,
            end_line=cls.end_line,
            map_text=map_text,
            content_text=content_text,
        ))

    for func in functions:
        container = _class_container_for_function(func, classes, class_ids)
        qualified_name = f"{container.split(':', 1)[1]}.{func.name}" if container else func.name
        base = f"func:{qualified_name}"
        func_counts[base] = func_counts.get(base, 0) + 1
        region_id = base if func_counts[base] == 1 else f"{base}@{func.start_line}"
        map_text = generator.function_signature(func).content
        content_text = _slice_region_content(source_bytes, func.start_byte, func.end_byte)
        regions.append(_region_entry(
            region_id=region_id,
            kind="function",
            name=func.name or "function",
            qualified_name=qualified_name or (func.name or "function"),
            container=container,
            start_line=func.start_line,
            end_line=func.end_line,
            map_text=map_text,
            content_text=content_text,
        ))

    regions.sort(key=lambda region: (region["start_line"], region["kind"], region["id"]))
    return regions


def _region_entry(
    region_id: str,
    kind: str,
    name: str,
    qualified_name: str,
    container: Optional[str],
    start_line: int,
    end_line: int,
    map_text: str,
    content_text: str,
) -> Dict[str, Any]:
    return {
        "id": region_id,
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "container": container,
        "start_line": start_line,
        "end_line": end_line,
        "map_hash": _stable_hash(map_text),
        "content_hash": _stable_hash(content_text),
        "map_git_oid": _git_blob_oid(map_text),
        "content_git_oid": _git_blob_oid(content_text),
    }


def _stable_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _git_blob_oid(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    payload = normalized.encode("utf-8")
    header = f"blob {len(payload)}\0".encode("utf-8")
    return f"git:blob:sha1:{hashlib.sha1(header + payload).hexdigest()}"


def _slice_region_content(source_bytes: bytes, start_byte: int, end_byte: int) -> str:
    return source_bytes[start_byte:end_byte].decode("utf-8", errors="ignore")


def _class_container_for_function(
    func,
    classes,
    class_ids: Dict[Tuple[int, int, str], str],
) -> Optional[str]:
    containing = [
        cls for cls in classes
        if cls.start_line <= func.start_line <= cls.end_line
    ]
    if not containing:
        return None
    cls = min(containing, key=lambda item: (item.end_line - item.start_line, item.start_line))
    return class_ids.get((cls.start_line, cls.end_line, cls.name or ""))


def _dependencies_for_file(result: AnalysisResult, filepath: str) -> List[Dict[str, Any]]:
    dependencies = []
    for edge in result.dependency_graph.edges:
        if edge.from_file != filepath:
            continue
        dependencies.append({
            "target": edge.to_file,
            "import_statement": " ".join(edge.import_statement.strip().split()),
            "import_type": edge.import_type,
            "is_external": edge.is_external,
        })
    dependencies.sort(key=lambda dep: (
        dep["target"],
        dep["import_statement"],
        dep["import_type"],
        dep["is_external"],
    ))
    return dependencies


def build_code_relations_json_payload(
    chunks: List[Chunk],
    mode: str,
    repo_root: str,
) -> Dict[str, Any]:
    for chunk in chunks:
        meta = chunk.meta if isinstance(chunk.meta, dict) else {}
        graph_payload = meta.get("code_relations_payload")
        if isinstance(graph_payload, dict):
            return graph_payload

    if mode == "mapnew":
        return _build_mapnew_json_payload(chunks, repo_root)

    file_chunks = [chunk for chunk in chunks if chunk.path and chunk.path != "__summary__"]
    files: List[Dict[str, Any]] = []
    entities: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    external_entities: Dict[str, Dict[str, Any]] = {}
    external_symbol_entities: Dict[str, Dict[str, Any]] = {}

    region_index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    file_regions: Dict[str, List[Dict[str, Any]]] = {}

    for chunk in file_chunks:
        meta = chunk.meta if isinstance(chunk.meta, dict) else {}
        file_id = _file_id(chunk.path)
        role = _file_role_from_chunk_text(chunk.text or "")
        file_record: Dict[str, Any] = {
            "file_id": file_id,
            "path": chunk.path,
            "language": meta.get("language"),
            "role": role,
            "stats": {
                "line_count": meta.get("line_count"),
                "imports_count": meta.get("imports_count"),
            },
        }
        display_key = "digest" if role.endswith("digest") or role == "digest" else "raw_text"
        file_record["display"] = {display_key: chunk.text}
        if chunk.images:
            file_record["images"] = chunk.to_json().get("images")
        if chunk.audios:
            file_record["audios"] = list(chunk.audios)
        if chunk.videos:
            file_record["videos"] = list(chunk.videos)
        files.append(file_record)

        regions = meta.get("regions", [])
        file_regions[chunk.path] = regions
        for region in regions:
            entity = _entity_from_region(chunk.path, region)
            entities.append(entity)
            region_index[(chunk.path, region["id"])] = entity

    for chunk in file_chunks:
        path = chunk.path
        if not path:
            continue
        meta = chunk.meta if isinstance(chunk.meta, dict) else {}
        module_entity_id = _entity_id(path, "module:top")

        for region in file_regions.get(path, []):
            if region["kind"] == "module":
                continue
            entity_id = _entity_id(path, region["id"])
            container_region = region.get("container")
            container_entity_id = _entity_id(path, container_region) if container_region else module_entity_id
            edges.append(_edge_record(
                kind="contains",
                from_entity_id=container_entity_id,
                to_entity_id=entity_id,
                path=path,
                start_line=region["start_line"],
                end_line=region["end_line"],
            ))

        for dep in meta.get("dependencies", []):
            to_entity_id = _dependency_target_entity_id(dep, external_entities)
            edges.append(_edge_record(
                kind="imports",
                from_entity_id=module_entity_id,
                to_entity_id=to_entity_id,
                path=path,
                start_line=_import_start_line(meta, dep["import_statement"]),
                end_line=_import_start_line(meta, dep["import_statement"]),
                attributes={
                    "import_type": dep["import_type"],
                    "import_statement": dep["import_statement"],
                    "is_external": dep["is_external"],
                },
            ))

        for call in meta.get("call_graph", []):
            caller_entity_id = _resolve_caller_entity_id(path, file_regions.get(path, []), call)
            callee_entity_id = _resolve_callee_entity_id(
                path,
                file_regions.get(path, []),
                call["callee"],
                external_symbol_entities,
            )
            edges.append(_edge_record(
                kind="calls",
                from_entity_id=caller_entity_id,
                to_entity_id=callee_entity_id,
                path=path,
                start_line=call["line"],
                end_line=call["line"],
            ))

    entities.extend(sorted(external_entities.values(), key=lambda entity: entity["entity_id"]))
    entities.extend(sorted(external_symbol_entities.values(), key=lambda entity: entity["entity_id"]))
    files.sort(key=lambda file: file["path"])
    entities.sort(key=lambda entity: (entity["file_id"] or "", entity["entity_id"]))
    edges.sort(key=lambda edge: (edge["kind"], edge["from_entity_id"], edge["to_entity_id"]))

    entity_counts: Dict[str, int] = {}
    for entity in entities:
        entity_counts[entity["kind"]] = entity_counts.get(entity["kind"], 0) + 1

    edge_counts: Dict[str, int] = {}
    for edge in edges:
        edge_counts[edge["kind"]] = edge_counts.get(edge["kind"], 0) + 1

    omitted_files = []
    for chunk in chunks:
        if chunk.path == "__summary__":
            continue
        if not chunk.path:
            continue
    return {
        "schema_version": "code-relations/v1",
        "mode": mode,
        "repo_root": repo_root,
        "summary": {
            "files_total": len(file_chunks),
            "files_analyzed": len(file_chunks),
            "files_omitted": len(omitted_files),
            "entity_counts": entity_counts,
            "edge_counts": edge_counts,
        },
        "files": files,
        "entities": entities,
        "edges": edges,
        "omitted_files": omitted_files,
    }


def _build_mapnew_json_payload(chunks: List[Chunk], repo_root: str) -> Dict[str, Any]:
    chunk = next((item for item in chunks if item.path == "mapnew.diff"), None)
    meta = chunk.meta if chunk and isinstance(chunk.meta, dict) else {}
    return {
        "schema_version": "code-relations/v1",
        "mode": "mapnew",
        "repo_root": repo_root,
        "summary": {
            "files_total": meta.get("changed_files_count", 0),
            "files_analyzed": meta.get("changed_files_count", 0),
            "files_omitted": 0,
            "entity_counts": {},
            "edge_counts": {},
        },
        "files": [],
        "entities": [],
        "edges": [],
        "omitted_files": [],
        "diff": {
            "old_ref": meta.get("old_ref"),
            "new_ref": meta.get("new_ref"),
            "files": meta.get("files", []),
            "implementation_only_regions": meta.get("implementation_only_regions", 0),
            "rendered_diff": chunk.text if chunk else "",
        },
    }


def _file_id(path: str) -> str:
    return f"file:{path}"


def _entity_id(path: str, region_id: str) -> str:
    return f"entity:{_file_id(path)}:{region_id}"


def _dependency_entity_id(target: str) -> str:
    return f"dep:{target}"


def _symbol_entity_id(symbol: str) -> str:
    return f"symbol:{symbol}"


def _entity_from_region(path: str, region: Dict[str, Any]) -> Dict[str, Any]:
    entity_id = _entity_id(path, region["id"])
    container_region = region.get("container")
    if region["kind"] == "module":
        container_entity_id = None
    elif container_region:
        container_entity_id = _entity_id(path, container_region)
    else:
        container_entity_id = _entity_id(path, "module:top")
    return {
        "entity_id": entity_id,
        "file_id": _file_id(path),
        "kind": region["kind"],
        "name": region["name"],
        "qualified_name": region["qualified_name"],
        "container_entity_id": container_entity_id,
        "location": {
            "path": path,
            "start_line": region["start_line"],
            "end_line": region["end_line"],
        },
        "hashes": {
            "map_git_oid": region.get("map_git_oid"),
            "content_git_oid": region.get("content_git_oid"),
        },
    }


def _file_role_from_chunk_text(text: str) -> str:
    first_line = text.splitlines()[0] if text else ""
    return "digest" if "(digest)" in first_line else "raw"


def _edge_record(
    kind: str,
    from_entity_id: str,
    to_entity_id: str,
    path: str,
    start_line: Optional[int],
    end_line: Optional[int],
    attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    edge_id = f"edge:{kind}:{from_entity_id}->{to_entity_id}:{path}:{start_line or 0}:{end_line or 0}"
    edge = {
        "edge_id": edge_id,
        "kind": kind,
        "from_entity_id": from_entity_id,
        "to_entity_id": to_entity_id,
        "location": {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
        },
    }
    if attributes:
        edge["attributes"] = attributes
    return edge


def _dependency_target_entity_id(
    dep: Dict[str, Any],
    external_entities: Dict[str, Dict[str, Any]],
) -> str:
    if not dep["is_external"] and dep["target"]:
        return _entity_id(dep["target"], "module:top")
    entity_id = _dependency_entity_id(dep["target"])
    if entity_id not in external_entities:
        external_entities[entity_id] = {
            "entity_id": entity_id,
            "file_id": None,
            "kind": "external_dependency",
            "name": dep["target"],
            "qualified_name": dep["target"],
            "container_entity_id": None,
            "hashes": {},
        }
    return entity_id


def _resolve_caller_entity_id(
    path: str,
    regions: List[Dict[str, Any]],
    call: Dict[str, Any],
) -> str:
    candidates = [
        region for region in regions
        if region["kind"] == "function"
        and (region["name"] == call["caller"] or region["qualified_name"].endswith(f".{call['caller']}"))
        and region["start_line"] <= call["line"] <= region["end_line"]
    ]
    if not candidates:
        candidates = [
            region for region in regions
            if region["kind"] == "function"
            and (region["name"] == call["caller"] or region["qualified_name"] == call["caller"])
        ]
    if candidates:
        best = min(candidates, key=lambda region: (region["end_line"] - region["start_line"], region["start_line"]))
        return _entity_id(path, best["id"])
    return _symbol_entity_id(f"{path}:{call['caller']}")


def _resolve_callee_entity_id(
    path: str,
    regions: List[Dict[str, Any]],
    callee: str,
    external_symbol_entities: Dict[str, Dict[str, Any]],
) -> str:
    candidates = [
        region for region in regions
        if region["kind"] == "function"
        and (region["name"] == callee or region["qualified_name"] == callee or region["qualified_name"].endswith(f".{callee}"))
    ]
    if candidates:
        best = min(candidates, key=lambda region: (region["end_line"] - region["start_line"], region["start_line"]))
        return _entity_id(path, best["id"])
    entity_id = _symbol_entity_id(callee)
    if entity_id not in external_symbol_entities:
        external_symbol_entities[entity_id] = {
            "entity_id": entity_id,
            "file_id": None,
            "kind": "external_symbol",
            "name": callee,
            "qualified_name": callee,
            "container_entity_id": None,
            "hashes": {},
        }
    return entity_id


def _import_start_line(meta: Dict[str, Any], import_statement: str) -> Optional[int]:
    imports = meta.get("imports", [])
    try:
        index = imports.index(import_statement)
        return index + 1
    except ValueError:
        return None


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
