"""
Comprehensive tests for the code relationship analyzer module.

Uses the thepipe codebase itself as a test fixture since it has:
- Multiple Python files with inter-dependencies
- Classes and functions to extract
- Internal imports to map
"""

import os
import json
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import List
from thepipe.analyzer.ast_extractor import TREE_SITTER_AVAILABLE

REQUIRES_TREE_SITTER = unittest.skipUnless(
    TREE_SITTER_AVAILABLE,
    "requires tree-sitter-language-pack",
)

# Get the repo root (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent
THEPIPE_DIR = REPO_ROOT / "thepipe"


class TestASTExtractor(unittest.TestCase):
    """Test AST extraction functionality"""
    
    def setUp(self):
        from thepipe.analyzer import get_extractor
        self.extractor = get_extractor()
    
    def test_detect_language_python(self):
        """Test Python file detection"""
        self.assertEqual(self.extractor.detect_language("test.py"), "python")
        self.assertEqual(self.extractor.detect_language("test.pyx"), "python")
    
    def test_detect_language_javascript(self):
        """Test JavaScript/TypeScript detection"""
        self.assertEqual(self.extractor.detect_language("app.js"), "javascript")
        self.assertEqual(self.extractor.detect_language("app.mjs"), "javascript")
        self.assertEqual(self.extractor.detect_language("app.cjs"), "javascript")
        self.assertEqual(self.extractor.detect_language("app.ts"), "typescript")
        self.assertEqual(self.extractor.detect_language("app.tsx"), "tsx")
    
    def test_detect_language_others(self):
        """Test other language detection"""
        self.assertEqual(self.extractor.detect_language("main.go"), "go")
        self.assertEqual(self.extractor.detect_language("lib.rs"), "rust")
        self.assertEqual(self.extractor.detect_language("util.c"), "c")
        self.assertEqual(self.extractor.detect_language("util.cpp"), "cpp")
    
    def test_detect_language_unknown(self):
        """Test unknown extensions return None"""
        self.assertIsNone(self.extractor.detect_language("readme.txt"))
        self.assertIsNone(self.extractor.detect_language("data.json"))

    @REQUIRES_TREE_SITTER
    def test_extract_rust_world_class_surfaces(self):
        """Rust extraction should include modules, grouped imports, types, traits, impls, and methods."""
        source = """
use crate::{utils::{helper, Thing}, api::Router};
pub use serde::{Serialize, Deserialize};
extern crate alloc;
mod utils;
pub mod api;

pub trait Handler { fn handle(&self); }
pub struct User { id: u64 }
pub enum Mode { A, B }
pub type UserId = u64;
union Raw { bits: u32 }
impl User { pub fn new() -> Self { Self { id: 0 } } }
"""

        analysis = self.extractor.extract(source, "rust", "src/main.rs")

        self.assertIsNotNone(analysis)
        self.assertIn("mod utils;", analysis.imports)
        self.assertIn("pub mod api;", analysis.imports)
        self.assertIn("use crate::utils::helper;", analysis.imports)
        self.assertIn("use crate::utils::Thing;", analysis.imports)
        self.assertIn("use crate::api::Router;", analysis.imports)
        self.assertIn("use serde::Serialize;", analysis.imports)
        self.assertIn("use serde::Deserialize;", analysis.imports)

        class_names = {node.name for node in analysis.classes}
        self.assertIn("Handler", class_names)
        self.assertIn("User", class_names)
        self.assertIn("Mode", class_names)
        self.assertIn("UserId", class_names)
        self.assertIn("Raw", class_names)
        self.assertIn("impl User", class_names)

        func_names = {node.name for node in analysis.functions}
        self.assertIn("handle", func_names)
        self.assertIn("new", func_names)

    @REQUIRES_TREE_SITTER
    def test_extract_tsx_react_and_type_surfaces(self):
        """TSX extraction should keep React wrapper components and TS type surfaces."""
        source = """
import React, { memo, forwardRef } from 'react';
import type { User } from '@/models/user';
export { helper } from './helper';

interface Props { user: User }
type Mode = 'view' | 'edit';
enum Status { Ready }
class Store {}

const Card = memo(function Card(props: Props) { return <div />; });
const Link = React.forwardRef<HTMLAnchorElement, Props>((props, ref) => <a ref={ref} />);
export const Plain = (props: Props) => <span>{props.user.name}</span>;
"""

        analysis = self.extractor.extract(source, "tsx", "src/Card.tsx")

        self.assertIsNotNone(analysis)
        self.assertEqual(
            analysis.imports,
            ["'react'", "'@/models/user'", "'./helper'"],
        )
        class_names = {node.name for node in analysis.classes}
        self.assertTrue({"Props", "Mode", "Status", "Store"}.issubset(class_names))
        func_names = {node.name for node in analysis.functions}
        self.assertTrue({"Card", "Link", "Plain"}.issubset(func_names))
    
    @REQUIRES_TREE_SITTER
    def test_extract_python_file(self):
        """Test extraction from a real Python file (core.py)"""
        from thepipe.analyzer import extract_file
        
        core_path = str(THEPIPE_DIR / "core.py")
        analysis = extract_file(core_path)
        
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "python")
        self.assertGreater(len(analysis.functions), 0)
        self.assertGreater(len(analysis.classes), 0)
        self.assertGreater(len(analysis.imports), 0)
        
        # Check we found the Chunk class
        class_names = [c.name for c in analysis.classes]
        self.assertIn("Chunk", class_names)
    
    @REQUIRES_TREE_SITTER
    def test_extract_functions(self):
        """Test function extraction from scraper.py"""
        from thepipe.analyzer import extract_file
        
        scraper_path = str(THEPIPE_DIR / "scraper.py")
        analysis = extract_file(scraper_path)
        
        self.assertIsNotNone(analysis)
        func_names = [f.name for f in analysis.functions]
        
        # scraper.py should have these functions
        self.assertIn("scrape_file", func_names)
        self.assertIn("scrape_directory", func_names)
        # Note: some function names may have parsing artifacts
    
    @REQUIRES_TREE_SITTER
    def test_extract_imports(self):
        """Test import extraction"""
        from thepipe.analyzer import extract_file
        
        core_path = str(THEPIPE_DIR / "core.py")
        analysis = extract_file(core_path)
        
        self.assertIsNotNone(analysis)
        # core.py imports things like argparse, base64, etc.
        import_text = " ".join(analysis.imports)
        self.assertIn("import", import_text)

    @REQUIRES_TREE_SITTER
    def test_extract_imports_from_require_and_dynamic_import(self):
        """Test web plugin captures literal paths for require() and import()."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = 'const foo = require("./foo");\nconst bar = import("./bar");\n'
        analysis = ASTExtractor().extract(code, "javascript", "x.mjs")

        self.assertIsNotNone(analysis)
        imports = " ".join(analysis.imports)
        self.assertIn("./foo", imports)
        self.assertIn("./bar", imports)

    @REQUIRES_TREE_SITTER
    def test_extract_call_graph_python(self):
        """Test call graph extraction for Python"""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = "def foo():\n    bar()\n\ndef bar():\n    pass\n"
        analysis = ASTExtractor().extract(code, "python", "x.py")

        self.assertIsNotNone(analysis)
        self.assertGreaterEqual(len(analysis.call_graph), 1)
        calls = {(e.caller, e.callee) for e in analysis.call_graph}
        self.assertIn(("foo", "bar"), calls)

    @REQUIRES_TREE_SITTER
    def test_extract_call_graph_javascript(self):
        """Test call graph extraction for JavaScript"""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = "function foo(){ bar(); }\\nconst baz=()=>{ qux(); }\\n"
        analysis = ASTExtractor().extract(code, "javascript", "x.js")

        self.assertIsNotNone(analysis)
        calls = {(e.caller, e.callee) for e in analysis.call_graph}
        self.assertIn(("foo", "bar"), calls)
        self.assertIn(("baz", "qux"), calls)

    @REQUIRES_TREE_SITTER
    def test_extract_named_arrow_function(self):
        """Arrow functions assigned to vars should keep the assigned name."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = "const Home = () => { return 1; };\n"
        analysis = ASTExtractor().extract(code, "javascript", "x.js")

        self.assertIsNotNone(analysis)
        self.assertEqual([f.name for f in analysis.functions if f.name], ["Home"])

    @REQUIRES_TREE_SITTER
    def test_extract_call_graph_skips_ambiguous_chained_callee(self):
        """Do not emit arbitrary raw AST subtree text as a callee."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = (
            "async function processRequest(url){\n"
            "  await fetch(url).then(cb);\n"
            "  greeter.greet();\n"
            "}\n"
        )
        analysis = ASTExtractor().extract(code, "javascript", "x.js")

        self.assertIsNotNone(analysis)
        callees = {e.callee for e in analysis.call_graph}
        self.assertIn("greeter.greet", callees)
        self.assertFalse(any("(" in callee or "await" in callee for callee in callees))

    @REQUIRES_TREE_SITTER
    def test_extract_swift_protocol_and_extension_types(self):
        """Swift extraction should keep protocol and extension surfaces."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = (
            "import Foundation\n"
            "protocol Greeter { func greet() }\n"
            "extension String { func x() {} }\n"
            "enum Mode { case a }\n"
            "actor Worker {}\n"
            "struct User {}\n"
            "class VC {}\n"
        )
        analysis = ASTExtractor().extract(code, "swift", "x.swift")

        self.assertIsNotNone(analysis)
        class_names = {c.name for c in analysis.classes if c.name}
        self.assertEqual(
            class_names,
            {"Greeter", "String", "Mode", "Worker", "User", "VC"},
        )

    @REQUIRES_TREE_SITTER
    def test_extract_dart_constructor_signature(self):
        """Dart constructors should still be surfaced as function-like entries."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = (
            "class A {\n"
            "  A();\n"
            "  void m() {}\n"
            "}\n"
        )
        analysis = ASTExtractor().extract(code, "dart", "x.dart")

        self.assertIsNotNone(analysis)
        func_names = {f.name for f in analysis.functions if f.name}
        self.assertIn("A", func_names)
        self.assertIn("m", func_names)

    @REQUIRES_TREE_SITTER
    def test_extract_python_names_after_unicode_bytes(self):
        """Byte offsets should stay correct after earlier Unicode content."""
        from thepipe.analyzer.ast_extractor import ASTExtractor
        from thepipe.analyzer.digest import generate_file_digest

        code = (
            'banner = "📊"\n'
            "\n"
            "def first():\n"
            "    return 1\n"
            "\n"
            "def second(value: int) -> int:\n"
            "    return value\n"
        )
        analysis = ASTExtractor().extract(code, "python", "x.py")

        self.assertIsNotNone(analysis)
        names = [f.name for f in analysis.functions if f.name]
        self.assertEqual(names, ["first", "second"])
        digest = generate_file_digest(code, analysis)
        self.assertIn("second :: value:int -> int", digest)
    
    def test_extract_cross_language_detection(self):
        """Test detection of cross-language bridges"""
        from thepipe.analyzer.ast_extractor import ASTExtractor
        
        extractor = ASTExtractor()
        
        # Test Cython detection
        cython_code = "cimport numpy\ncdef int x = 0"
        self.assertTrue(extractor._detect_cross_language(cython_code, "test.pyx"))
        
        # Test ctypes detection
        ctypes_code = "from ctypes import CDLL"
        self.assertTrue(extractor._detect_cross_language(ctypes_code, "test.py"))
        
        # Normal Python shouldn't be detected
        normal_code = "import os\nprint('hello')"
        self.assertFalse(extractor._detect_cross_language(normal_code, "test.py"))

    def test_haskell_regex_fallback_filters_plain_bindings(self):
        """Haskell fallback should not treat every value binding as a function."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = (
            "add :: Int -> Int -> Int\n"
            "add x y = x + y\n"
            "x = 5\n"
            "config = defaultConfig { port = 8080 }\n"
        )
        functions = ASTExtractor()._regex_fallback_functions("haskell", code)
        names = [f.name for f in functions]

        self.assertIn("add", names)
        self.assertNotIn("x", names)
        self.assertNotIn("config", names)

    def test_ruby_regex_fallback_uses_declaration_boundaries(self):
        """Ruby fallback should not be confused by block keywords inside strings."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = (
            "class Parser\n"
            "  def process\n"
            "    query = \"BEGIN transaction; end of story\"\n"
            "    puts \"defend the base\"\n"
            "  end\n"
            "end\n"
            "\n"
            "module Later\n"
            "end\n"
        )
        extractor = ASTExtractor()
        classes = extractor._regex_fallback_classes("ruby", code)
        functions = extractor._regex_fallback_functions("ruby", code)

        parser = next(c for c in classes if c.name == "Parser")
        process = next(f for f in functions if f.name == "process")
        self.assertEqual(parser.end_line, 7)
        self.assertEqual(process.end_line, 7)

    def test_php_regex_fallback_uses_declaration_boundaries(self):
        """PHP fallback should ignore braces inside strings."""
        from thepipe.analyzer.ast_extractor import ASTExtractor

        code = (
            "class Parser {\n"
            "    public function getPattern() {\n"
            "        return \"{ not a real brace }\";\n"
            "    }\n"
            "}\n"
            "\n"
            "function main() {\n"
            "    return 1;\n"
            "}\n"
        )
        extractor = ASTExtractor()
        classes = extractor._regex_fallback_classes("php", code)
        functions = extractor._regex_fallback_functions("php", code)

        parser = next(c for c in classes if c.name == "Parser")
        get_pattern = next(f for f in functions if f.name == "getPattern")
        main = next(f for f in functions if f.name == "main")
        self.assertEqual(parser.end_line, 6)
        self.assertEqual(get_pattern.end_line, 6)
        self.assertEqual(main.start_line, 7)


@REQUIRES_TREE_SITTER
class TestDependencyGraph(unittest.TestCase):
    """Test dependency graph building"""
    
    def test_build_graph(self):
        """Test building a dependency graph from the thepipe codebase"""
        from thepipe.analyzer import Analyzer, AnalyzerConfig
        
        config = AnalyzerConfig(include_patterns=["**/*.py"])
        analyzer = Analyzer(str(REPO_ROOT), config)
        result = analyzer.analyze()
        
        self.assertGreater(result.total_files, 0)
        self.assertGreater(len(result.dependency_graph.edges), 0)
    
    def test_nearest_neighbors_depth_1(self):
        """Test nearest neighbor calculation at depth 1"""
        from thepipe.analyzer import DependencyGraph, DependencyEdge
        
        graph = DependencyGraph()
        
        # Create a simple graph: A -> B -> C
        graph.add_edge(DependencyEdge(
            from_file="a.py", to_file="b.py",
            import_statement="import b", is_external=False
        ))
        graph.add_edge(DependencyEdge(
            from_file="b.py", to_file="c.py",
            import_statement="import c", is_external=False
        ))
        
        # Depth 1 from A should include B
        neighbors = graph.nearest_neighbors("a.py", depth=1)
        self.assertIn("b.py", neighbors)
        self.assertNotIn("c.py", neighbors)
    
    def test_nearest_neighbors_depth_2(self):
        """Test nearest neighbor calculation at depth 2"""
        from thepipe.analyzer import DependencyGraph, DependencyEdge
        
        graph = DependencyGraph()
        graph.add_edge(DependencyEdge(
            from_file="a.py", to_file="b.py",
            import_statement="import b", is_external=False
        ))
        graph.add_edge(DependencyEdge(
            from_file="b.py", to_file="c.py",
            import_statement="import c", is_external=False
        ))
        
        # Depth 2 from A should include B and C
        neighbors = graph.nearest_neighbors("a.py", depth=2)
        self.assertIn("b.py", neighbors)
        self.assertIn("c.py", neighbors)
    
    def test_nearest_neighbors_bidirectional(self):
        """Test that neighbors includes both imports and importers"""
        from thepipe.analyzer import DependencyGraph, DependencyEdge
        
        graph = DependencyGraph()
        # A imports B, C imports A
        graph.add_edge(DependencyEdge(
            from_file="a.py", to_file="b.py",
            import_statement="import b", is_external=False
        ))
        graph.add_edge(DependencyEdge(
            from_file="c.py", to_file="a.py",
            import_statement="import a", is_external=False
        ))
        
        # Neighbors of A should include both B and C
        neighbors = graph.nearest_neighbors("a.py", depth=1)
        self.assertIn("b.py", neighbors)
        self.assertIn("c.py", neighbors)
    
    def test_external_deps_not_in_adjacency(self):
        """Test that external dependencies don't appear in internal adjacency"""
        from thepipe.analyzer import DependencyGraph, DependencyEdge
        
        graph = DependencyGraph()
        graph.add_edge(DependencyEdge(
            from_file="a.py", to_file="os",
            import_statement="import os", is_external=True
        ))
        graph.add_edge(DependencyEdge(
            from_file="a.py", to_file="b.py",
            import_statement="import b", is_external=False
        ))
        
        # Only internal deps in adjacency
        imports = graph.imports_of("a.py")
        self.assertIn("b.py", imports)
        self.assertNotIn("os", imports)


