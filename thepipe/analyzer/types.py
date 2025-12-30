"""
Code Relationship Analyzer Types

Shared dataclasses for AST extraction, dependency mapping, and traversal networks.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class ASTNode:
    """Represents a node in the AST (function, class, import, etc.)"""
    type: str  # "function", "class", "import", "module"
    name: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    start_byte: int = 0
    end_byte: int = 0
    children: List['ASTNode'] = field(default_factory=list)
    source_text: Optional[str] = None  # Original source if needed


@dataclass
class FileAnalysis:
    """Complete analysis of a single source file"""
    path: str  # Canonical path relative to repo root
    language: str  # "python", "javascript", "typescript", etc.
    imports: List[str] = field(default_factory=list)  # Raw import strings
    exports: List[str] = field(default_factory=list)  # Exported symbols
    functions: List[ASTNode] = field(default_factory=list)
    classes: List[ASTNode] = field(default_factory=list)
    
    # Metadata
    size_bytes: int = 0
    line_count: int = 0
    
    # For cross-language detection
    is_cross_language: bool = False  # Cython, SWIG, FFI
    platform_specific: Optional[str] = None  # "ios", "android", None


@dataclass
class DependencyEdge:
    """Represents an import relationship between two files"""
    from_file: str  # Canonical path of importing file
    to_file: str  # Canonical path of imported file (resolved)
    import_statement: str  # Raw import text
    import_type: str = "import"  # "import", "require", "include", "cimport"
    is_external: bool = False  # Points outside repo
    language_bridge: Optional[str] = None  # "cython", "ctypes", "jni"


@dataclass
class DependencyGraph:
    """Complete dependency graph for a repository"""
    edges: List[DependencyEdge] = field(default_factory=list)
    adjacency: Dict[str, List[str]] = field(default_factory=dict)  # file -> [imports]
    reverse_adjacency: Dict[str, List[str]] = field(default_factory=dict)  # file -> [imported_by]
    
    def add_edge(self, edge: DependencyEdge) -> None:
        """Add an edge to the graph"""
        self.edges.append(edge)
        
        if edge.from_file not in self.adjacency:
            self.adjacency[edge.from_file] = []
        if not edge.is_external:
            self.adjacency[edge.from_file].append(edge.to_file)
        
        if not edge.is_external:
            if edge.to_file not in self.reverse_adjacency:
                self.reverse_adjacency[edge.to_file] = []
            self.reverse_adjacency[edge.to_file].append(edge.from_file)
    
    def nearest_neighbors(self, target_file: str, depth: int = 1) -> Set[str]:
        """BFS to find N-nearest neighbor files by imports"""
        visited: Set[str] = set()
        current_level: Set[str] = {target_file}
        
        for _ in range(depth):
            next_level: Set[str] = set()
            for file in current_level:
                if file in visited:
                    continue
                visited.add(file)
                # Add files this file imports
                next_level.update(self.adjacency.get(file, []))
                # Add files that import this file
                next_level.update(self.reverse_adjacency.get(file, []))
            current_level = next_level - visited
        
        visited.discard(target_file)  # Don't include the target itself
        return visited
    
    def imports_of(self, file: str) -> List[str]:
        """Get files that this file imports"""
        return self.adjacency.get(file, [])
    
    def imported_by(self, file: str) -> List[str]:
        """Get files that import this file"""
        return self.reverse_adjacency.get(file, [])


@dataclass
class SemanticTag:
    """Semantic concern tag for a code location"""
    tag: str  # "oauth", "state-machine", "networking", etc.
    confidence: float = 1.0
    source: str = "pattern"  # "pattern", "comment", "name"
    file: Optional[str] = None
    line: Optional[int] = None


@dataclass
class TraversalNode:
    """A node in the traversal network for agent navigation"""
    id: str  # Canonical path
    language: str
    chunk_type: str = "file"  # "file", "class", "function"
    name: str = ""
    
    # Content sizing
    code_tokens: int = 0
    digest_tokens: int = 0
    keep_as_code: bool = True  # False = use digest instead
    
    # Navigation
    imports: List[str] = field(default_factory=list)
    imported_by: List[str] = field(default_factory=list)
    
    # Semantic
    tags: List[str] = field(default_factory=list)
    
    # Metadata
    size_bytes: int = 0


@dataclass 
class AnalysisResult:
    """Complete analysis result for a repository/directory"""
    repo_root: str
    files: Dict[str, FileAnalysis] = field(default_factory=dict)
    dependency_graph: DependencyGraph = field(default_factory=DependencyGraph)
    semantic_tags: Dict[str, List[str]] = field(default_factory=dict)  # tag -> [files]
    
    # Statistics
    total_files: int = 0
    total_functions: int = 0
    total_classes: int = 0
    languages: Dict[str, int] = field(default_factory=dict)  # language -> count
