"""
AST Extractor using tree-sitter

Language-agnostic AST extraction with support for 165+ languages via tree-sitter-language-pack.
"""

from typing import Dict, List, Optional, Tuple
import logging

from .types import ASTNode, FileAnalysis

logger = logging.getLogger(__name__)

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
}

# Query patterns for extracting imports (language-specific)
IMPORT_QUERIES = {
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
}

# Query patterns for class definitions
CLASS_QUERIES = {
    'python': '(class_definition name: (identifier) @name) @class',
    'javascript': '(class_declaration name: (identifier) @name) @class',
    'typescript': '(class_declaration name: (identifier) @name) @class',
    'go': '(type_declaration (type_spec name: (type_identifier) @name)) @class',
    'rust': '(struct_item name: (type_identifier) @name) @class',
    'java': '(class_declaration name: (identifier) @name) @class',
}


class ASTExtractor:
    """Language-agnostic AST extraction via tree-sitter"""
    
    def __init__(self):
        self._parsers: Dict[str, any] = {}
        self._languages: Dict[str, any] = {}
    
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
                logger.warning(f"Failed to load parser for {language}: {e}")
                return None, None
        
        return self._parsers.get(language), self._languages.get(language)
    
    def detect_language(self, filepath: str) -> Optional[str]:
        """Detect language from file extension"""
        from pathlib import Path
        ext = Path(filepath).suffix.lower()
        return EXTENSION_TO_LANGUAGE.get(ext)
    
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
        
        # Extract imports
        analysis.imports = self._extract_imports(tree, source_code, language, lang)
        
        # Extract functions
        analysis.functions = self._extract_functions(tree, source_code, language, lang)
        
        # Extract classes
        analysis.classes = self._extract_classes(tree, source_code, language, lang)
        
        # Detect cross-language bridges
        analysis.is_cross_language = self._detect_cross_language(source_code, filepath)
        
        return analysis
    
    def _extract_imports(
        self, tree, source: str, language: str, lang
    ) -> List[str]:
        """Extract import statements from AST"""
        imports = []
        
        query_str = IMPORT_QUERIES.get(language)
        if not query_str or lang is None:
            # Fallback: walk tree manually for common patterns
            return self._extract_imports_fallback(tree, source, language)
        
        try:
            query = lang.query(query_str)
            captures = query.captures(tree.root_node)
            
            for node, name in captures:
                if name == 'import':
                    import_text = source[node.start_byte:node.end_byte]
                    imports.append(import_text.strip())
        except Exception as e:
            logger.debug(f"Query failed for {language}, using fallback: {e}")
            return self._extract_imports_fallback(tree, source, language)
        
        return imports
    
    def _extract_imports_fallback(self, tree, source: str, language: str) -> List[str]:
        """Fallback import extraction by walking the tree"""
        imports = []
        
        def walk(node):
            node_type = node.type
            
            # Python imports
            if node_type in ('import_statement', 'import_from_statement'):
                imports.append(source[node.start_byte:node.end_byte].strip())
            # JS/TS imports
            elif node_type == 'import_statement':
                imports.append(source[node.start_byte:node.end_byte].strip())
            # C/C++ includes
            elif node_type == 'preproc_include':
                imports.append(source[node.start_byte:node.end_byte].strip())
            # Go imports
            elif node_type in ('import_declaration', 'import_spec'):
                imports.append(source[node.start_byte:node.end_byte].strip())
            # Rust use
            elif node_type == 'use_declaration':
                imports.append(source[node.start_byte:node.end_byte].strip())
                
            for child in node.children:
                walk(child)
        
        walk(tree.root_node)
        return imports
    
    def _extract_functions(
        self, tree, source: str, language: str, lang
    ) -> List[ASTNode]:
        """Extract function definitions from AST"""
        functions = []
        
        def walk(node):
            is_func = False
            name = None
            
            if language == 'python' and node.type == 'function_definition':
                is_func = True
                for child in node.children:
                    if child.type == 'identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            elif language in ('javascript', 'typescript'):
                if node.type in ('function_declaration', 'method_definition'):
                    is_func = True
                    for child in node.children:
                        if child.type in ('identifier', 'property_identifier'):
                            name = source[child.start_byte:child.end_byte]
                            break
            elif language == 'go' and node.type == 'function_declaration':
                is_func = True
                for child in node.children:
                    if child.type == 'identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            elif language == 'rust' and node.type == 'function_item':
                is_func = True
                for child in node.children:
                    if child.type == 'identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            elif language in ('c', 'cpp') and node.type == 'function_definition':
                is_func = True
                # Navigate to function name
                for child in node.children:
                    if child.type == 'function_declarator':
                        for subchild in child.children:
                            if subchild.type == 'identifier':
                                name = source[subchild.start_byte:subchild.end_byte]
                                break
            
            if is_func:
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
        self, tree, source: str, language: str, lang
    ) -> List[ASTNode]:
        """Extract class definitions from AST"""
        classes = []
        
        def walk(node):
            is_class = False
            name = None
            
            if language == 'python' and node.type == 'class_definition':
                is_class = True
                for child in node.children:
                    if child.type == 'identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            elif language in ('javascript', 'typescript') and node.type == 'class_declaration':
                is_class = True
                for child in node.children:
                    if child.type == 'identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            elif language == 'rust' and node.type == 'struct_item':
                is_class = True
                for child in node.children:
                    if child.type == 'type_identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            elif language == 'go' and node.type == 'type_declaration':
                is_class = True
                for child in node.children:
                    if child.type == 'type_spec':
                        for subchild in child.children:
                            if subchild.type == 'type_identifier':
                                name = source[subchild.start_byte:subchild.end_byte]
                                break
            elif language == 'java' and node.type == 'class_declaration':
                is_class = True
                for child in node.children:
                    if child.type == 'identifier':
                        name = source[child.start_byte:child.end_byte]
                        break
            
            if is_class:
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
