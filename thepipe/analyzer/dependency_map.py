"""
Dependency Mapper

Resolves imports to file paths and builds a dependency graph.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import logging

from .types import (
    FileAnalysis,
    DependencyEdge,
    DependencyGraph,
    AnalysisResult,
)

logger = logging.getLogger(__name__)


# ============================================================================
# PLUGIN SYSTEM FOR CUSTOM DEPENDENCY RESOLVERS
# ============================================================================

from typing import Callable

# Global registry for custom language resolvers
_CUSTOM_RESOLVERS: Dict[str, Callable] = {}

def register_resolver(
    language: str,
    resolver_func: Callable[[str, str, 'DependencyMapper'], Optional[DependencyEdge]]
) -> None:
    """
    Register a custom dependency resolver for a language.
    
    Allows community extensions to add support for languages not built-in.
    
    Args:
        language: Language identifier (e.g., 'dart', 'swift', 'kotlin')
        resolver_func: Function(import_stmt, from_file, mapper) -> DependencyEdge | None
    
    Example:
        def resolve_dart(stmt, file, mapper):
            # Parse Dart imports
            return DependencyEdge(...) or None
        
        register_resolver('dart', resolve_dart)
    """
    _CUSTOM_RESOLVERS[language.lower()] = resolver_func
    logger.info(f"Registered custom resolver for: {language}")

def get_registered_languages() -> List[str]:
    """Get list of languages with custom resolvers."""
    return list(_CUSTOM_RESOLVERS.keys())



class DependencyMapper:
    """
    Maps imports between files and builds a dependency graph.
    
    Handles:
    - Relative imports (from . import, ./, ../)
    - Absolute imports (from package.module import)
    - Package detection (setup.py, pyproject.toml, package.json)
    """
    
    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self._file_index: Dict[str, str] = {}  # module_name -> filepath
        self._package_roots: List[Path] = []
    
    def build_graph(self, file_analyses: Dict[str, FileAnalysis]) -> DependencyGraph:
        """
        Build a dependency graph from file analyses.
        
        Args:
            file_analyses: Dict of filepath -> FileAnalysis
            
        Returns:
            DependencyGraph with edges and adjacency lists
        """
        graph = DependencyGraph()
        
        # Build file index for import resolution
        self._build_file_index(file_analyses)
        
        # Process each file's imports
        for filepath, analysis in file_analyses.items():
            for import_stmt in analysis.imports:
                edge = self._resolve_import(
                    import_stmt=import_stmt,
                    from_file=filepath,
                    language=analysis.language
                )
                if edge:
                    graph.add_edge(edge)
        
        return graph
    
    def _build_file_index(self, file_analyses: Dict[str, FileAnalysis]) -> None:
        """Build an index of module names to file paths"""
        self._file_index.clear()
        
        for filepath in file_analyses.keys():
            path = Path(filepath)
            
            # Add various forms of the module name
            # e.g., "thepipe/scraper.py" -> "thepipe.scraper", "scraper"
            
            # Full path without extension
            rel_path = path.relative_to(self.repo_root) if path.is_absolute() else path
            module_path = str(rel_path.with_suffix('')).replace(os.sep, '.')
            self._file_index[module_path] = filepath
            
            # Just the filename without extension
            self._file_index[path.stem] = filepath
            
            # For JavaScript/TypeScript, also index with extensions
            if path.suffix in ('.js', '.ts', '.jsx', '.tsx'):
                self._file_index[str(rel_path)] = filepath
    
    def _resolve_import(
        self,
        import_stmt: str,
        from_file: str,
        language: str
    ) -> Optional[DependencyEdge]:
        """
        Resolve an import statement to a file path.
        
        Checks built-in resolvers first, then custom registered resolvers.
        
        Returns DependencyEdge or None if resolution fails.
        """
        # Built-in language resolvers
        if language == 'python':
            return self._resolve_python_import(import_stmt, from_file)
        elif language in ('javascript', 'typescript'):
            return self._resolve_js_import(import_stmt, from_file)
        elif language == 'go':
            return self._resolve_go_import(import_stmt, from_file)
        elif language == 'rust':
            return self._resolve_rust_import(import_stmt, from_file)
        elif language in ('c', 'cpp'):
            return self._resolve_c_import(import_stmt, from_file)
        
        # Check custom resolver registry (plugin system)
        if language in _CUSTOM_RESOLVERS:
            try:
                return _CUSTOM_RESOLVERS[language](import_stmt, from_file, self)
            except Exception as e:
                logger.warning(f"Custom resolver for {language} failed: {e}")
                return None
        
        # No resolver available for this language
        return None
    
    def _resolve_python_import(
        self, import_stmt: str, from_file: str
    ) -> Optional[DependencyEdge]:
        """Resolve Python import statements"""
        
        # Parse the import statement
        # "from package.module import something"
        # "import package.module"
        # "from . import sibling"
        # "from ..parent import something"
        
        module_name = None
        is_relative = False
        
        # Match "from X import Y" pattern
        from_match = re.match(r'from\s+(\.*)(\S*)\s+import', import_stmt)
        if from_match:
            dots = from_match.group(1)
            module_part = from_match.group(2)
            
            if dots:
                # Relative import
                is_relative = True
                levels_up = len(dots) - 1
                from_path = Path(from_file)
                
                # Navigate up the directory tree
                current_dir = from_path.parent
                for _ in range(levels_up):
                    current_dir = current_dir.parent
                
                if module_part:
                    # from ..foo import bar -> look for foo in parent
                    target_path = current_dir / module_part.replace('.', os.sep)
                else:
                    # from . import sibling -> current directory
                    target_path = current_dir
                
                # Try to find the actual file
                for ext in ['.py', '/__init__.py', '']:
                    candidate = Path(str(target_path) + ext)
                    try:
                        if candidate.exists() or str(candidate) in self._file_index.values():
                            try:
                                module_name = str(candidate.relative_to(self.repo_root))
                            except ValueError:
                                # Path isn't under repo root, use as-is
                                module_name = str(candidate)
                            break
                    except Exception:
                        continue
            else:
                # Absolute import
                module_name = module_part
        else:
            # Match "import X" pattern
            import_match = re.match(r'import\s+(\S+)', import_stmt)
            if import_match:
                module_name = import_match.group(1).split(',')[0].strip()
        
        if not module_name:
            return None
        
        # Try to resolve to a file in our index
        resolved_file = self._lookup_module(module_name, from_file, 'python')
        
        is_external = resolved_file is None
        
        return DependencyEdge(
            from_file=from_file,
            to_file=resolved_file or module_name,
            import_statement=import_stmt,
            import_type='import',
            is_external=is_external,
        )
    
    def _resolve_js_import(
        self, import_stmt: str, from_file: str
    ) -> Optional[DependencyEdge]:
        """Resolve JavaScript/TypeScript imports"""
        
        # Match import "path" or require("path")
        path_match = re.search(r'["\']([^"\']+)["\']', import_stmt)
        if not path_match:
            return None
        
        import_path = path_match.group(1)
        
        # Check if relative import
        if import_path.startswith('.'):
            from_dir = Path(from_file).parent
            resolved = (from_dir / import_path).resolve()
            
            # Try with various extensions
            for ext in ['', '.js', '.ts', '.jsx', '.tsx', '/index.js', '/index.ts']:
                candidate = Path(str(resolved) + ext)
                try:
                    rel_path = str(candidate.relative_to(self.repo_root))
                    if rel_path in self._file_index.values():
                        return DependencyEdge(
                            from_file=from_file,
                            to_file=rel_path,
                            import_statement=import_stmt,
                            import_type='import',
                            is_external=False,
                        )
                except ValueError:
                    pass
            
            # Couldn't resolve but it's a relative import
            return DependencyEdge(
                from_file=from_file,
                to_file=import_path,
                import_statement=import_stmt,
                import_type='import',
                is_external=False,
            )
        else:
            # Package import (npm, etc.)
            return DependencyEdge(
                from_file=from_file,
                to_file=import_path,
                import_statement=import_stmt,
                import_type='import',
                is_external=True,
            )
    
    def _resolve_go_import(
        self, import_stmt: str, from_file: str
    ) -> Optional[DependencyEdge]:
        """Resolve Go imports"""
        
        path_match = re.search(r'"([^"]+)"', import_stmt)
        if not path_match:
            return None
        
        import_path = path_match.group(1)
        
        # Check if it's a local package
        is_external = not any(
            import_path.startswith(prefix)
            for prefix in ['./', '../', str(self.repo_root)]
        )
        
        return DependencyEdge(
            from_file=from_file,
            to_file=import_path,
            import_statement=import_stmt,
            import_type='import',
            is_external=is_external,
        )
    
    def _resolve_rust_import(
        self, import_stmt: str, from_file: str
    ) -> Optional[DependencyEdge]:
        """Resolve Rust use/mod statements"""
        
        # Match "use crate::module" or "mod module"
        use_match = re.search(r'use\s+(crate::)?(\S+)', import_stmt)
        mod_match = re.search(r'mod\s+(\S+);', import_stmt)
        
        if use_match:
            path = use_match.group(2).rstrip(';').split('::')[0]
            is_external = use_match.group(1) is None and path not in ('self', 'super')
        elif mod_match:
            path = mod_match.group(1)
            is_external = False
        else:
            return None
        
        return DependencyEdge(
            from_file=from_file,
            to_file=path,
            import_statement=import_stmt,
            import_type='use' if use_match else 'mod',
            is_external=is_external,
        )
    
    def _resolve_c_import(
        self, import_stmt: str, from_file: str
    ) -> Optional[DependencyEdge]:
        """Resolve C/C++ #include statements"""
        
        # Match #include "header.h" or #include <header.h>
        local_match = re.search(r'#include\s*"([^"]+)"', import_stmt)
        system_match = re.search(r'#include\s*<([^>]+)>', import_stmt)
        
        if local_match:
            header_path = local_match.group(1)
            from_dir = Path(from_file).parent
            resolved = from_dir / header_path
            
            return DependencyEdge(
                from_file=from_file,
                to_file=str(resolved),
                import_statement=import_stmt,
                import_type='include',
                is_external=False,
            )
        elif system_match:
            return DependencyEdge(
                from_file=from_file,
                to_file=system_match.group(1),
                import_statement=import_stmt,
                import_type='include',
                is_external=True,
            )
        
        return None
    
    def _lookup_module(
        self, module_name: str, from_file: str, language: str
    ) -> Optional[str]:
        """Look up a module name in our file index"""
        
        # Direct match
        if module_name in self._file_index:
            return self._file_index[module_name]
        
        # Try with dots replaced by path separators
        path_form = module_name.replace('.', os.sep)
        if path_form in self._file_index:
            return self._file_index[path_form]
        
        # Try adding .py extension
        if language == 'python':
            py_path = path_form + '.py'
            for filepath in self._file_index.values():
                if filepath.endswith(py_path):
                    return filepath
            
            # Try __init__.py for packages
            init_path = os.path.join(path_form, '__init__.py')
            for filepath in self._file_index.values():
                if filepath.endswith(init_path):
                    return filepath
        
        return None


def build_analysis_result(
    repo_root: str,
    file_analyses: Dict[str, FileAnalysis]
) -> AnalysisResult:
    """
    Build a complete analysis result with dependency graph.
    
    Args:
        repo_root: Root path of the repository
        file_analyses: Dict of filepath -> FileAnalysis
        
    Returns:
        AnalysisResult with files, dependency graph, and statistics
    """
    mapper = DependencyMapper(repo_root)
    graph = mapper.build_graph(file_analyses)
    
    result = AnalysisResult(
        repo_root=repo_root,
        files=file_analyses,
        dependency_graph=graph,
        total_files=len(file_analyses),
    )
    
    # Calculate statistics
    for analysis in file_analyses.values():
        result.total_functions += len(analysis.functions)
        result.total_classes += len(analysis.classes)
        
        lang = analysis.language
        result.languages[lang] = result.languages.get(lang, 0) + 1
    
    return result
