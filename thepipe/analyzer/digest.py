"""
Digest Generator

Generates token-efficient digests of code using:
- Haskell-style type signatures: `fn :: param:Type -> ReturnType`
- S-expression class structures: `(ClassName (methods ...))`
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from .types import ASTNode, FileAnalysis, CallGraphEntry


@dataclass
class Digest:
    """A compressed representation of code"""
    type: str  # "signature", "class_structure", "module_index"
    content: str
    original_lines: int
    digest_lines: int
    
    @property
    def compression_ratio(self) -> float:
        if self.original_lines == 0:
            return 0.0
        return 1 - (self.digest_lines / self.original_lines)


class DigestGenerator:
    """
    Generates token-efficient digests from AST nodes.
    
    Formats:
    - Functions: Haskell-style `name :: param1:Type -> param2:Type -> ReturnType`
    - Classes: S-expression `(ClassName (public-methods ...) (private-methods ...))`
    """
    
    def __init__(self, source_code: str, language: str = "python"):
        self.source = source_code
        self.source_bytes = source_code.encode("utf-8")
        self.language = language
        self.lines = source_code.split('\n')
        self._sexp_safe = re.compile(r"^[A-Za-z0-9_./:@+$-]+$")

    def _sexp_atom(self, value: str) -> str:
        if not value:
            return "unknown"
        text = value.strip()
        if not text:
            return "unknown"
        if self._sexp_safe.match(text):
            return text
        escaped = (
            text.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace('"', '\\"')
        )
        return f"\"{escaped}\""

    def _slice_source(self, start_byte: int, end_byte: int) -> str:
        return self.source_bytes[start_byte:end_byte].decode("utf-8", errors="ignore")
    
    def function_signature(self, node: ASTNode) -> Digest:
        """
        Generate Haskell-style function signature.
        
        Format: name :: param1:Type -> param2:Type -> ReturnType
        Example: authenticate :: token:str -> user:User -> bool
        """
        if self.language == "python":
            return self._python_function_signature(node)
        elif self.language in ("javascript", "typescript", "tsx"):
            return self._js_function_signature(node)
        else:
            return self._generic_function_signature(node)
    
    def _python_function_signature(self, node: ASTNode) -> Digest:
        """Extract Python function signature with type hints"""
        # Get the source text for this function
        func_text = self._slice_source(node.start_byte, node.end_byte)
        
        # Parse def line
        def_match = re.match(
            r'(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*([^:]+))?:',
            func_text,
            re.DOTALL
        )
        
        if not def_match:
            # Fallback to just name
            return Digest(
                type="signature",
                content=f"{node.name} :: ...",
                original_lines=node.end_line - node.start_line + 1,
                digest_lines=1
            )
        
        name = def_match.group(1)
        params_str = def_match.group(2).strip()
        return_type = def_match.group(3).strip() if def_match.group(3) else "None"
        
        # Parse parameters
        params = self._parse_python_params(params_str)
        
        # Build Haskell-style signature
        if params:
            param_chain = " -> ".join(f"{p['name']}:{p['type']}" for p in params)
            signature = f"{name} :: {param_chain} -> {return_type}"
        else:
            signature = f"{name} :: () -> {return_type}"
        
        return Digest(
            type="signature",
            content=signature,
            original_lines=node.end_line - node.start_line + 1,
            digest_lines=1
        )
    
    def _parse_python_params(self, params_str: str) -> List[Dict[str, str]]:
        """Parse Python function parameters with type hints"""
        if not params_str:
            return []
        
        params = []
        # Handle nested brackets by replacing them temporarily
        cleaned = re.sub(r'\[[^\]]*\]', lambda m: m.group().replace(',', ';'), params_str)
        
        for part in cleaned.split(','):
            part = part.strip().replace(';', ',')
            if not part or part == 'self' or part == 'cls':
                continue
            
            # Match param: Type = default
            match = re.match(r'(\*{0,2}\w+)\s*(?::\s*([^=]+))?\s*(?:=.*)?', part)
            if match:
                name = match.group(1)
                ptype = match.group(2).strip() if match.group(2) else "Any"
                params.append({"name": name, "type": ptype})
        
        return params
    
    def _js_function_signature(self, node: ASTNode) -> Digest:
        """Extract JS/TS function signature"""
        func_text = self._slice_source(node.start_byte, node.end_byte)
        
        # Try to parse function declaration
        match = re.match(
            r'(?:async\s+)?(?:function\s+)?(\w+)?\s*\(([^)]*)\)\s*(?::\s*(\w+))?',
            func_text
        )
        
        if not match:
            return Digest(
                type="signature",
                content=f"{node.name or 'anonymous'} :: ...",
                original_lines=node.end_line - node.start_line + 1,
                digest_lines=1
            )
        
        name = match.group(1) or node.name or "anonymous"
        params_str = match.group(2)
        return_type = match.group(3) or "void"
        
        # Parse TS params
        params = []
        for part in params_str.split(','):
            part = part.strip()
            if not part:
                continue
            match = re.match(r'(\w+)\s*(?::\s*(\w+))?', part)
            if match:
                params.append({
                    "name": match.group(1),
                    "type": match.group(2) or "any"
                })
        
        if params:
            param_chain = " -> ".join(f"{p['name']}:{p['type']}" for p in params)
            signature = f"{name} :: {param_chain} -> {return_type}"
        else:
            signature = f"{name} :: () -> {return_type}"
        
        return Digest(
            type="signature",
            content=signature,
            original_lines=node.end_line - node.start_line + 1,
            digest_lines=1
        )
    
    def _generic_function_signature(self, node: ASTNode) -> Digest:
        """Generic signature for unsupported languages"""
        return Digest(
            type="signature",
            content=f"{node.name or 'function'} :: ...",
            original_lines=node.end_line - node.start_line + 1,
            digest_lines=1
        )
    
    def class_structure(self, node: ASTNode, methods: List[ASTNode]) -> Digest:
        """
        Generate S-expression class structure.
        
        Format:
        (ClassName
          (public-methods method1 method2)
          (private-methods _method1 _method2))
        """
        class_name = self._sexp_atom(node.name or "UnknownClass")
        
        public_methods = []
        private_methods = []
        
        for method in methods:
            if method.name:
                if method.name.startswith('_') and not method.name.startswith('__'):
                    private_methods.append(method.name)
                else:
                    public_methods.append(method.name)
        
        # Sort method names for deterministic output
        public_methods.sort()
        private_methods.sort()

        # Build S-expression
        lines = [f"({class_name}"]
        
        if public_methods:
            rendered = " ".join(self._sexp_atom(name) for name in public_methods)
            lines.append(f"  (public-methods {rendered})")
        if private_methods:
            rendered = " ".join(self._sexp_atom(name) for name in private_methods)
            lines.append(f"  (private-methods {rendered})")
        
        lines.append(")")
        content = "\n".join(lines)
        
        return Digest(
            type="class_structure",
            content=content,
            original_lines=node.end_line - node.start_line + 1,
            digest_lines=len(lines)
        )
    
    def module_index(self, analysis: FileAnalysis) -> Digest:
        """
        Generate module-level index.
        
        Format:
        (module path/to/file
          (imports ...)
          (exports ...)
          (functions fn1 fn2 ...)
          (classes Cls1 Cls2 ...))
        """
        lines = [f"(module {analysis.path}"]
        
        # Imports (full list, deterministic order)
        if analysis.imports:
            normalized_imports = []
            for imp in analysis.imports:
                if not imp:
                    continue
                cleaned = " ".join(imp.strip().split())
                if cleaned:
                    normalized_imports.append(cleaned)
            unique_imports = sorted(set(normalized_imports))
            if unique_imports:
                lines.append("  (imports")
                for imp in unique_imports:
                    lines.append(f"    {self._sexp_atom(imp)}")
                lines.append("  )")
        
        # Functions (sorted for deterministic output)
        func_names = sorted([f.name for f in analysis.functions if f.name])
        if func_names:
            rendered = " ".join(self._sexp_atom(name) for name in func_names)
            lines.append(f"  (functions {rendered})")
        
        # Classes (sorted for deterministic output)
        class_names = sorted([c.name for c in analysis.classes if c.name])
        if class_names:
            rendered = " ".join(self._sexp_atom(name) for name in class_names)
            lines.append(f"  (classes {rendered})")
        
        lines.append(")")
        content = "\n".join(lines)
        
        return Digest(
            type="module_index",
            content=content,
            original_lines=analysis.line_count,
            digest_lines=len(lines)
        )

    def call_graph(
        self,
        call_graph: List[CallGraphEntry],
        max_entries: int = 20,
    ) -> Optional[Digest]:
        """Generate a compact per-file call graph section."""
        if not call_graph:
            return None

        def is_noisy_callee(name: str) -> bool:
            lowered = name.strip().lower()
            noisy_exact = {
                "print", "len", "str", "int", "float", "bool",
                "list", "dict", "set", "tuple", "enumerate", "range",
                "zip", "map", "filter", "sorted", "sum", "min", "max",
                "open", "isinstance", "getattr", "setattr", "hasattr",
            }
            if lowered in noisy_exact:
                return True

            noisy_prefixes = (
                "console.",
                "logger.",
                "logging.",
                "json.",
            )
            return lowered.startswith(noisy_prefixes)

        # De-duplicate calls and sort for deterministic output.
        unique_pairs = {(entry.caller, entry.callee) for entry in call_graph}
        primary_pairs = sorted(
            [pair for pair in unique_pairs if not is_noisy_callee(pair[1])]
        )
        noisy_pairs = sorted(
            [pair for pair in unique_pairs if is_noisy_callee(pair[1])]
        )

        filtered_pairs = primary_pairs
        if not filtered_pairs:
            # If everything is noisy, keep a minimal sample.
            filtered_pairs = noisy_pairs

        selected_pairs = filtered_pairs[:max_entries]

        lines = ["(calls"]
        for caller_name, callee_name in selected_pairs:
            caller = self._sexp_atom(caller_name)
            callee = self._sexp_atom(callee_name)
            lines.append(f"  ({caller} -> {callee})")
        if len(filtered_pairs) > max_entries:
            lines.append(f"  (truncated {len(filtered_pairs) - max_entries} more)")
        lines.append(")")

        return Digest(
            type="call_graph",
            content="\n".join(lines),
            original_lines=len(call_graph),
            digest_lines=len(lines),
        )


def generate_file_digest(
    source_code: str,
    analysis: FileAnalysis,
    include_signatures: bool = True,
    include_classes: bool = True,
) -> str:
    """
    Generate a complete digest for a file.
    
    Returns a formatted string with module index, class structures,
    and function signatures.
    """
    generator = DigestGenerator(source_code, analysis.language)
    parts = []
    
    # TODO: Add a separate global graph artifact (NOT in per-file digests).
    # Goals:
    # - Persist cross-file edges (imports/calls/dataflow/semantic).
    # - Support layered views (e.g., API/Data/UI) and guided tours.
    # - Enable incremental updates and stable IDs for diffing.
    #
    # Proposed format (graph.json):
    # {
    #   "version": "1.0",
    #   "project": {"name": "...", "git_commit": "...", "languages": [...]},
    #   "nodes": [{"id":"file:src/a.ts","type":"file","name":"a.ts","path":"src/a.ts"}, ...],
    #   "edges": [{"src":"file:src/a.ts","dst":"file:src/b.ts","type":"imports","weight":0.7}, ...],
    #   "layers": [{"id":"layer:api","name":"API","node_ids":[...]}],
    #   "tours": [{"id":"tour:onboarding","steps":[{"title":"Entry","node_ids":[...]}]}]
    # }
    #
    # Pseudocode:
    # result = analyze_repo(...)
    # nodes = build_nodes_from_files(result.files) + build_nodes_from_symbols(result)
    # edges = build_edges_from_imports(result.dependency_graph)
    # edges += build_edges_from_calls(result.files[*].call_graph)
    # edges += build_edges_from_semantic_tags(result.semantic_tags)
    # layers = infer_layers(nodes, edges, file_patterns, llm_optional=False)
    # tours = build_tours(nodes, edges, layers)
    # write_json("outputs/graph.json", {version, project, nodes, edges, layers, tours})

    # Module index
    module_digest = generator.module_index(analysis)
    parts.append(module_digest.content)

    # Call graph (per-file)
    call_digest = generator.call_graph(analysis.call_graph)
    if call_digest:
        parts.append(call_digest.content)
    
    # Class structures (sorted for deterministic output)
    if include_classes:
        for cls in sorted(analysis.classes, key=lambda c: (c.name or "", c.start_line, c.end_line)):
            # Find methods within this class (simplified - just by line range)
            class_methods = [
                f for f in analysis.functions
                if cls.start_line <= f.start_line <= cls.end_line
            ]
            class_digest = generator.class_structure(cls, class_methods)
            parts.append(class_digest.content)
    
    # Function signatures (excluding class methods already shown)
    if include_signatures:
        class_ranges = [(c.start_line, c.end_line) for c in analysis.classes]
        for func in sorted(analysis.functions, key=lambda f: (f.name or "", f.start_line, f.end_line)):
            # Skip if inside a class
            in_class = any(
                start <= func.start_line <= end
                for start, end in class_ranges
            )
            if not in_class:
                sig = generator.function_signature(func)
                parts.append(sig.content)
    
    return "\n\n".join(parts)
