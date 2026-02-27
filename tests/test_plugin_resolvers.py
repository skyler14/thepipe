"""
Tests for plugin-based import resolution.
"""

from types import SimpleNamespace
from pathlib import Path

from thepipe.analyzer.plugins import register_builtin_plugins
from thepipe.analyzer.plugins.base import PluginConfig
from thepipe.analyzer.plugins.go import GoPlugin
from thepipe.analyzer.plugins.rust import RustPlugin
from thepipe.analyzer.plugins.web import WebStackPlugin
from thepipe.analyzer.resolvers import resolve_dart_import, resolve_swift_import
from thepipe.analyzer.dependency_map import DependencyMapper
from thepipe.analyzer.types import FileAnalysis


def _build_context(key: str, manifest_path: Path, data: dict) -> dict:
    return {
        key: {
            "manifests": [
                {
                    "path": str(manifest_path),
                    "data": data,
                }
            ],
            **data,
        }
    }


def test_go_plugin_resolves_local_module_import(tmp_path):
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/acme\n\ngo 1.22\n")
    
    util_file = tmp_path / "pkg" / "util" / "util.go"
    util_file.parent.mkdir(parents=True)
    util_file.write_text("package util\n")
    
    main_file = tmp_path / "cmd" / "app" / "main.go"
    main_file.parent.mkdir(parents=True)
    main_file.write_text("package main\n")
    
    plugin = GoPlugin()
    manifest_data = plugin.parse_manifest(go_mod)
    config = PluginConfig(
        project_root=tmp_path,
        context=_build_context("go", go_mod, manifest_data),
    )
    
    edge = plugin.resolve_import(
        'import "example.com/acme/pkg/util"',
        main_file,
        config,
    )
    
    assert edge is not None
    assert edge.is_external is False
    assert edge.to_file.endswith("pkg/util/util.go")


def test_rust_plugin_resolves_crate_module(tmp_path):
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text(
        "[package]\nname = \"demo-app\"\nversion = \"0.1.0\"\nedition = \"2021\"\n"
    )
    
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.rs").write_text("use crate::utils::helper;\n")
    (src_dir / "utils.rs").write_text("pub fn helper() {}\n")
    
    plugin = RustPlugin()
    manifest_data = plugin.parse_manifest(cargo_toml)
    config = PluginConfig(
        project_root=tmp_path,
        context=_build_context("rust", cargo_toml, manifest_data),
    )
    
    edge = plugin.resolve_import(
        "use crate::utils::helper;",
        src_dir / "main.rs",
        config,
    )
    
    assert edge is not None
    assert edge.is_external is False
    assert edge.to_file.endswith("src/utils.rs")


def test_web_plugin_resolves_tsconfig_alias(tmp_path):
    package_json = tmp_path / "package.json"
    package_json.write_text('{"name":"demo-web"}')
    
    tsconfig = tmp_path / "tsconfig.json"
    tsconfig.write_text(
        '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}'
    )
    
    source_file = tmp_path / "src" / "main.ts"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("import { helper } from '@/lib/helper';\n")
    
    helper_file = tmp_path / "src" / "lib" / "helper.ts"
    helper_file.parent.mkdir(parents=True)
    helper_file.write_text("export const helper = () => 1;\n")
    
    plugin = WebStackPlugin()
    package_data = plugin.parse_manifest(package_json)
    tsconfig_data = plugin.parse_manifest(tsconfig)
    
    context = {
        "web": {
            "manifests": [
                {"path": str(package_json), "data": package_data},
                {"path": str(tsconfig), "data": tsconfig_data},
            ],
            **package_data,
            **tsconfig_data,
        },
        "typescript": {
            "manifests": [
                {"path": str(tsconfig), "data": tsconfig_data},
            ],
            **tsconfig_data,
        },
        "javascript": {
            "manifests": [
                {"path": str(tsconfig), "data": tsconfig_data},
            ],
            **tsconfig_data,
        },
    }
    
    config = PluginConfig(
        project_root=tmp_path,
        context=context,
    )
    
    edge = plugin.resolve_import(
        "import { helper } from '@/lib/helper';",
        source_file,
        config,
    )
    
    assert edge is not None
    assert edge.is_external is False
    assert edge.to_file.endswith("src/lib/helper.ts")


