"""
Main Analyzer API

High-level interface for analyzing repositories and directories.
This is where all components come together.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Union, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import fnmatch
import logging
import json

from .types import (
    FileAnalysis,
    DependencyGraph,
    AnalysisResult,
    TraversalNode,
    SemanticTag,
)
from .ast_extractor import ASTExtractor, get_extractor, EXTENSION_TO_LANGUAGE
from .dependency_map import DependencyMapper, build_analysis_result
from .digest import DigestGenerator, generate_file_digest, Digest
from .semantic_tagger import SemanticTagger, build_semantic_index

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class AnalyzerConfig:
    """
    Configuration for the code analyzer.
    
    This is the main options object - adjust these to control analysis behavior.
    """
    
    # File Discovery
    respect_gitignore: bool = True
    include_patterns: List[str] = field(default_factory=lambda: [
        "**/*.py", "**/*.js", "**/*.ts", "**/*.tsx", "**/*.jsx",
        "**/*.go", "**/*.rs", "**/*.c", "**/*.cpp", "**/*.h",
        "**/*.java", "**/*.kt", "**/*.swift", "**/*.rb",
    ])
    exclude_patterns: List[str] = field(default_factory=lambda: [
        "**/__pycache__/**", "**/node_modules/**", "**/.git/**",
        "**/venv/**", "**/.venv/**", "**/dist/**", "**/build/**",
        "**/*.min.js", "**/*.bundle.js",
    ])
    max_file_size_mb: float = 10.0
    follow_symlinks: bool = False
    
    # Analysis Scope
    target_files: Optional[List[str]] = None  # Analyze only these + neighbors
    neighbor_depth: int = 2  # How many levels of imports to include
    include_external_deps: bool = False  # Include external package refs
    
    # Features
    extract_ast: bool = True
    build_dependency_graph: bool = True
    generate_digests: bool = True
    generate_semantic_tags: bool = True
    
    # Digest Thresholds (compression ratio required to use digest vs full code)
    digest_threshold_small: float = 0.50   # < 100 lines
    digest_threshold_medium: float = 0.70  # 100-500 lines
    digest_threshold_large: float = 0.80   # > 500 lines
    
    # Output
    output_format: str = "json"  # "json", "dict"
    include_source_in_output: bool = False  # Include full source code
    
    # Performance
    max_workers: int = 4  # Parallel file processing
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary for serialization"""
        return {
            "respect_gitignore": self.respect_gitignore,
            "include_patterns": self.include_patterns,
            "exclude_patterns": self.exclude_patterns,
            "max_file_size_mb": self.max_file_size_mb,
            "neighbor_depth": self.neighbor_depth,
            "extract_ast": self.extract_ast,
            "build_dependency_graph": self.build_dependency_graph,
            "generate_digests": self.generate_digests,
            "generate_semantic_tags": self.generate_semantic_tags,
        }


# ============================================================================
# FILE DISCOVERY
# ============================================================================

def discover_files(
    root: str,
    config: AnalyzerConfig,
) -> List[str]:
    """
    Discover files to analyze based on config patterns.
    
    Returns list of absolute file paths.
    """
    root_path = Path(root).resolve()
    files: List[str] = []
    
    # Load gitignore patterns
    gitignore_patterns: List[str] = []
    if config.respect_gitignore:
        gitignore_path = root_path / ".gitignore"
        if gitignore_path.exists():
            with open(gitignore_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        gitignore_patterns.append(line)
    
    def should_include(path: Path) -> bool:
        """Check if file matches include patterns and not exclude"""
        rel_path = str(path.relative_to(root_path))
        
        # Check gitignore
        for pattern in gitignore_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return False
            if fnmatch.fnmatch(path.name, pattern):
                return False
        
        # Check exclude patterns
        for pattern in config.exclude_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return False
        
        # Check include patterns
        for pattern in config.include_patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
        
        return False
    
    def should_traverse(path: Path) -> bool:
        """Check if directory should be traversed"""
        name = path.name
        return name not in {
            '__pycache__', 'node_modules', '.git', 'venv', '.venv',
            'dist', 'build', '.tox', '.eggs', '.mypy_cache',
        }
    
    # Walk directory tree
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=config.follow_symlinks):
        current = Path(dirpath)
        
        # Filter directories to traverse
        dirnames[:] = [d for d in dirnames if should_traverse(current / d)]
        
        for filename in filenames:
            filepath = current / filename
            
            # Check file size
            try:
                size_mb = filepath.stat().st_size / (1024 * 1024)
                if size_mb > config.max_file_size_mb:
                    continue
            except OSError:
                continue
            
            if should_include(filepath):
                files.append(str(filepath))
    
    return sorted(files)


