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
]