def test_builtin_plugin_registration_is_idempotent():
    manager1 = register_builtin_plugins()
    count_before = len(manager1._registered_plugins)

    manager2 = register_builtin_plugins()

    assert manager1 is manager2
    assert len(manager2._registered_plugins) == count_before


def test_swift_import_strips_attribute_arguments():
    mapper = SimpleNamespace(_file_index={}, repo_root=Path("."))

    edge = resolve_swift_import(
        "@available(iOS 15, *) import Foundation",
        "Sources/App.swift",
        mapper,
    )

    assert edge is not None
    assert edge.is_external is True
    assert edge.to_file == "Foundation"


def test_dart_package_import_uses_mapper_lookup_api(tmp_path):
    class StubMapper:
        repo_root = tmp_path

        def lookup_dart_package_file(self, package_name: str, rel_path: str):
            assert package_name == "my_pkg"
            assert rel_path == "src/foo.dart"
            return "packages/my_pkg/lib/src/foo.dart"

    edge = resolve_dart_import(
        "import 'package:my_pkg/src/foo.dart';",
        "app/lib/main.dart",
        StubMapper(),
    )

    assert edge is not None
    assert edge.is_external is False
    assert edge.to_file == "packages/my_pkg/lib/src/foo.dart"


def test_dependency_mapper_prefers_matching_dart_package_name(tmp_path):
    mapper = DependencyMapper(str(tmp_path))
    files = {
        str(tmp_path / "packages" / "foo" / "lib" / "src" / "widget.dart"): FileAnalysis(
            path="packages/foo/lib/src/widget.dart",
            language="dart",
        ),
        str(tmp_path / "packages" / "bar" / "lib" / "src" / "widget.dart"): FileAnalysis(
            path="packages/bar/lib/src/widget.dart",
            language="dart",
        ),
        str(tmp_path / "vendor" / "bar" / "lib" / "src" / "widget.dart"): FileAnalysis(
            path="vendor/bar/lib/src/widget.dart",
            language="dart",
        ),
    }

    mapper._build_file_index(files)

    match = mapper.lookup_dart_package_file("bar", "src/widget.dart")

    assert match is not None
    assert match.endswith("packages/bar/lib/src/widget.dart")


def test_dependency_mapper_builtin_first_prevents_plugin_shadowing(tmp_path):
    mapper = DependencyMapper(str(tmp_path), enable_plugin_resolvers=False)

    builtin_edge = SimpleNamespace(tag="builtin")
    plugin_edge = SimpleNamespace(tag="plugin")

    mapper._resolve_builtin_import = lambda *args, **kwargs: builtin_edge
    mapper._resolve_plugin_import = lambda *args, **kwargs: plugin_edge
    mapper._resolve_custom_import = lambda *args, **kwargs: None

    edge = mapper._resolve_import("import x", "a.py", "python")

    assert edge is builtin_edge


def test_dependency_mapper_plugin_first_is_opt_in(tmp_path):
    mapper = DependencyMapper(
        str(tmp_path),
        enable_plugin_resolvers=False,
        plugin_resolver_precedence="plugin_first",
    )

    builtin_edge = SimpleNamespace(tag="builtin")
    plugin_edge = SimpleNamespace(tag="plugin")

    mapper._resolve_builtin_import = lambda *args, **kwargs: builtin_edge
    mapper._resolve_plugin_import = lambda *args, **kwargs: plugin_edge
    mapper._resolve_custom_import = lambda *args, **kwargs: None

    edge = mapper._resolve_import("import x", "a.py", "python")

    assert edge is plugin_edge