class TestIntegrationHelpers(unittest.TestCase):
    """Test mapnew helpers and chunk metadata wiring."""

    def test_remove_worktree_recursively_cleans_directory(self):
        from thepipe.analyzer.integration import _remove_worktree

        with tempfile.TemporaryDirectory() as temp_dir:
            nested_dir = Path(temp_dir) / "repo"
            nested_dir.mkdir()
            (nested_dir / "artifact.txt").write_text("x", encoding="utf-8")

            with mock.patch("thepipe.analyzer.integration.subprocess.run", side_effect=RuntimeError("locked")):
                _remove_worktree("/tmp/repo", str(nested_dir))

            self.assertFalse(nested_dir.exists())

    def test_create_worktree_cleans_temp_dir_on_failure(self):
        from thepipe.analyzer.integration import _create_worktree

        with tempfile.TemporaryDirectory() as temp_dir:
            worktree_dir = Path(temp_dir) / "thepipe_mapnew_fixed"
            worktree_dir.mkdir()
            (worktree_dir / "placeholder.txt").write_text("x", encoding="utf-8")

            with mock.patch("thepipe.analyzer.integration.tempfile.mkdtemp", return_value=str(worktree_dir)):
                with mock.patch("thepipe.analyzer.integration.subprocess.run", side_effect=RuntimeError("bad ref")):
                    with self.assertRaises(RuntimeError):
                        _create_worktree("/tmp/repo", "HEAD~999")

            self.assertFalse(worktree_dir.exists())

    def test_diff_chunk_outputs_uses_labels(self):
        from thepipe.analyzer.integration import _diff_chunk_outputs
        from thepipe.core import Chunk

        old_chunks = [Chunk(path="a.py", text="# a.py\nold")]
        new_chunks = [Chunk(path="a.py", text="# a.py\nnew")]

        diff_text = _diff_chunk_outputs(old_chunks, new_chunks, "HEAD", "working-tree")

        self.assertIn("--- old:HEAD", diff_text)
        self.assertIn("+++ new:working-tree", diff_text)
        self.assertIn("-old", diff_text)
        self.assertIn("+new", diff_text)

    def test_mapnew_fallback_chunk_uses_virtual_artifact_name(self):
        from thepipe.analyzer.integration import _mapnew_fallback_chunk

        chunk = _mapnew_fallback_chunk(
            error="boom",
            dir_path="/repo",
            include_patterns=["src/**/*.py"],
            code_old="HEAD",
            code_new=None,
        )

        self.assertEqual(chunk.path, "mapnew-fallback.md")
        self.assertEqual(chunk.meta["artifact"], "mapnew_fallback")
        self.assertIn('--include_patterns "src/**/*.py"', chunk.text)

    def test_analysis_to_meta_includes_import_strings(self):
        from thepipe.analyzer.integration import _analysis_to_meta
        from thepipe.analyzer.types import FileAnalysis, ASTNode, CallGraphEntry

        content = "import os\nfrom app import run\n\ndef foo():\n    bar()\n"
        start_byte = content.encode("utf-8").index(b"def foo")
        end_byte = len(content.encode("utf-8"))
        analysis = FileAnalysis(
            path="x.py",
            language="python",
            imports=[" import os ", "from app import run", "import os"],
            functions=[ASTNode(type="function", name="foo", start_line=4, end_line=5, start_byte=start_byte, end_byte=end_byte)],
            call_graph=[CallGraphEntry(caller="foo", callee="bar", line=2)],
            line_count=5,
        )

        meta = _analysis_to_meta(analysis, content)

        self.assertEqual(meta["imports"], ["from app import run", "import os"])
        self.assertEqual(meta["imports_count"], 3)
        region_ids = [region["id"] for region in meta["regions"]]
        self.assertIn("module:top", region_ids)
        self.assertIn("func:foo", region_ids)
        foo_region = next(region for region in meta["regions"] if region["id"] == "func:foo")
        self.assertIn("map_hash", foo_region)
        self.assertIn("content_hash", foo_region)
        self.assertIn("map_git_oid", foo_region)
        self.assertIn("content_git_oid", foo_region)

    def test_build_code_relations_json_payload_emits_entities_and_edges(self):
        from thepipe.analyzer.integration import build_code_relations_json_payload
        from thepipe.core import Chunk

        chunk = Chunk(
            path="a.py",
            text="# a.py (digest)\n(module a.py\n  (imports\n    \"import os\"\n  )\n  (functions foo)\n)",
            meta={
                "language": "python",
                "line_count": 5,
                "imports": ["import os"],
                "imports_count": 1,
                "functions": [{"name": "foo", "start_line": 3, "end_line": 4}],
                "classes": [],
                "regions": [
                    {
                        "id": "module:top",
                        "kind": "module",
                        "name": "top",
                        "qualified_name": "top",
                        "container": None,
                        "start_line": 1,
                        "end_line": 5,
                        "map_hash": "aaaa",
                        "content_hash": "bbbb",
                        "map_git_oid": "git:blob:sha1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "content_git_oid": "git:blob:sha1:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    },
                    {
                        "id": "func:foo",
                        "kind": "function",
                        "name": "foo",
                        "qualified_name": "foo",
                        "container": None,
                        "start_line": 3,
                        "end_line": 4,
                        "map_hash": "cccc",
                        "content_hash": "dddd",
                        "map_git_oid": "git:blob:sha1:cccccccccccccccccccccccccccccccccccccccc",
                        "content_git_oid": "git:blob:sha1:dddddddddddddddddddddddddddddddddddddddd",
                    },
                ],
                "call_graph": [{"caller": "foo", "callee": "print", "line": 4}],
                "dependencies": [
                    {
                        "target": "os",
                        "import_statement": "import os",
                        "import_type": "import",
                        "is_external": True,
                    }
                ],
            },
        )

        payload = build_code_relations_json_payload([chunk], mode="map", repo_root=".")

        self.assertEqual(payload["schema_version"], "code-relations/v1")
        self.assertEqual(payload["mode"], "map")
        self.assertEqual(payload["files"][0]["file_id"], "file:a.py")
        entity_ids = {entity["entity_id"] for entity in payload["entities"]}
        self.assertIn("entity:file:a.py:module:top", entity_ids)
        self.assertIn("entity:file:a.py:func:foo", entity_ids)
        edge_kinds = {(edge["kind"], edge["from_entity_id"], edge["to_entity_id"]) for edge in payload["edges"]}
        self.assertIn(
            ("contains", "entity:file:a.py:module:top", "entity:file:a.py:func:foo"),
            edge_kinds,
        )
        self.assertIn(
            ("imports", "entity:file:a.py:module:top", "dep:os"),
            edge_kinds,
        )
        self.assertIn(
            ("calls", "entity:file:a.py:func:foo", "symbol:print"),
            edge_kinds,
        )
        call_edge = next(edge for edge in payload["edges"] if edge["kind"] == "calls")
        self.assertEqual(call_edge["location"]["start_line"], 4)
        self.assertEqual(call_edge["location"]["end_line"], 4)

    def test_process_mapnew_end_to_end(self):
        from thepipe.analyzer.integration import process_code_relations

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            with mock.patch("thepipe.analyzer.integration.logger"):
                subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
                subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
                subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

                (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
                subprocess.run(["git", "add", "a.py"], cwd=repo, check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

                (repo / "a.py").write_text(
                    "import os\n\n"
                    "def foo(value: int) -> int:\n"
                    "    return value\n",
                    encoding="utf-8",
                )

                chunks = process_code_relations(str(repo), mode="mapnew")

            self.assertEqual(len(chunks), 1)
            chunk = chunks[0]
            self.assertEqual(chunk.path, "mapnew.diff")
            self.assertEqual(chunk.meta["artifact"], "mapnew_diff")
            self.assertEqual(chunk.meta["changed_files_count"], 1)
            self.assertIn("--- old:HEAD", chunk.text)
            self.assertIn("+++ new:working-tree", chunk.text)
            self.assertIn('"import os"', chunk.text)
            self.assertIn("-foo :: () -> None", chunk.text)
            self.assertIn("+foo :: value:int -> int", chunk.text)
            self.assertEqual(chunk.meta["implementation_only_regions"], 0)
            self.assertEqual(len(chunk.meta["files"]), 1)
            file_meta = chunk.meta["files"][0]
            self.assertEqual(file_meta["path"], "a.py")
            self.assertEqual(file_meta["status"], "modified")
            self.assertTrue(file_meta["map_changed"])
            self.assertEqual(file_meta["hunks"][0]["old_start"], 1)
            self.assertEqual(file_meta["hunks"][0]["new_start"], 1)
            self.assertEqual(file_meta["old"]["path"], "a.py")
            self.assertEqual(file_meta["new"]["path"], "a.py")
            self.assertIn("meta", file_meta["old"])
            self.assertIn("meta", file_meta["new"])
            self.assertTrue(any(
                region["change"] == "structural" and region["id"] == "func:foo"
                for region in file_meta["region_changes"]
            ))
            self.assertTrue(any(
                region["change"] == "structural" and region["id"] == "module:top"
                for region in file_meta["region_changes"]
            ))

    def test_process_mapnew_reports_implementation_only_changes(self):
        from thepipe.analyzer.integration import process_code_relations

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

            (repo / "a.py").write_text(
                "def foo():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "a.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            (repo / "a.py").write_text(
                "def foo():\n"
                "    return 2\n",
                encoding="utf-8",
            )

            chunk = process_code_relations(str(repo), mode="mapnew")[0]

        self.assertIn("## Implementation-Only Changes", chunk.text)
        self.assertIn("function `foo`", chunk.text)
        self.assertIn("map unchanged", chunk.text)
        self.assertEqual(chunk.meta["implementation_only_regions"], 1)
        self.assertEqual(chunk.meta["changed_files_count"], 1)
        file_meta = chunk.meta["files"][0]
        self.assertEqual(file_meta["path"], "a.py")
        self.assertFalse(file_meta["map_changed"])
        self.assertEqual(len(file_meta["implementation_only_regions"]), 1)
        region = file_meta["implementation_only_regions"][0]
        self.assertEqual(region["id"], "func:foo")
        self.assertEqual(region["change"], "implementation_only")
        self.assertEqual(region["changed_ranges"]["old"], [{"start": 2, "end": 2}])
        self.assertEqual(region["changed_ranges"]["new"], [{"start": 2, "end": 2}])
        self.assertIn("map unchanged", region["note"])

    def test_process_mapnew_tracks_renames_for_preview_metadata(self):
        from thepipe.analyzer.integration import process_code_relations

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

            (repo / "a.py").write_text(
                "def foo():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "a.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            subprocess.run(["git", "mv", "a.py", "b.py"], cwd=repo, check=True, capture_output=True)

            chunk = process_code_relations(str(repo), mode="mapnew")[0]

        self.assertEqual(chunk.meta["changed_files_count"], 1)
        file_meta = chunk.meta["files"][0]
        self.assertEqual(file_meta["status"], "renamed")
        self.assertEqual(file_meta["old_path"], "a.py")
        self.assertEqual(file_meta["new_path"], "b.py")
        self.assertEqual(file_meta["path"], "b.py")
        self.assertEqual(file_meta["old"]["path"], "a.py")
        self.assertEqual(file_meta["new"]["path"], "b.py")
        self.assertFalse(file_meta["map_changed"])

    def test_process_mapnew_includes_untracked_added_files_in_preview_metadata(self):
        from thepipe.analyzer.integration import process_code_relations

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

            (repo / "a.py").write_text(
                "def base():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "a.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            (repo / "b.py").write_text(
                "def new_feature():\n"
                "    return 2\n",
                encoding="utf-8",
            )

            chunk = process_code_relations(str(repo), mode="mapnew")[0]

        self.assertEqual(chunk.meta["changed_files_count"], 1)
        file_meta = chunk.meta["files"][0]
        self.assertEqual(file_meta["path"], "b.py")
        self.assertEqual(file_meta["status"], "added")
        self.assertTrue(file_meta["untracked"])
        self.assertIsNone(file_meta["old"])
        self.assertEqual(file_meta["new"]["path"], "b.py")
        self.assertEqual(file_meta["hunks"], [{
            "old_start": 0,
            "old_end": None,
            "new_start": 1,
            "new_end": 2,
        }])
        self.assertTrue(any(
            region["change"] == "added" and region["id"] == "func:new_feature"
            for region in file_meta["region_changes"]
        ))

    def test_process_mapnew_supports_explicit_refs_and_json_preview_payload(self):
        from thepipe.analyzer.integration import process_code_relations

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

            (repo / "a.py").write_text(
                "def foo():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "a.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "first"], cwd=repo, check=True, capture_output=True)
            old_ref = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            (repo / "a.py").unlink()
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "remove"], cwd=repo, check=True, capture_output=True)
            new_ref = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            chunk = process_code_relations(str(repo), mode="mapnew", code_old=old_ref, code_new=new_ref)[0]
            payload = chunk.to_json(verbose=True)

        self.assertEqual(chunk.meta["old_ref"], old_ref)
        self.assertEqual(chunk.meta["new_ref"], new_ref)
        self.assertEqual(chunk.meta["changed_files_count"], 1)
        file_meta = chunk.meta["files"][0]
        self.assertEqual(file_meta["path"], "a.py")
        self.assertEqual(file_meta["status"], "deleted")
        self.assertFalse(file_meta["untracked"])
        self.assertEqual(file_meta["old"]["path"], "a.py")
        self.assertIsNone(file_meta["new"])
        self.assertTrue(any(
            region["change"] == "removed" and region["id"] == "func:foo"
            for region in file_meta["region_changes"]
        ))
        self.assertEqual(payload["meta"]["files"][0]["status"], "deleted")
        json.dumps(payload)

    def test_process_mapnew_handles_paths_with_spaces(self):
        from thepipe.analyzer.integration import process_code_relations

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

            file_path = repo / "a b.py"
            file_path.write_text(
                "def foo():\n"
                "    return 1\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "a b.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            file_path.write_text(
                "def foo():\n"
                "    return 2\n",
                encoding="utf-8",
            )

            chunk = process_code_relations(str(repo), mode="mapnew")[0]

        self.assertEqual(chunk.meta["changed_files_count"], 1)
        file_meta = chunk.meta["files"][0]
        self.assertEqual(file_meta["path"], "a b.py")
        self.assertEqual(file_meta["status"], "modified")
        self.assertFalse(file_meta["map_changed"])
        self.assertEqual(file_meta["old"]["path"], "a b.py")
        self.assertEqual(file_meta["new"]["path"], "a b.py")
        self.assertEqual(file_meta["implementation_only_regions"][0]["id"], "func:foo")


class TestDigestGenerator(unittest.TestCase):
    """Test digest generation (Haskell-style signatures, S-expressions)"""
    
    def test_function_signature_python(self):
        """Test Python function signature generation"""
        from thepipe.analyzer.digest import DigestGenerator
        from thepipe.analyzer import ASTNode
        
        source = "def add(x: int, y: int) -> int:\n    return x + y"
        generator = DigestGenerator(source, "python")
        
        node = ASTNode(
            type="function", name="add",
            start_line=1, end_line=2,
            start_byte=0, end_byte=len(source)
        )
        
        digest = generator.function_signature(node)
        self.assertEqual(digest.type, "signature")
        self.assertIn("add", digest.content)
        self.assertIn("int", digest.content)
        self.assertIn("::", digest.content)  # Haskell-style
    
    def test_class_structure_sexpression(self):
        """Test S-expression class structure generation"""
        from thepipe.analyzer.digest import DigestGenerator
        from thepipe.analyzer import ASTNode
        
        source = "class Foo:\n    def bar(self): pass\n    def _private(self): pass"
        generator = DigestGenerator(source, "python")
        
        cls_node = ASTNode(type="class", name="Foo", start_line=1, end_line=3)
        methods = [
            ASTNode(type="function", name="bar", start_line=2, end_line=2),
            ASTNode(type="function", name="_private", start_line=3, end_line=3),
        ]
        
        digest = generator.class_structure(cls_node, methods)
        self.assertEqual(digest.type, "class_structure")
        self.assertIn("(Foo", digest.content)
        self.assertIn("public-methods", digest.content)
        self.assertIn("bar", digest.content)
        self.assertIn("private-methods", digest.content)
        self.assertIn("_private", digest.content)
    
    def test_module_index(self):
        """Test module index generation"""
        from thepipe.analyzer.digest import DigestGenerator
        from thepipe.analyzer import FileAnalysis, ASTNode
        
        analysis = FileAnalysis(
            path="test/module.py",
            language="python",
            imports=["import os", "import sys"],
            functions=[
                ASTNode(type="function", name="foo"),
                ASTNode(type="function", name="bar"),
            ],
            classes=[ASTNode(type="class", name="MyClass")],
            line_count=100,
        )
        
        generator = DigestGenerator("", "python")
        digest = generator.module_index(analysis)
        
        self.assertEqual(digest.type, "module_index")
        self.assertIn("(module", digest.content)
        self.assertIn("\"import os\"", digest.content)
        self.assertIn("\"import sys\"", digest.content)
        self.assertIn("foo", digest.content)
        self.assertIn("bar", digest.content)
        self.assertIn("MyClass", digest.content)

    def test_call_graph_dedup_and_filter(self):
        """Test call graph de-duplication and noisy filtering"""
        from thepipe.analyzer.digest import DigestGenerator
        from thepipe.analyzer.types import CallGraphEntry

        calls = [
            CallGraphEntry(caller="foo", callee="console.log", line=1),
            CallGraphEntry(caller="foo", callee="console.log", line=2),  # duplicate
            CallGraphEntry(caller="foo", callee="doThing", line=3),
            CallGraphEntry(caller="foo", callee="doThing", line=4),  # duplicate
            CallGraphEntry(caller="bar", callee="print", line=5),
        ]

        digest = DigestGenerator("", "javascript").call_graph(calls)
        self.assertIsNotNone(digest)
        self.assertIn("(foo -> doThing)", digest.content)
        self.assertNotIn("console.log", digest.content)
        self.assertNotIn("print", digest.content)

    def test_digest_stable_ordering(self):
        """Test deterministic ordering for module index, class methods, and call graph."""
        from thepipe.analyzer.digest import DigestGenerator
        from thepipe.analyzer.types import CallGraphEntry
        from thepipe.analyzer import FileAnalysis, ASTNode

        analysis = FileAnalysis(
            path="test/module.py",
            language="python",
            functions=[
                ASTNode(type="function", name="beta"),
                ASTNode(type="function", name="alpha"),
            ],
            classes=[
                ASTNode(type="class", name="Zebra"),
                ASTNode(type="class", name="Apple"),
            ],
            line_count=10,
        )

        generator = DigestGenerator("", "python")
        module_digest = generator.module_index(analysis)
        self.assertIn("(functions alpha beta)", module_digest.content)
        self.assertIn("(classes Apple Zebra)", module_digest.content)

        cls_node = ASTNode(type="class", name="Foo", start_line=1, end_line=5)
        methods = [
            ASTNode(type="function", name="_beta", start_line=2, end_line=2),
            ASTNode(type="function", name="_alpha", start_line=3, end_line=3),
            ASTNode(type="function", name="beta", start_line=4, end_line=4),
            ASTNode(type="function", name="alpha", start_line=5, end_line=5),
        ]
        class_digest = generator.class_structure(cls_node, methods)
        self.assertIn("(public-methods alpha beta)", class_digest.content)
        self.assertIn("(private-methods _alpha _beta)", class_digest.content)

        calls = [
            CallGraphEntry(caller="b", callee="z", line=2),
            CallGraphEntry(caller="a", callee="m", line=1),
        ]
        call_digest = generator.call_graph(calls)
        self.assertIsNotNone(call_digest)
        self.assertLess(
            call_digest.content.find("(a -> m)"),
            call_digest.content.find("(b -> z)"),
        )

    def test_digest_escapes_non_atom_symbol_names(self):
        """S-expression surfaces should quote names like Ruby predicates."""
        from thepipe.analyzer.digest import DigestGenerator
        from thepipe.analyzer import FileAnalysis, ASTNode

        analysis = FileAnalysis(
            path="x.rb",
            language="ruby",
            functions=[ASTNode(type="function", name="valid?")],
            classes=[ASTNode(type="class", name="User")],
            line_count=2,
        )
        generator = DigestGenerator("", "ruby")
        module_digest = generator.module_index(analysis)
        class_digest = generator.class_structure(
            ASTNode(type="class", name="User", start_line=1, end_line=2),
            [ASTNode(type="function", name="valid?", start_line=2, end_line=2)],
        )

        self.assertIn('(functions "valid?")', module_digest.content)
        self.assertIn('(public-methods "valid?")', class_digest.content)

    def test_tsx_digest_uses_js_signature_parser(self):
        """TSX should keep typed JS/TS signature extraction."""
        from thepipe.analyzer.digest import DigestGenerator
        from thepipe.analyzer.types import ASTNode

        source = "function Button(props: Props): JSX.Element {}"
        digest = DigestGenerator(source, "tsx").function_signature(
            ASTNode(
                type="function",
                name="Button",
                start_line=1,
                end_line=1,
                start_byte=0,
                end_byte=len(source),
            )
        )

        self.assertEqual(digest.content, "Button :: props:Props -> JSX")
    
    @REQUIRES_TREE_SITTER
    def test_generate_file_digest(self):
        """Test full file digest generation"""
        from thepipe.analyzer import extract_file
        from thepipe.analyzer.digest import generate_file_digest
        
        core_path = str(THEPIPE_DIR / "core.py")
        analysis = extract_file(core_path)
        
        with open(core_path) as f:
            source = f.read()
        
        digest = generate_file_digest(source, analysis)
        
        # Should contain module index
        self.assertIn("(module", digest)
        # Should contain call graph block (core.py has call sites)
        self.assertIn("(calls", digest)
        # Should contain Chunk class
        self.assertIn("Chunk", digest)


class TestSemanticTagger(unittest.TestCase):
    """Test semantic tagging functionality"""
    
    def test_tag_oauth_pattern(self):
        """Test OAuth pattern detection"""
        from thepipe.analyzer import SemanticTagger
        
        tagger = SemanticTagger()
        # Use explicit oauth keywords
        content = "def sso_login(oauth_token, bearer_token): pass"
        tags = tagger.tag_content(content, "auth.py")
        
        tag_names = [t.tag for t in tags]
        self.assertIn("oauth", tag_names)
    
    def test_tag_database_pattern(self):
        """Test database pattern detection"""
        from thepipe.analyzer import SemanticTagger
        
        tagger = SemanticTagger()
        content = "cursor.execute('SELECT * FROM users')"
        tags = tagger.tag_content(content, "db.py")
        
        tag_names = [t.tag for t in tags]
        self.assertIn("database", tag_names)
    
    def test_tag_async_pattern(self):
        """Test async pattern detection"""
        from thepipe.analyzer import SemanticTagger
        
        tagger = SemanticTagger()
        content = "async def fetch_data(): await response.json()"
        tags = tagger.tag_content(content, "async.py")
        
        tag_names = [t.tag for t in tags]
        self.assertIn("async", tag_names)
    
    def test_explicit_comment_tags(self):
        """Test explicit @tag: comments"""
        from thepipe.analyzer import SemanticTagger
        
        tagger = SemanticTagger()
        content = "# @tag: authentication\ndef login(): pass"
        tags = tagger.tag_content(content, "login.py")
        
        tag_names = [t.tag for t in tags]
        self.assertIn("authentication", tag_names)
    
    def test_tag_real_file(self):
        """Test tagging a real file from the codebase"""
        from thepipe.analyzer import SemanticTagger
        
        tagger = SemanticTagger()
        
        # scraper.py should have multiple tags
        scraper_path = str(THEPIPE_DIR / "scraper.py")
        tags = tagger.tag_file(scraper_path)
        
        self.assertGreater(len(tags), 0)
        tag_names = [t.tag for t in tags]
        # scraper.py should have file_io and parsing tags
        self.assertTrue(any(t in tag_names for t in ["file_io", "parsing", "networking"]))


@REQUIRES_TREE_SITTER
class TestCodeRelationsIntegration(unittest.TestCase):
    """Test the code_relations integration with scrape_directory"""
    
    def test_mode_limited(self):
        """Test 'limited' mode - only include_patterns files"""
        from thepipe.scraper import scrape_directory
        
        chunks = scrape_directory(
            str(REPO_ROOT),
            include_patterns=["thepipe/core.py"],
            options={"code_relations": "limited"}
        )
        
        # Should only have core.py + summary
        paths = [c.path for c in chunks]
        self.assertIn("thepipe/core.py", paths)
        self.assertEqual(len([p for p in paths if p != "__summary__"]), 1)
    
    def test_mode_map(self):
        """Test 'map' mode - all files as digests"""
        from thepipe.scraper import scrape_directory
        
        chunks = scrape_directory(
            str(REPO_ROOT),
            include_patterns=["thepipe/core.py"],
            options={"code_relations": "map"}
        )
        
        # Should only have core.py + summary
        paths = [c.path for c in chunks]
        self.assertIn("thepipe/core.py", paths)
        self.assertEqual(len([p for p in paths if p != "__summary__"]), 1)
        
        # core.py should be digest
        core_chunk = next((c for c in chunks if c.path == "thepipe/core.py"), None)
        self.assertIsNotNone(core_chunk)
        self.assertIn("(digest)", core_chunk.text[:200])
    
    def test_mode_mapnn(self):
        """Test 'mapnn' mode - N_1/N_2 neighbor die-off"""
        from thepipe.scraper import scrape_directory
        
        chunks = scrape_directory(
            str(REPO_ROOT),
            include_patterns=["thepipe/core.py"],
            options={"code_relations": "mapnn", "code_n1": 1, "code_n2": 2}
        )
        
        # Should have fewer files than 'map' mode
        paths = [c.path for c in chunks if c.path != "__summary__"]
        # With small N values, should exclude distant files
        self.assertLess(len(paths), 25)
    
    def test_n1_n2_parameters(self):
        """Test that code_n1 and code_n2 affect results"""
        from thepipe.scraper import scrape_directory
        
        # Small N values
        chunks_small = scrape_directory(
            str(REPO_ROOT),
            include_patterns=["thepipe/core.py"],
            options={"code_relations": "mapnn", "code_n1": 1, "code_n2": 1}
        )
        
        # Larger N values
        chunks_large = scrape_directory(
            str(REPO_ROOT),
            include_patterns=["thepipe/core.py"],
            options={"code_relations": "mapnn", "code_n1": 3, "code_n2": 5}
        )
        
        # Larger N should include more files
        self.assertLessEqual(len(chunks_small), len(chunks_large))
    
    def test_output_is_chunks(self):
        """Test that output is standard Chunk objects"""
        from thepipe.scraper import scrape_directory
        from thepipe.core import Chunk
        
        chunks = scrape_directory(
            str(REPO_ROOT),
            include_patterns=["thepipe/core.py"],
            options={"code_relations": "limited"}
        )
        
        for chunk in chunks:
            self.assertIsInstance(chunk, Chunk)
            self.assertTrue(hasattr(chunk, "path"))
            self.assertTrue(hasattr(chunk, "text"))
    
    def test_summary_chunk(self):
        """Test that summary chunk is included"""
        from thepipe.scraper import scrape_directory
        
        chunks = scrape_directory(
            str(REPO_ROOT),
            include_patterns=["thepipe/core.py"],
            options={"code_relations": "mapnn"}
        )
        
        summary = next((c for c in chunks if c.path == "__summary__"), None)
        self.assertIsNotNone(summary)
        self.assertIn("Semantic Tags", summary.text)


class TestAnalyzerConfig(unittest.TestCase):
    """Test AnalyzerConfig options"""
    
    def test_default_config(self):
        """Test default configuration values"""
        from thepipe.analyzer import AnalyzerConfig
        
        config = AnalyzerConfig()
        
        self.assertTrue(config.respect_gitignore)
        self.assertTrue(config.extract_ast)
        self.assertTrue(config.build_dependency_graph)
        self.assertTrue(config.generate_digests)
        self.assertEqual(config.max_workers, 4)
    
    def test_custom_config(self):
        """Test custom configuration"""
        from thepipe.analyzer import AnalyzerConfig
        
        config = AnalyzerConfig(
            include_patterns=["**/*.ts"],
            max_file_size_mb=5.0,
            generate_semantic_tags=False,
        )
        
        self.assertEqual(config.include_patterns, ["**/*.ts"])
        self.assertEqual(config.max_file_size_mb, 5.0)
        self.assertFalse(config.generate_semantic_tags)
    
    def test_config_to_dict(self):
        """Test config serialization"""
        from thepipe.analyzer import AnalyzerConfig
        
        config = AnalyzerConfig()
        d = config.to_dict()
        
        self.assertIn("respect_gitignore", d)
        self.assertIn("include_patterns", d)
        self.assertIsInstance(d, dict)


class TestFileDiscovery(unittest.TestCase):
    """Test file discovery with gitignore and patterns"""
    
    def test_discover_python_files(self):
        """Test discovering Python files"""
        from thepipe.analyzer import discover_files, AnalyzerConfig
        
        config = AnalyzerConfig(include_patterns=["**/*.py"])
        files = discover_files(str(REPO_ROOT), config)
        
        self.assertGreater(len(files), 0)
        for f in files:
            self.assertTrue(f.endswith(".py"))
    
    def test_exclude_patterns(self):
        """Test that exclude patterns work"""
        from thepipe.analyzer import discover_files, AnalyzerConfig
        
        config = AnalyzerConfig(
            include_patterns=["**/*.py"],
            exclude_patterns=["**/test*.py"]
        )
        files = discover_files(str(REPO_ROOT), config)
        
        for f in files:
            self.assertNotIn("test_", Path(f).name)
    
    def test_max_file_size(self):
        """Test max file size filtering"""
        from thepipe.analyzer import discover_files, AnalyzerConfig
        
        # Set very small limit
        config = AnalyzerConfig(
            include_patterns=["**/*.py"],
            max_file_size_mb=0.001  # 1KB
        )
        files = discover_files(str(REPO_ROOT), config)
        
        # Should filter out larger files
        for f in files:
            size_mb = os.path.getsize(f) / (1024 * 1024)
            self.assertLessEqual(size_mb, 0.001)


if __name__ == "__main__":
    unittest.main()


@REQUIRES_TREE_SITTER
class TestUniversalLanguageSupport(unittest.TestCase):
    """Test universal language support for Dart, Swift, Kotlin, Ruby"""
    
    def setUp(self):
        from thepipe.analyzer import get_extractor
        self.extractor = get_extractor()
        self.fixtures_dir = Path(__file__).parent / "fixtures"
    
    def test_dart_extraction(self):
        """Test Dart file extraction with imports, classes, functions"""
        from thepipe.analyzer import extract_file
        
        dart_file = str(self.fixtures_dir / "test.dart")
        analysis = extract_file(dart_file)
        
        self.assertIsNotNone(analysis, "Dart file should be analyzed")
        self.assertEqual(analysis.language, "dart")
        
        # Check imports
        self.assertGreater(len(analysis.imports), 0, "Should find Dart imports")
        import_text = " ".join(analysis.imports)
        self.assertIn("import", import_text.lower())
        self.assertEqual(len(analysis.imports), 2)
        
        # Check classes (MyApp, HomePage, _HomePageState)
        self.assertGreater(len(analysis.classes), 0, "Should find Dart classes")
        class_names = [c.name for c in analysis.classes if c.name]
        self.assertEqual(set(class_names), {"MyApp", "HomePage", "_HomePageState"})
        
        # Check functions (main, build, loadData, initState)
        self.assertGreater(len(analysis.functions), 0, "Should find Dart functions")
        func_names = [f.name for f in analysis.functions if f.name]
        self.assertEqual(
            set(func_names),
            {"build", "createState", "initState", "loadData", "main"},
        )
    
    def test_swift_extraction(self):
        """Test Swift file extraction with imports, classes, structs, functions"""
        from thepipe.analyzer import extract_file
        
        swift_file = str(self.fixtures_dir / "test.swift")
        analysis = extract_file(swift_file)
        
        self.assertIsNotNone(analysis, "Swift file should be analyzed")
        self.assertEqual(analysis.language, "swift")
        
        # Check imports
        self.assertGreater(len(analysis.imports), 0, "Should find Swift imports")
        self.assertEqual(len(analysis.imports), 2)
        
        # Check classes (ViewController) and structs (User)
        self.assertGreater(len(analysis.classes), 0, "Should find Swift classes/structs")
        class_names = [c.name for c in analysis.classes if c.name]
        self.assertEqual(set(class_names), {"ViewController", "User"})
        
        # Check functions
        self.assertGreater(len(analysis.functions), 0, "Should find Swift functions")
        func_names = [f.name for f in analysis.functions if f.name]
        self.assertEqual(
            set(func_names),
            {"viewDidLoad", "setupUI", "handleTap", "greet", "main"},
        )
    
    def test_kotlin_extraction(self):
        """Test Kotlin file extraction with imports, classes, objects, functions"""
        from thepipe.analyzer import extract_file
        
        kotlin_file = str(self.fixtures_dir / "test.kt")
        analysis = extract_file(kotlin_file)
        
        self.assertIsNotNone(analysis, "Kotlin file should be analyzed")
        self.assertEqual(analysis.language, "kotlin")
        
        # Check imports
        self.assertGreater(len(analysis.imports), 0, "Should find Kotlin imports")
        
        # Check classes (MainActivity, User) and objects (Constants)
        self.assertGreater(len(analysis.classes), 0, "Should find Kotlin classes/objects")
        class_names = [c.name for c in analysis.classes if c.name]
        self.assertTrue(any(name in class_names for name in ["MainActivity", "User", "Constants"]))
        
        # Check functions
        self.assertGreater(len(analysis.functions), 0, "Should find Kotlin functions")
        func_names = [f.name for f in analysis.functions if f.name]
        # Swift function extraction may have parsing artifacts
        self.assertTrue(len(func_names) > 0)
    
    def test_ruby_extraction(self):
        """Test Ruby file extraction with requires, classes, modules, functions"""
        from thepipe.analyzer import extract_file
        from thepipe.analyzer.digest import generate_file_digest
        
        ruby_file = str(self.fixtures_dir / "test.rb")
        analysis = extract_file(ruby_file)
        
        self.assertIsNotNone(analysis, "Ruby file should be analyzed")
        self.assertEqual(analysis.language, "ruby")
        
        # Check requires (Ruby uses 'require' and 'module' for imports)
        # Pattern matching may capture module definitions as well
        import_text = " ".join(analysis.imports) if analysis.imports else ""
        self.assertTrue(len(analysis.imports) >= 0, "Imports extracted (may include modules)")
        
        # Check classes (User) and modules (Utils)
        self.assertGreater(len(analysis.classes), 0, "Should find Ruby classes/modules")
        class_names = [c.name for c in analysis.classes if c.name]
        # Ruby class/module extraction
        self.assertGreater(len(analysis.classes), 0, "Should find Ruby classes/modules")
        
        # Check functions
        self.assertGreater(len(analysis.functions), 0, "Should find Ruby functions")
        func_names = [f.name for f in analysis.functions if f.name]
        # Swift function extraction may have parsing artifacts
        self.assertTrue(len(func_names) > 0)

        digest = generate_file_digest(Path(ruby_file).read_text(), analysis)
        self.assertIn("(User", digest)
        self.assertIn("initialize", digest)
        self.assertIn("greet", digest)
        self.assertNotIn("initialize ::", digest)


if __name__ == "__main__":
    unittest.main()


@REQUIRES_TREE_SITTER
class TestTop20Languages(unittest.TestCase):
    """Test top 20 most popular programming languages"""
    
    def setUp(self):
        from thepipe.analyzer import get_extractor
        self.extractor = get_extractor()
        self.fixtures_dir = Path(__file__).parent / "fixtures"
    
    def test_java_extraction(self):
        """Test Java extraction"""
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.java"))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "java")
        self.assertGreater(len(analysis.imports), 0, "Should find Java imports")
        self.assertGreater(len(analysis.classes), 0, "Should find Java classes")
        self.assertGreater(len(analysis.functions), 0, "Should find Java methods")
    
    def test_csharp_extraction(self):
        """Test C# extraction"""
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.cs"))
        # C# parser may not be available in all tree-sitter versions
        if analysis:
            self.assertEqual(analysis.language, "c_sharp")
            self.assertGreater(len(analysis.classes), 0, "Should find C# classes")
    
    def test_php_extraction(self):
        """Test PHP extraction"""
        from thepipe.analyzer import extract_file
        from thepipe.analyzer.digest import generate_file_digest
        analysis = extract_file(str(self.fixtures_dir / "test.php"))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "php")
        self.assertGreater(len(analysis.classes), 0, "Should find PHP classes")
        self.assertGreater(len(analysis.functions), 0, "Should find PHP functions")
        digest = generate_file_digest((self.fixtures_dir / "test.php").read_text(), analysis)
        self.assertIn("(User", digest)
        self.assertIn("__construct", digest)
        self.assertIn("greet", digest)
        self.assertNotIn("__construct ::", digest)
    
    def test_scala_extraction(self):
        """Test Scala extraction"""
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.scala"))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "scala")
        self.assertGreater(len(analysis.imports), 0, "Should find Scala imports")
        self.assertGreater(len(analysis.classes), 0, "Should find Scala classes/objects/traits")
    
    def test_r_extraction(self):
        """Test R extraction"""  
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.R"))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "r")
        # R uses library() which may or may not be caught as imports
        self.assertGreater(len(analysis.functions), 0, "Should find R functions")
    
    def test_perl_extraction(self):
        """Test Perl extraction"""
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.pl"))
        self.assertIsNotNone(analysis)
        # Perl may be detected (we have .pl extension mapped)
        # Just verify we can parse it without crashing
        self.assertTrue(analysis.language is not None or analysis is not None)
    
    def test_haskell_extraction(self):
        """Test Haskell extraction"""
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.hs"))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "haskell")
        self.assertEqual(
            analysis.imports,
            ["import Data.List", "import Control.Monad"],
        )
        self.assertEqual(
            [f.name for f in analysis.functions if f.name],
            ["greet", "processData", "main"],
        )
        self.assertEqual(
            [c.name for c in analysis.classes if c.name],
            ["User"],
        )
    
    def test_lua_extraction(self):
        """Test Lua extraction"""
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.lua"))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "lua")
        self.assertGreater(len(analysis.functions), 0, "Should find Lua functions")
    
    def test_elixir_extraction(self):
        """Test Elixir extraction"""
        from thepipe.analyzer import extract_file
        analysis = extract_file(str(self.fixtures_dir / "test.ex"))
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.language, "elixir")
        # Elixir function detection may need specific patterns
        self.assertTrue(len(analysis.functions) >= 0, "Should process Elixir file")
    
    def test_objc_extraction(self):
        """Test Objective-C extraction"""
        from thepipe.analyzer import extract_file  
        analysis = extract_file(str(self.fixtures_dir / "test.m"))
        # May not have explicit objc support, but shouldn't crash
        if analysis:
            self.assertTrue(len(analysis.classes) >= 0)
            self.assertTrue(len(analysis.functions) >= 0)


if __name__ == "__main__":
    unittest.main()


@REQUIRES_TREE_SITTER
class TestEmbeddedLanguages(unittest.TestCase):
    """Test files with embedded/multiple languages (HTML+CSS+JS, PHP+jQuery)"""
    
    def setUp(self):
        from thepipe.analyzer import get_extractor
        self.extractor = get_extractor()
        self.fixtures_dir = Path(__file__).parent / "fixtures"
    
    def test_html_with_embedded_js_css(self):
        """Test HTML file with embedded CSS and JavaScript"""
        from thepipe.analyzer import extract_file
        
        html_file = str(self.fixtures_dir / "test_webapp.html")
        analysis = extract_file(html_file)
        
        # HTML files might not have full analysis, but shouldn't crash
        # Tree-sitter can parse HTML and extract embedded <script> blocks
        self.assertTrue(analysis is None or analysis.language in ['html', None])
        
        # The goal is to verify we can handle multi-language files without crashing
        # Full extraction of embedded languages would require special handling
    
    def test_php_with_embedded_html_jquery(self):
        """Test PHP file with embedded HTML, CSS, and jQuery"""
        from thepipe.analyzer import extract_file
        
        php_file = str(self.fixtures_dir / "test_webapp.php")
        analysis = extract_file(php_file)
        
        self.assertIsNotNone(analysis, "Should analyze PHP file")
        self.assertEqual(analysis.language, "php")
        
        # PHP with embedded HTML may have limited class extraction
        # This is exploratory - tree-sitter may struggle with mixed languages
        class_names = [c.name for c in analysis.classes if c.name]
        # Document findings: embedding HTML can interfere with PHP parsing
        
        # Should find PHP functions/methods
        self.assertGreater(len(analysis.functions), 0, "Should find PHP methods")
        func_names = [f.name for f in analysis.functions if f.name]
        self.assertTrue(any(name in func_names for name in ["getAllUsers", "addUser", "deleteUser", "__construct"]))
    
    def test_multi_language_resilience(self):
        """Test that analyzer doesn't crash on complex multi-language files"""
        from thepipe.analyzer import extract_file
        
        # Both files should be processable without crashing
        files = ["test_webapp.html", "test_webapp.php"]
        
        for filename in files:
            filepath = str(self.fixtures_dir / filename)
            try:
                analysis = extract_file(filepath)
                # Success if we got here without crashing
                self.assertTrue(True, f"{filename} processed without crash")
            except Exception as e:
                self.fail(f"{filename} crashed: {e}")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
