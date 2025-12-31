"""
Code Relationship Analyzer

A module for AST extraction, dependency mapping, and code traversal networks.
Enhances scrape_directory with code relationship understanding.
"""

from .types import (
    ASTNode,
    FileAnalysis,
    DependencyEdge,
    DependencyGraph,
    SemanticTag,
    TraversalNode,
    AnalysisResult,
)
from .ast_extractor import (
    ASTExtractor,
    get_extractor,
    extract_file,
    EXTENSION_TO_LANGUAGE,
)
from .dependency_map import (
    DependencyMapper,
    build_analysis_result,
    register_resolver,
    get_registered_languages,
)
from .digest import (
    Digest,
    DigestGenerator,
    generate_file_digest,
)
from .semantic_tagger import (
    SemanticTagger,
    build_semantic_index,
    SEMANTIC_PATTERNS,
)
from .api import (
    Analyzer,
    AnalyzerConfig,
    analyze_directory,
    analyze_files,
    discover_files,
)

__all__ = [
    # Types
    "ASTNode",
    "FileAnalysis", 
    "DependencyEdge",
    "DependencyGraph",
    "SemanticTag",
    "TraversalNode",
    "AnalysisResult",
    # AST Extraction
    "ASTExtractor",
    "get_extractor",
    "extract_file",
    "EXTENSION_TO_LANGUAGE",
    # Dependency Mapping
    "DependencyMapper",
    "build_analysis_result",
    "register_resolver",
    "get_registered_languages",
    # Digest
    "Digest",
    "DigestGenerator",
    "generate_file_digest",
    # Semantic
    "SemanticTagger",
    "build_semantic_index",
    "SEMANTIC_PATTERNS",
    # API
    "Analyzer",
    "AnalyzerConfig",
    "analyze_directory",
    "analyze_files",
    "discover_files",
]