# ============================================================================
# MAIN ANALYZER
# ============================================================================

class Analyzer:
    """
    Main analyzer class for repository code analysis.
    
    Usage:
        analyzer = Analyzer("/path/to/repo")
        result = analyzer.analyze()
        
        # Or with config:
        config = AnalyzerConfig(
            include_patterns=["**/*.py"],
            neighbor_depth=3,
        )
        result = analyzer.analyze(config=config)
    """
    
    def __init__(self, repo_root: str, config: Optional[AnalyzerConfig] = None):
        self.repo_root = Path(repo_root).resolve()
        self.config = config or AnalyzerConfig()
        self.extractor = get_extractor()
        self.tagger = SemanticTagger()
    
    def analyze(
        self,
        config: Optional[AnalyzerConfig] = None,
        target_files: Optional[List[str]] = None,
    ) -> AnalysisResult:
        """
        Analyze the repository.
        
        Args:
            config: Override default config
            target_files: Analyze only these files + their dependencies
            
        Returns:
            AnalysisResult with files, dependency graph, semantic tags, etc.
        """
        cfg = config or self.config
        if target_files:
            cfg.target_files = target_files
        
        # Step 1: Discover files (or use target_files if provided)
        if target_files:
            # Use provided files directly - convert to absolute paths
            files = [str(self.repo_root / f) for f in target_files]
            logger.info(f"Using {len(files)} provided target files")
        else:
            logger.info(f"Discovering files in {self.repo_root}...")
            files = discover_files(str(self.repo_root), cfg)
            logger.info(f"Found {len(files)} files to analyze")
        
        # Step 2: Extract AST from each file
        logger.info("Extracting AST...")
        file_analyses: Dict[str, FileAnalysis] = {}
        file_sources: Dict[str, str] = {}
        
        def process_file(filepath: str) -> Optional[tuple]:
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    source = f.read()
                
                rel_path = str(Path(filepath).relative_to(self.repo_root))
                language = self.extractor.detect_language(filepath)
                
                if not language:
                    return None
                
                analysis = self.extractor.extract(source, language, rel_path)
                if analysis:
                    return (rel_path, analysis, source)
            except Exception as e:
                logger.debug(f"Failed to process {filepath}: {e}")
            return None
        
        # Process files in parallel
        with ThreadPoolExecutor(max_workers=cfg.max_workers) as executor:
            futures = {executor.submit(process_file, f): f for f in files}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    rel_path, analysis, source = result
                    file_analyses[rel_path] = analysis
                    file_sources[rel_path] = source
        
        logger.info(f"Analyzed {len(file_analyses)} files")
        
        # Step 3: Build dependency graph
        if cfg.build_dependency_graph:
            logger.info("Building dependency graph...")
            result = build_analysis_result(str(self.repo_root), file_analyses)
        else:
            result = AnalysisResult(
                repo_root=str(self.repo_root),
                files=file_analyses,
            )
        
        # Step 4: Generate semantic tags
        if cfg.generate_semantic_tags:
            logger.info("Generating semantic tags...")
            file_tags: Dict[str, List[SemanticTag]] = {}
            for rel_path, source in file_sources.items():
                tags = self.tagger.tag_content(source, rel_path)
                if tags:
                    file_tags[rel_path] = tags
            
            result.semantic_tags = build_semantic_index(file_tags)
        
        # Step 5: Generate digests (if requested)
        # Store in a separate dict for now
        self._digests: Dict[str, str] = {}
        if cfg.generate_digests:
            logger.info("Generating digests...")
            for rel_path, analysis in file_analyses.items():
                if rel_path in file_sources:
                    digest = generate_file_digest(
                        file_sources[rel_path],
                        analysis,
                    )
                    self._digests[rel_path] = digest
        
        return result
    
    def get_digest(self, filepath: str) -> Optional[str]:
        """Get the digest for a specific file"""
        return self._digests.get(filepath)
    
    def get_context(
        self,
        target_file: str,
        token_budget: int = 8000,
        include_neighbors: bool = True,
        prefer_code_for_tags: Optional[List[str]] = None,
    ) -> str:
        """
        Build context for an LLM agent starting from a target file.
        
        Args:
            target_file: The file to center context around
            token_budget: Approximate token limit
            include_neighbors: Include imported files
            prefer_code_for_tags: Keep full code for files with these semantic tags
            
        Returns:
            Formatted context string
        """
        # This is a placeholder - full implementation would:
        # 1. Start with target file (full code if small, else digest)
        # 2. Add nearest neighbors (digest by default)
        # 3. Expand digests to code if budget remaining
        # 4. Prefer code for files matching prefer_code_for_tags
        
        parts = []
        
        # Add target file
        if target_file in self._digests:
            parts.append(f"# {target_file}\n{self._digests[target_file]}")
        
        return "\n\n".join(parts)
    
    def to_json(self, result: AnalysisResult) -> str:
        """Serialize analysis result to JSON"""
        data = {
            "version": "1.0",
            "repo_root": result.repo_root,
            "statistics": {
                "total_files": result.total_files,
                "total_functions": result.total_functions,
                "total_classes": result.total_classes,
                "languages": result.languages,
            },
            "files": {},
            "dependency_graph": {
                "edges": [
                    {
                        "from": e.from_file,
                        "to": e.to_file,
                        "type": e.import_type,
                        "external": e.is_external,
                    }
                    for e in result.dependency_graph.edges
                ],
            },
            "semantic_index": result.semantic_tags,
        }
        
        for path, analysis in result.files.items():
            data["files"][path] = {
                "language": analysis.language,
                "imports": len(analysis.imports),
                "functions": [f.name for f in analysis.functions if f.name],
                "classes": [c.name for c in analysis.classes if c.name],
                "lines": analysis.line_count,
            }
            if path in self._digests:
                data["files"][path]["digest"] = self._digests[path]
        
        return json.dumps(data, indent=2)


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def analyze_directory(
    path: str,
    include_patterns: Optional[List[str]] = None,
    neighbor_depth: int = 2,
    **kwargs
) -> AnalysisResult:
    """
    Convenience function to analyze a directory.
    
    Args:
        path: Path to directory
        include_patterns: Glob patterns for files to include
        neighbor_depth: Levels of imports to include
        **kwargs: Additional AnalyzerConfig options
        
    Returns:
        AnalysisResult
    """
    config = AnalyzerConfig(
        neighbor_depth=neighbor_depth,
        **kwargs
    )
    if include_patterns:
        config.include_patterns = include_patterns
    
    analyzer = Analyzer(path, config)
    return analyzer.analyze()


def analyze_files(
    repo_root: str,
    files: List[str],
    neighbor_depth: int = 2,
) -> AnalysisResult:
    """
    Analyze specific files and their dependencies.
    
    Args:
        repo_root: Repository root path
        files: List of file paths relative to repo_root
        neighbor_depth: Levels of imports to include
        
    Returns:
        AnalysisResult
    """
    config = AnalyzerConfig(
        target_files=files,
        neighbor_depth=neighbor_depth,
    )
    analyzer = Analyzer(repo_root, config)
    return analyzer.analyze()