def test_dependency_mapper_plugin_first_skips_builtin_when_plugin_hits(tmp_path):
    mapper = DependencyMapper(
        str(tmp_path),
        enable_plugin_resolvers=False,
        plugin_resolver_precedence="plugin_first",
    )

    plugin_edge = SimpleNamespace(tag="plugin")

    def fail_builtin(*args, **kwargs):
        raise AssertionError("builtin resolver should not run when plugin returns an edge")

    mapper._resolve_builtin_import = fail_builtin
    mapper._resolve_plugin_import = lambda *args, **kwargs: plugin_edge
    mapper._resolve_custom_import = lambda *args, **kwargs: None

    edge = mapper._resolve_import("import x", "a.py", "python")

    assert edge is plugin_edge


def test_dependency_mapper_stem_collision_stays_unresolved(tmp_path):
    mapper = DependencyMapper(str(tmp_path), enable_plugin_resolvers=False)

    src_utils = tmp_path / "src" / "utils.py"
    test_utils = tmp_path / "tests" / "utils.py"
    src_utils.parent.mkdir(parents=True, exist_ok=True)
    test_utils.parent.mkdir(parents=True, exist_ok=True)
    src_utils.write_text("def src_utils(): pass\n")
    test_utils.write_text("def test_utils(): pass\n")

    files = {
        str(src_utils): FileAnalysis(path="src/utils.py", language="python"),
        str(test_utils): FileAnalysis(path="tests/utils.py", language="python"),
    }
    mapper._build_file_index(files)

    assert "utils" not in mapper._file_index
    assert mapper._lookup_module("utils", "src/main.py", "python") is None
    assert mapper._lookup_module("utils", "tests/main.py", "python") is None


def test_normalize_internal_target_short_circuits_for_indexed_target(tmp_path):
    mapper = DependencyMapper(str(tmp_path), enable_plugin_resolvers=False)
    files = {
        str(tmp_path / "pkg" / "mod.py"): FileAnalysis(path="pkg/mod.py", language="python"),
    }
    mapper._build_file_index(files)

    def fail_absolute(*args, **kwargs):
        raise AssertionError("_absolute_path should not run for indexed target")

    mapper._absolute_path = fail_absolute

    resolved = mapper._normalize_internal_target("pkg/mod.py", "pkg/main.py")
    assert resolved == "pkg/mod.py"


def test_normalize_internal_target_does_not_resolve_unindexed_by_default(tmp_path):
    mapper = DependencyMapper(str(tmp_path), enable_plugin_resolvers=False)
    main_file = tmp_path / "src" / "main.py"
    new_file = tmp_path / "src" / "new.py"
    main_file.parent.mkdir(parents=True, exist_ok=True)
    main_file.write_text("import new\n")
    new_file.write_text("def value():\n    return 1\n")

    files = {
        str(main_file): FileAnalysis(path="src/main.py", language="python"),
    }
    mapper._build_file_index(files)

    resolved = mapper._normalize_internal_target("new.py", "src/main.py")
    assert resolved == "new.py"


def test_normalize_internal_target_can_resolve_unindexed_when_enabled(tmp_path):
    mapper = DependencyMapper(
        str(tmp_path),
        enable_plugin_resolvers=False,
        allow_unindexed_internal_targets=True,
    )
    main_file = tmp_path / "src" / "main.py"
    new_file = tmp_path / "src" / "new.py"
    main_file.parent.mkdir(parents=True, exist_ok=True)
    main_file.write_text("import new\n")
    new_file.write_text("def value():\n    return 1\n")

    files = {
        str(main_file): FileAnalysis(path="src/main.py", language="python"),
    }
    mapper._build_file_index(files)

    resolved = mapper._normalize_internal_target("new.py", "src/main.py")
    assert resolved == "src/new.py"
