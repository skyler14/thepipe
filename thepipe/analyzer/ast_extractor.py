"""
AST Extractor using tree-sitter

Language-agnostic AST extraction with support for 165+ languages via tree-sitter-language-pack.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

from .types import ASTNode, FileAnalysis
from .plugins import register_builtin_plugins
from .plugins.base import LanguagePlugin

logger = logging.getLogger(__name__)

NodeKey = Tuple[int, int, int, int, int, int, str, bool]

try:
    from tree_sitter import QueryCursor
except Exception:  # pragma: no cover - optional dependency shape
    QueryCursor = None

# Try to import tree-sitter-language-pack, fall back gracefully
try:
    from tree_sitter_language_pack import get_parser, get_language
    TREE_SITTER_AVAILABLE = True
except ImportError:
    try:
        # Fallback to older tree-sitter-languages
        from tree_sitter_languages import get_parser, get_language
        TREE_SITTER_AVAILABLE = True
    except ImportError:
        TREE_SITTER_AVAILABLE = False
        logger.warning(
            "tree-sitter not available. Install with: "
            "pip install tree-sitter-language-pack"
        )


# Language detection from file extension
EXTENSION_TO_LANGUAGE = {
    '.py': 'python',
    '.pyw': 'python',
    '.pyx': 'python',  # Cython, parse as Python
    '.pxd': 'python',
    '.pxi': 'python',
    '.js': 'javascript',
    '.mjs': 'javascript',
    '.cjs': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.go': 'go',
    '.rs': 'rust',
    '.c': 'c',
    '.h': 'c',
    '.cpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.hpp': 'cpp',
    '.hxx': 'cpp',
    '.java': 'java',
    '.kt': 'kotlin',
    '.kts': 'kotlin',
    '.swift': 'swift',
    '.rb': 'ruby',
    '.php': 'php',
    '.cs': 'c_sharp',
    '.scala': 'scala',
    '.lua': 'lua',
    '.r': 'r',
    '.R': 'r',
    '.jl': 'julia',
    '.zig': 'zig',
    '.elm': 'elm',
    '.ex': 'elixir',
    '.exs': 'elixir',
    '.erl': 'erlang',
    '.hrl': 'erlang',
    '.hs': 'haskell',
    '.ml': 'ocaml',
    '.mli': 'ocaml',
    '.clj': 'clojure',
    '.cljs': 'clojure',
    '.cljc': 'clojure',
    '.dart': 'dart',
    '.v': 'v',
    '.nim': 'nim',
    '.asm': 'asm',
    '.s': 'asm',
    '.S': 'asm',
    '.html': 'html',
    '.htm': 'html',
    '.m': 'objective_c',
    '.pl': 'perl',
    '.pm': 'perl',
}

# Query patterns for extracting imports (language-specific)
IMPORT_QUERIES = {
    'dart': """
        (import_directive) @import
        (export_directive) @import
    """,
    'python': """
        (import_statement) @import
        (import_from_statement) @import
    """,
    'javascript': """
        (import_statement) @import
        (call_expression
            function: (identifier) @func (#eq? @func "require")
        ) @import
    """,
    'typescript': """
        (import_statement) @import
        (call_expression
            function: (identifier) @func (#eq? @func "require")
        ) @import
    """,
    'go': """
        (import_declaration) @import
        (import_spec) @import  
    """,
    'rust': """
        (use_declaration) @import
        (mod_item) @import
    """,
    'c': """
        (preproc_include) @import
    """,
    'cpp': """
        (preproc_include) @import
    """,
    'swift': """
        (import_declaration) @import
    """,
}

# Query patterns for function definitions
FUNCTION_QUERIES = {
    'python': '(function_definition name: (identifier) @name) @func',
    'javascript': """
        (function_declaration name: (identifier) @name) @func
        (arrow_function) @func
        (method_definition name: (property_identifier) @name) @func
    """,
    'typescript': """
        (function_declaration name: (identifier) @name) @func
        (arrow_function) @func
        (method_definition name: (property_identifier) @name) @func
    """,
    'go': '(function_declaration name: (identifier) @name) @func',
    'rust': '(function_item name: (identifier) @name) @func',
    'c': '(function_definition declarator: (function_declarator declarator: (identifier) @name)) @func',
    'cpp': '(function_definition declarator: (function_declarator declarator: (identifier) @name)) @func',
    'swift': """
        (function_declaration name: (simple_identifier) @name) @func
        (init_declaration) @func
        (deinit_declaration) @func
    """,
    'dart': """
        (function_signature name: (identifier) @name) @func
        (method_declaration name: (identifier) @name) @func
        (constructor_declaration name: (identifier) @name) @func
    """,
}

# Query patterns for class definitions
CLASS_QUERIES = {
    'python': '(class_definition name: (identifier) @name) @class',
    'javascript': '(class_declaration name: (identifier) @name) @class',
    'typescript': '(class_declaration name: (identifier) @name) @class',
    'go': '(type_declaration (type_spec name: (type_identifier) @name)) @class',
    'rust': '(struct_item name: (type_identifier) @name) @class',
    'java': '(class_declaration name: (identifier) @name) @class',
    'swift': """
        (class_declaration name: (type_identifier) @name) @class
        (struct_declaration name: (type_identifier) @name) @class
        (enum_declaration name: (type_identifier) @name) @class
        (protocol_declaration name: (type_identifier) @name) @class
        (actor_declaration name: (type_identifier) @name) @class
        (extension_declaration type: (type_identifier) @name) @class
    """,
    'dart': """
        (class_definition name: (identifier) @name) @class
        (mixin_declaration name: (identifier) @name) @class
        (extension_declaration name: (identifier) @name) @class
        (enum_declaration name: (identifier) @name) @class
    """,
}


# ============================================================================
# UNIVERSAL PATTERN-BASED DETECTION
# ============================================================================

def _is_import_node(node) -> bool:
    """Universal import detection across all languages.
    
    Detects imports by checking if node type contains import-related keywords.
    Works for: Python (import_statement), Dart (import_directive), 
    Go (import_declaration), Rust (use_declaration), etc.
    """
    node_type = node.type.lower()
    import_keywords = ['import', 'use', 'require', 'include', 'extern', 'module']
    return any(kw in node_type for kw in import_keywords)


def _is_function_node(node) -> bool:
    """Universal function detection across all languages.
    
    Detects functions by checking if node type contains function-related keywords.
    Works for: Python (function_definition), Dart (function_declaration),
    Swift (function_declaration), Kotlin (function_declaration), etc.
    """
    node_type = node.type.lower()
    function_keywords = ['function', 'method', 'procedure', 'func', 'def']
    return any(kw in node_type for kw in function_keywords)


def _is_class_node(node) -> bool:
    """Universal class detection across all languages.
    
    Detects classes/structs by checking if node type contains class-related keywords.
    Works for: Python (class_definition), Dart (class_declaration),
    Swift (class_declaration, struct_declaration), Go (type_declaration), etc.
    """
    node_type = node.type.lower()
    class_keywords = ['class', 'struct', 'interface', 'trait', 'enum', 'object']
    return any(kw in node_type for kw in class_keywords)


def _extract_identifier_from_node(node, source: str) -> Optional[str]:
    """Extract identifier/name from a node by looking for identifier children.
    
    Universal helper that searches for common identifier node types.
    """
    identifier_types = {'identifier', 'type_identifier', 'property_identifier'}
    
    # Direct identifier child
    for child in node.children:
        if child.type in identifier_types:
            return source[child.start_byte:child.end_byte]
    
    # Nested identifier (e.g., in type_spec or declarators)
    for child in node.children:
        for subchild in child.children:
            if subchild.type in identifier_types:
                return source[subchild.start_byte:subchild.end_byte]
    
    return None


class ASTExtractor:
    """Language-agnostic AST extraction via tree-sitter"""
    
    def __init__(self):
        self._parsers: Dict[str, any] = {}
        self._languages: Dict[str, any] = {}
        try:
            # Idempotent singleton registration; safe even if DependencyMapper does this too.
            self._plugin_manager = register_builtin_plugins()
        except Exception as e:
            logger.warning("Plugin initialization failed in ASTExtractor", exc_info=True)
            self._plugin_manager = None
    
    def _get_parser(self, language: str):
        """Lazy-load parser for a language"""
        if not TREE_SITTER_AVAILABLE:
            raise ImportError(
                "tree-sitter not available. Install: pip install tree-sitter-language-pack"
            )
        
        if language not in self._parsers:
            try:
                self._parsers[language] = get_parser(language)
                self._languages[language] = get_language(language)
            except Exception as e:
                logger.warning(f"Failed to load parser for {language}", exc_info=True)
                return None, None
        
        return self._parsers.get(language), self._languages.get(language)
    
    def detect_language(self, filepath: str) -> Optional[str]:
        """Detect language from file extension"""
        ext = Path(filepath).suffix.lower()
        return EXTENSION_TO_LANGUAGE.get(ext)
    
    def _get_plugin_for_file(self, filepath: str) -> Optional[LanguagePlugin]:
        """Get a registered plugin for a file path based on extension."""
        if not filepath or not self._plugin_manager:
            return None
        
        ext = Path(filepath).suffix.lower()
        if not ext:
            return None
        
        return self._plugin_manager.get_plugin_for_extension(ext)
    
    def extract(self, source_code: str, language: str, filepath: str = "") -> Optional[FileAnalysis]:
        """
        Extract AST metadata from source code.
        
        Args:
            source_code: The source code to parse
            language: Language identifier (e.g., 'python', 'javascript')
            filepath: Optional filepath for context
            
        Returns:
            FileAnalysis with imports, functions, classes extracted
        """
        parser, lang = self._get_parser(language)
        if parser is None:
            return None
        
        try:
            tree = parser.parse(source_code.encode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to parse {filepath}: {e}")
            return None
        
        analysis = FileAnalysis(
            path=filepath,
            language=language,
            size_bytes=len(source_code.encode('utf-8')),
            line_count=source_code.count('\n') + 1,
        )
        
        plugin = self._get_plugin_for_file(filepath)
        
        # Extract imports
        analysis.imports = self._extract_imports(
            tree, source_code, language, lang, plugin=plugin
        )
        
        # Extract functions
        analysis.functions = self._extract_functions(
            tree, source_code, language, lang, plugin=plugin
        )
        
        # Extract classes
        analysis.classes = self._extract_classes(
            tree, source_code, language, lang, plugin=plugin
        )
        
        # Detect cross-language bridges
        analysis.is_cross_language = self._detect_cross_language(source_code, filepath)
        
        return analysis
    
    def _extract_imports(
        self,
        tree,
        source: str,
        language: str,
        lang,
        plugin: Optional[LanguagePlugin] = None,
    ) -> List[str]:
        """Extract import statements from AST"""
        imports = []
        
        query_str = ""
        if plugin and plugin.import_queries.strip():
            query_str = plugin.import_queries
        else:
            query_str = IMPORT_QUERIES.get(language, "")
        
        if not query_str or lang is None:
            # Fallback: walk tree manually for common patterns
            return self._extract_imports_fallback(tree, source, language)
        
        try:
            query = lang.query(query_str)
            captures = self._query_captures(query, tree.root_node)
            
            for node, name in captures:
                if name == 'import':
                    import_text = source[node.start_byte:node.end_byte]
                    imports.append(import_text.strip())
        except Exception as e:
            logger.warning(
                f"Query failed for {language} imports; using fallback",
                exc_info=True,
            )
            return self._extract_imports_fallback(tree, source, language)
        
        return imports
    
    def _extract_imports_fallback(self, tree, source: str, language: str) -> List[str]:
        """Universal fallback import extraction using pattern matching.
        
        Works for all 165 tree-sitter languages by detecting import-related node types.
        """
        imports = []
        
        def walk(node):
            if _is_import_node(node):
                imports.append(source[node.start_byte:node.end_byte].strip())
            
            for child in node.children:
                walk(child)
        
        walk(tree.root_node)
        return imports

    @staticmethod
    def _node_key(node) -> NodeKey:
        """
        Deterministic parse-local node key without relying on tree-sitter internals.

        We include both byte offsets and point coordinates to reduce the chance of
        accidental collisions in parser-recovery trees while keeping the key stable
        across repeated wrapper objects for the same underlying node.
        """
        start_row, start_col = node.start_point
        end_row, end_col = node.end_point
        return (
            node.start_byte,
            node.end_byte,
            start_row,
            start_col,
            end_row,
            end_col,
            node.type,
            getattr(node, "is_named", True),
        )

    @staticmethod
    def _build_name_capture_maps(
        captures,
        source: str,
    ) -> Tuple[Dict[NodeKey, str], Dict[NodeKey, str]]:
        """
        Index @name captures by parent/grandparent node key for O(1) lookup.

        Some grammars attach the name node directly to the function/class node,
        while others nest it one level deeper (e.g., C declarators).
        """
        direct_meta: Dict[NodeKey, Tuple[int, int, str]] = {}
        nested_meta: Dict[NodeKey, Tuple[int, int, str]] = {}

        for node, capture_name in captures:
            if capture_name != 'name':
                continue

            name_text = source[node.start_byte:node.end_byte]
            # Prefer identifier-like captures over larger declarator spans:
            # shorter capture span usually corresponds to the actual symbol token
            # (e.g., `foo`) rather than a wrapped declarator or qualified path.
            # Known limitation: this may favor short C++ member names over fully
            # qualified captures (e.g., `doSomething` vs `Ns::Type::doSomething`).
            rank = (node.end_byte - node.start_byte, node.start_byte)
            parent = getattr(node, "parent", None)
            if parent is not None:
                parent_key = ASTExtractor._node_key(parent)
                existing = direct_meta.get(parent_key)
                if existing is None or rank < existing[:2]:
                    direct_meta[parent_key] = (rank[0], rank[1], name_text)

            grandparent = getattr(parent, "parent", None) if parent is not None else None
            if grandparent is not None:
                grandparent_key = ASTExtractor._node_key(grandparent)
                existing = nested_meta.get(grandparent_key)
                if existing is None or rank < existing[:2]:
                    nested_meta[grandparent_key] = (rank[0], rank[1], name_text)

        direct_by_parent = {key: value[2] for key, value in direct_meta.items()}
        nested_by_grandparent = {key: value[2] for key, value in nested_meta.items()}
        return direct_by_parent, nested_by_grandparent

    @staticmethod
    def _normalize_capture_dict(capture_dict) -> List[Tuple[object, str]]:
        """Normalize capture dicts into [(node, capture_name)] tuples."""
        captures: List[Tuple[object, str]] = []
        for capture_name, nodes in capture_dict.items():
            for item in nodes:
                # Some APIs return Node objects directly; others wrap in tuples.
                node = item[0] if isinstance(item, tuple) and item else item
                if hasattr(node, "start_byte"):
                    captures.append((node, capture_name))
        return captures

    @staticmethod
    def _query_captures(query, root_node) -> List[Tuple[object, str]]:
        """
        Version-tolerant query capture adapter.

        Supports:
        - legacy Query.captures(root_node) -> [(node, "capture"), ...]
        - QueryCursor.captures(root_node) -> {"capture": [node, ...], ...}
        - QueryCursor.matches(root_node)  -> [(pattern_idx, {"capture": [node]})]
        """
        raw_captures = None

        if hasattr(query, "captures"):
            raw_captures = query.captures(root_node)
        elif QueryCursor is not None:
            cursor = QueryCursor(query)
            if hasattr(cursor, "captures"):
                raw_captures = cursor.captures(root_node)
            elif hasattr(cursor, "matches"):
                matches = cursor.matches(root_node)
                captures: List[Tuple[object, str]] = []
                for match in matches:
                    if isinstance(match, tuple) and len(match) == 2 and isinstance(match[1], dict):
                        captures.extend(ASTExtractor._normalize_capture_dict(match[1]))
                captures.sort(key=lambda c: (c[0].start_byte, c[0].end_byte, c[1]))
                return captures
        else:
            raise AttributeError("No compatible tree-sitter query capture API found")

        captures: List[Tuple[object, str]] = []
        if isinstance(raw_captures, dict):
            captures = ASTExtractor._normalize_capture_dict(raw_captures)
        else:
            # Legacy APIs usually return list[(node, capture_name)].
            for item in raw_captures or []:
                if (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and hasattr(item[0], "start_byte")
                    and isinstance(item[1], str)
                ):
                    captures.append((item[0], item[1]))
                elif (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[1], dict)
                ):
                    captures.extend(ASTExtractor._normalize_capture_dict(item[1]))

        captures.sort(key=lambda c: (c[0].start_byte, c[0].end_byte, c[1]))
        return captures

    def _extract_functions(
        self,
        tree,
        source: str,
        language: str,
        lang,
        plugin: Optional[LanguagePlugin] = None,
    ) -> List[ASTNode]:
        """Universal function extraction with query priority."""
        functions = []
        
        # Try specific query first
        query_str = ""
        if plugin and plugin.function_queries.strip():
            query_str = plugin.function_queries
        else:
            query_str = FUNCTION_QUERIES.get(language, "")
        
        if query_str and lang:
            try:
                query = lang.query(query_str)
                captures = self._query_captures(query, tree.root_node)
                direct_name_map, nested_name_map = self._build_name_capture_maps(
                    captures, source
                )
                
                # Deduplicate by node ID to handle multiple captures per node
                processed_nodes = set()
                
                for node, capture_name in captures:
                    node_key = self._node_key(node)
                    if capture_name == 'func' and node_key not in processed_nodes:
                        name = direct_name_map.get(node_key)
                        if not name:
                            name = nested_name_map.get(node_key)
                        if not name:
                            name = _extract_identifier_from_node(node, source)
                        
                        functions.append(ASTNode(
                            type='function',
                            name=name,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            start_byte=node.start_byte,
                            end_byte=node.end_byte,
                        ))
                        processed_nodes.add(node_key)
                
                return functions
            except Exception as e:
                logger.warning(
                    f"Query failed for {language} functions; using fallback",
                    exc_info=True,
                )
        
        # Original fallback logic
        def walk(node):
            if _is_function_node(node):
                name = _extract_identifier_from_node(node, source)
                if name:
                    functions.append(ASTNode(
                        type='function',
                        name=name,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                    ))
            
            for child in node.children:
                walk(child)
        
        walk(tree.root_node)
        return functions

    def _extract_classes(
        self,
        tree,
        source: str,
        language: str,
        lang,
        plugin: Optional[LanguagePlugin] = None,
    ) -> List[ASTNode]:
        """Universal class extraction with query priority."""
        classes = []
        
        # Try specific query first
        query_str = ""
        if plugin and plugin.class_queries.strip():
            query_str = plugin.class_queries
        else:
            query_str = CLASS_QUERIES.get(language, "")
        
        if query_str and lang:
            try:
                query = lang.query(query_str)
                captures = self._query_captures(query, tree.root_node)
                direct_name_map, nested_name_map = self._build_name_capture_maps(
                    captures, source
                )
                
                processed_nodes = set()
                
                for node, capture_name in captures:
                    node_key = self._node_key(node)
                    if capture_name == 'class' and node_key not in processed_nodes:
                        name = direct_name_map.get(node_key)
                        if not name:
                            name = nested_name_map.get(node_key)
                        if not name:
                            name = _extract_identifier_from_node(node, source)
                            
                        classes.append(ASTNode(
                            type='class',
                            name=name,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            start_byte=node.start_byte,
                            end_byte=node.end_byte,
                        ))
                        processed_nodes.add(node_key)
                return classes
            except Exception as e:
                logger.warning(
                    f"Query failed for {language} classes; using fallback",
                    exc_info=True,
                )
        
        # Original fallback logic
        def walk(node):
            if _is_class_node(node):
                name = _extract_identifier_from_node(node, source)
                if name:  # Filter unnamed classes
                    classes.append(ASTNode(
                        type='class',
                        name=name,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                    ))
            
            for child in node.children:
                walk(child)
        
        walk(tree.root_node)
        return classes
    
    def _detect_cross_language(self, source: str, filepath: str) -> bool:
        """Detect if file uses cross-language bridges"""
        import re
        
        patterns = [
            r'cimport|cdef|cpdef',  # Cython
            r'from ctypes import|ctypes\.CDLL',  # ctypes
            r'cffi\.FFI',  # CFFI
            r'%module|%{|%}',  # SWIG
            r'System\.loadLibrary|JNI',  # JNI
            r'extern "C"',  # C/C++ extern
            r'pybind11|PYBIND11',  # pybind11
        ]
        
        for pattern in patterns:
            if re.search(pattern, source):
                return True
        
        # Check file extension for known cross-language types
        if filepath.endswith(('.pyx', '.pxd', '.pxi')):
            return True
        
        return False


# Singleton instance for convenience
_extractor: Optional[ASTExtractor] = None

def get_extractor() -> ASTExtractor:
    """Get the singleton AST extractor instance"""
    global _extractor
    if _extractor is None:
        _extractor = ASTExtractor()
    return _extractor


def extract_file(filepath: str) -> Optional[FileAnalysis]:
    """
    Convenience function to extract AST from a file.
    
    Args:
        filepath: Path to the source file
        
    Returns:
        FileAnalysis or None if parsing failed
    """
    extractor = get_extractor()
    
    language = extractor.detect_language(filepath)
    if not language:
        logger.debug(f"Unknown language for {filepath}")
        return None
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            source = f.read()
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return None
    
    return extractor.extract(source, language, filepath)
