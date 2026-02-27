"""
Dependency Mapper

Resolves imports to file paths and builds a dependency graph.
"""

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import logging

from .types import (
    FileAnalysis,
    DependencyEdge,
    DependencyGraph,
    AnalysisResult,
)
from .ast_extractor import EXTENSION_TO_LANGUAGE
from .plugins import PluginConfig, register_builtin_plugins
from .plugins.base import LanguagePlugin

logger = logging.getLogger(__name__)

_PLUGIN_CONTEXT_PRUNE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "build",
    "dist",
    "target",
}


# ============================================================================
# PLUGIN SYSTEM FOR CUSTOM DEPENDENCY RESOLVERS
# ============================================================================

# Global registry for custom language resolvers
_CUSTOM_RESOLVERS: Dict[str, Callable] = {}

def register_resolver(
    language: str,
    resolver_func: Callable[[str, str, 'DependencyMapper'], Optional[DependencyEdge]]
) -> None:
    """
    Register a custom dependency resolver for a language.

    This is a legacy extension point kept for backward compatibility.
    Resolution order in DependencyMapper is:
    1) built-in or plugin (based on configured precedence)
    2) custom resolver fallback

    Use plugin-based resolvers for new language integrations.
    
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

    Resolver architecture:
    - Built-in resolvers handle core languages.
    - Extension-based plugins can augment behavior (configurable precedence).
    - Legacy language-keyed custom resolvers remain as fallback compatibility.
    """
    
    def __init__(
        self,
        repo_root: str,
        *,
        enable_plugin_resolvers: bool = True,
        plugin_resolver_precedence: str = "builtin_first",
        allow_unindexed_internal_targets: bool = False,
    ):
        self.repo_root = Path(repo_root).resolve()
        self._file_index: Dict[str, str] = {}  # module_name -> filepath
        self._stem_index: Dict[str, List[str]] = {}
        self._indexed_file_paths: List[str] = []
        self._indexed_file_path_set_abs: Set[str] = set()
        self._indexed_file_path_set_rel: Set[str] = set()
        self._dart_lib_path_index: Dict[str, List[str]] = {}
        self._package_roots: List[Path] = []
        self._plugin_manager = None
        self._enable_plugin_resolvers = enable_plugin_resolvers
        self._allow_unindexed_internal_targets = allow_unindexed_internal_targets
        if plugin_resolver_precedence not in {"builtin_first", "plugin_first"}:
            raise ValueError(
                "plugin_resolver_precedence must be 'builtin_first' or 'plugin_first'"
            )
        self._plugin_resolver_precedence = plugin_resolver_precedence
        self._plugin_config = PluginConfig(
            project_root=self.repo_root,
            context={},
        )
        
        if self._enable_plugin_resolvers:
            try:
                # Idempotent singleton registration; returns the shared plugin manager.
                self._plugin_manager = register_builtin_plugins()
                self._plugin_config.context = self._load_plugin_context()
            except Exception as e:
                logger.warning(
                    "Plugin initialization failed in DependencyMapper",
                    exc_info=True,
                )
    
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
        self._stem_index.clear()
        self._indexed_file_path_set_abs.clear()
        self._indexed_file_path_set_rel.clear()
        self._dart_lib_path_index.clear()
        
        ordered_paths = sorted(file_analyses.keys())
        for filepath in ordered_paths:
            path = Path(filepath)
            
            # Add various forms of the module name
            # e.g., "thepipe/scraper.py" -> "thepipe.scraper", "scraper"
            
            # Full path without extension
            rel_path = path.relative_to(self.repo_root) if path.is_absolute() else path
            module_path = str(rel_path.with_suffix('')).replace(os.sep, '.')
            self._file_index.setdefault(module_path, filepath)
            
            # Track same-stem files to avoid nondeterministic overwrite.
            self._stem_index.setdefault(path.stem, []).append(filepath)
            
            # For JavaScript/TypeScript, also index with extensions
            if path.suffix in ('.js', '.ts', '.jsx', '.tsx'):
                self._file_index.setdefault(str(rel_path), filepath)

        # Add stem shortcuts only when unambiguous.
        for stem, candidates in self._stem_index.items():
            if len(candidates) == 1:
                self._file_index[stem] = candidates[0]

        self._indexed_file_paths = sorted(set(self._file_index.values()))
        for filepath in self._indexed_file_paths:
            path = Path(filepath)
            if path.is_absolute():
                self._indexed_file_path_set_abs.add(os.path.normpath(str(path)))
                try:
                    rel = os.path.normpath(str(path.relative_to(self.repo_root)))
                    self._indexed_file_path_set_rel.add(rel)
                except ValueError:
                    pass
            else:
                rel = os.path.normpath(str(path))
                self._indexed_file_path_set_rel.add(rel)
                abs_path = os.path.normpath(str(self.repo_root / path))
                self._indexed_file_path_set_abs.add(abs_path)

        # Cache Dart package-import candidates keyed by "lib/<rel_path>" suffix.
        for filepath in self._indexed_file_paths:
            normalized = filepath.replace("\\", "/")
            segments = [segment for segment in normalized.split("/") if segment]
            for idx, segment in enumerate(segments):
                if segment == "lib" and idx + 1 < len(segments):
                    lib_suffix = "/".join(segments[idx:])
                    self._dart_lib_path_index.setdefault(lib_suffix, []).append(filepath)
                    break

    def lookup_dart_package_file(self, package_name: str, rel_path: str) -> Optional[str]:
        """
        Resolve a Dart `package:` import to a repo file using cached lib-path suffixes.

        Prefers `<package_name>/lib/...` matches when multiple packages provide the same
        relative import path in a monorepo, then falls back to lexicographic order.
        """
        lib_suffix = f"lib/{rel_path}".replace("\\", "/").lstrip("/")
        candidates = self._dart_lib_path_index.get(lib_suffix)
        if not candidates:
            return None

        def candidate_key(filepath: str) -> Tuple[int, str]:
            normalized = filepath.replace("\\", "/")
            parts = [part for part in normalized.split("/") if part]
            score = 3
            for idx, part in enumerate(parts):
                if part == "lib" and idx > 0 and parts[idx - 1] == package_name:
                    # Strongly prefer conventional monorepo layouts:
                    # packages/<pkg>/lib/... over arbitrary */<pkg>/lib/...
                    if idx > 1 and parts[idx - 2] in {"packages", "package", "pkgs"}:
                        score = 0
                    else:
                        score = 1
                    break
            if score == 3 and normalized.endswith("/" + lib_suffix):
                score = 2
            return (score, normalized)

        return min(candidates, key=candidate_key)
    
    def _absolute_path(self, file_path: str) -> Path:
        """Convert relative analyzer paths to absolute filesystem paths."""
        path = Path(file_path)
        if path.is_absolute():
            return path
        return (self.repo_root / path).resolve()

    def _as_repo_relative(self, path: Path) -> Optional[str]:
        """Return normalized repo-relative path if `path` is under repo_root."""
        try:
            return os.path.normpath(str(path.relative_to(self.repo_root)))
        except ValueError:
            return None

    def _indexed_relative_path(
        self,
        path_str: str,
        base_dir: Optional[Path] = None,
    ) -> Optional[str]:
        """
        Resolve path_str to an indexed repo-relative path if available.

        Accepts absolute paths, repo-relative paths, and optionally base-dir
        relative paths for local import normalization.
        """
        normalized = os.path.normpath(path_str)
        candidate = Path(normalized)

        if candidate.is_absolute():
            if normalized in self._indexed_file_path_set_abs:
                return self._as_repo_relative(candidate) or normalized
            rel = self._as_repo_relative(candidate)
            if rel and rel in self._indexed_file_path_set_rel:
                return rel
            return None

        if normalized in self._indexed_file_path_set_rel:
            return normalized

        repo_abs = os.path.normpath(str(self.repo_root / candidate))
        if repo_abs in self._indexed_file_path_set_abs:
            return normalized

        if base_dir is not None:
            local_abs = os.path.normpath(str(base_dir / candidate))
            if local_abs in self._indexed_file_path_set_abs:
                local_rel = self._as_repo_relative(Path(local_abs))
                return local_rel or local_abs

        return None
    
    def _normalize_internal_target(self, to_file: str, from_file: str) -> str:
        """
        Normalize plugin-resolved internal targets into repo-relative paths.
        
        Keeps unresolved symbolic module names unchanged.
        """
        target = Path(to_file)
        
        # Absolute path -> repo-relative if possible.
        if target.is_absolute():
            try:
                return str(target.relative_to(self.repo_root))
            except ValueError:
                return str(target)

        indexed = self._indexed_relative_path(to_file)
        if indexed:
            return indexed

        from_abs = self._absolute_path(from_file)
        indexed_local = self._indexed_relative_path(to_file, base_dir=from_abs.parent)
        if indexed_local:
            return indexed_local

        if not self._allow_unindexed_internal_targets:
            return to_file

        repo_candidate = Path(os.path.normpath(str(self.repo_root / target)))
        local_candidate = Path(os.path.normpath(str(from_abs.parent / target)))

        if repo_candidate.exists():
            try:
                return str(repo_candidate.resolve().relative_to(self.repo_root))
            except ValueError:
                pass
        if local_candidate.exists():
            try:
                return str(local_candidate.resolve().relative_to(self.repo_root))
            except ValueError:
                return str(local_candidate.resolve())
        
        return to_file
    
    def _normalize_plugin_edge(
        self,
        edge: Optional[DependencyEdge],
        from_file: str,
    ) -> Optional[DependencyEdge]:
        """Normalize plugin edge paths to keep graph nodes canonical."""
        if edge is None:
            return None
        
        edge.from_file = from_file
        
        if not edge.is_external and edge.to_file:
            edge.to_file = self._normalize_internal_target(edge.to_file, from_file)
        
        return edge
    
    def _context_keys_for_plugin(self, plugin: LanguagePlugin) -> Set[str]:
        """
        Build context aliases for a plugin.
        
        This allows plugins to access context via class-name keys ("swift")
        and language keys ("typescript") derived from handled extensions.
        """
        keys: Set[str] = set(plugin.context_keys)
        
        for ext in plugin.extensions:
            language = EXTENSION_TO_LANGUAGE.get(ext.lower())
            if language:
                keys.add(language)
        
        return {k for k in keys if k}
    
    def _load_plugin_context(self) -> Dict[str, Any]:
        """
        Parse known manifest files and store data in context buckets.
        
        Context structure:
            {
              "swift": {
                 "manifests": [{"path": ".../Package.swift", "data": {...}}],
                 ...first_manifest_fields
              },
              ...
            }
        """
        context: Dict[str, Any] = {}
        if not self._plugin_manager:
            return context
        
        manifest_names = set(self._plugin_manager.get_all_manifest_files())
        if not manifest_names:
            return context
        
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in _PLUGIN_CONTEXT_PRUNE_DIRS
            )
            filenames.sort()
            for filename in filenames:
                if filename not in manifest_names:
                    continue
                
                plugin = self._plugin_manager.get_plugin_for_manifest(filename)
                if not plugin:
                    continue
                
                manifest_path = Path(dirpath) / filename
                
                try:
                    parsed = plugin.parse_manifest(manifest_path)
                except Exception as e:
                    logger.warning(
                        f"Manifest parse failed for {manifest_path}",
                        exc_info=True,
                    )
                    continue
                
                if not parsed:
                    continue
                
                manifest_entry = {
                    "path": str(manifest_path),
                    "data": parsed,
                }
                
                for key in self._context_keys_for_plugin(plugin):
                    bucket = context.setdefault(key, {"manifests": []})
                    bucket["manifests"].append(manifest_entry)
                    
                    # Provide top-level shortcuts for common single-manifest repos.
                    for field, value in parsed.items():
                        bucket.setdefault(field, value)
        
        return context
    
    def _resolve_builtin_import(
        self,
        import_stmt: str,
        from_file: str,
        language: str,
    ) -> Optional[DependencyEdge]:
        """Resolve imports via the built-in language-specific resolvers."""
        if language == 'python':
            return self._resolve_python_import(import_stmt, from_file)
        if language in ('javascript', 'typescript'):
            return self._resolve_js_import(import_stmt, from_file)
        if language == 'go':
            return self._resolve_go_import(import_stmt, from_file)
        if language == 'rust':
            return self._resolve_rust_import(import_stmt, from_file)
        if language in ('c', 'cpp'):
            return self._resolve_c_import(import_stmt, from_file)
        return None

    def _resolve_plugin_import(
        self,
        import_stmt: str,
        from_file: str,
    ) -> Optional[DependencyEdge]:
        """Resolve imports via extension-based plugin resolvers."""
        if not self._plugin_manager:
            return None

        ext = Path(from_file).suffix.lower()
        plugin = self._plugin_manager.get_plugin_for_extension(ext)
        if not plugin:
            return None

        try:
            edge = plugin.resolve_import(
                import_stmt=import_stmt,
                from_file=self._absolute_path(from_file),
                config=self._plugin_config,
            )
            return self._normalize_plugin_edge(edge, from_file)
        except Exception as e:
            logger.warning(
                f"Plugin resolver {plugin.__class__.__name__} failed for {from_file}",
                exc_info=True,
            )
            return None

    def _resolve_custom_import(
        self,
        import_stmt: str,
        from_file: str,
        language: str,
    ) -> Optional[DependencyEdge]:
        """
        Resolve imports via the legacy language-keyed custom resolver registry.

        This remains a fallback path for existing integrations that still use
        `register_resolver()` and is evaluated after built-in/plugin resolvers.
        """
        if language in _CUSTOM_RESOLVERS:
            try:
                return _CUSTOM_RESOLVERS[language](import_stmt, from_file, self)
            except Exception as e:
                logger.warning(
                    f"Custom resolver for {language} failed",
                    exc_info=True,
                )
                return None
        return None

    def _resolve_import(
        self,
        import_stmt: str,
        from_file: str,
        language: str
    ) -> Optional[DependencyEdge]:
        """
        Resolve an import statement to a file path.

        Resolution order is explicit and configurable:
        - `builtin_first` (default): built-in -> plugin -> custom
        - `plugin_first`: plugin -> built-in -> custom
        """
        if self._plugin_resolver_precedence == "plugin_first":
            plugin_edge = self._resolve_plugin_import(import_stmt, from_file)
            if plugin_edge:
                return plugin_edge

            builtin_edge = self._resolve_builtin_import(import_stmt, from_file, language)
            if builtin_edge:
                return builtin_edge
        else:
            builtin_edge = self._resolve_builtin_import(import_stmt, from_file, language)
            if builtin_edge:
                return builtin_edge

            plugin_edge = self._resolve_plugin_import(import_stmt, from_file)
            if plugin_edge:
                return plugin_edge

        custom_edge = self._resolve_custom_import(import_stmt, from_file, language)
        if custom_edge:
            return custom_edge

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
                        if candidate.exists() or self._indexed_relative_path(str(candidate)):
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
                    if self._indexed_relative_path(rel_path):
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

        if language == 'python' and '.' not in module_name:
            stem_candidates = self._stem_index.get(module_name, [])
            if len(stem_candidates) == 1:
                return stem_candidates[0]
            if len(stem_candidates) > 1:
                logger.debug(
                    "Ambiguous bare Python import '%s' in %s, candidates=%s",
                    module_name,
                    from_file,
                    stem_candidates,
                )
                return None
        
        # Try adding .py extension
        if language == 'python':
            py_path = path_form + '.py'
            for filepath in self._indexed_file_paths:
                if filepath.endswith(py_path):
                    return filepath
            
            # Try __init__.py for packages
            init_path = os.path.join(path_form, '__init__.py')
            for filepath in self._indexed_file_paths:
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
