from pathlib import Path
from typing import Dict, List, Optional, Any
import re
import tomllib
from .base import LanguagePlugin, PluginConfig
from ..types import DependencyEdge

class RustPlugin(LanguagePlugin):
    @property
    def extensions(self) -> List[str]:
        return ['.rs']

    @property
    def manifest_files(self) -> List[str]:
        return ['Cargo.toml']

    @property
    def import_queries(self) -> str:
        return """
        (use_declaration) @import
        (extern_crate_declaration) @import
        """

    def parse_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        """Parse Cargo.toml for workspace members and dependencies."""
        data: Dict[str, Any] = {
            "members": [],
            "dependencies": {},
            "package_name": None,
        }
        
        try:
            parsed = tomllib.loads(manifest_path.read_text())
            workspace = parsed.get("workspace", {})
            package = parsed.get("package", {})
            
            members = workspace.get("members", [])
            if isinstance(members, list):
                data["members"] = [str(m) for m in members]
            
            package_name = package.get("name")
            if isinstance(package_name, str):
                data["package_name"] = package_name
            
            deps: Dict[str, Any] = {}
            for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                section_data = parsed.get(section, {})
                if isinstance(section_data, dict):
                    deps.update(section_data)
            data["dependencies"] = deps
        except Exception:
            pass
        
        return data
    
    def _nearest_cargo_manifest(
        self,
        config: PluginConfig,
        from_file: Path,
    ) -> Optional[Path]:
        """Find the nearest Cargo.toml manifest for the source file."""
        bucket = config.context.get("rust", {})
        manifests = bucket.get("manifests", [])
        
        best_path: Optional[Path] = None
        best_depth: Optional[int] = None
        
        for entry in manifests:
            manifest_path = Path(entry.get("path", ""))
            manifest_dir = manifest_path.parent
            try:
                rel = from_file.resolve().relative_to(manifest_dir.resolve())
                depth = len(rel.parts)
            except Exception:
                continue
            
            if best_depth is None or depth < best_depth:
                best_depth = depth
                best_path = manifest_path
        
        return best_path
    
    def _resolve_module_in_src(self, src_root: Path, module_name: str) -> Optional[Path]:
        """Resolve a Rust module name inside a crate src directory."""
        candidates = [
            src_root / f"{module_name}.rs",
            src_root / module_name / "mod.rs",
            src_root / module_name / "lib.rs",
            src_root / module_name / "main.rs",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None
    
    def _to_relative(self, path: Path, project_root: Path) -> str:
        """Convert path to project-relative string when possible."""
        try:
            return str(path.resolve().relative_to(project_root.resolve()))
        except Exception:
            return str(path.resolve())
    
    def _resolve_workspace_crate(
        self,
        crate_name: str,
        config: PluginConfig,
    ) -> Optional[Path]:
        """
        Resolve workspace crate name to a representative source file.
        
        Supports crate naming differences between Cargo ("my-crate")
        and Rust module paths ("my_crate").
        """
        bucket = config.context.get("rust", {})
        manifests = bucket.get("manifests", [])
        
        for entry in manifests:
            manifest_path = Path(entry.get("path", ""))
            data = entry.get("data", {})
            package_name = data.get("package_name")
            if not package_name:
                continue
            
            canonical = str(package_name).replace("-", "_")
            if canonical != crate_name:
                continue
            
            crate_root = manifest_path.parent
            for candidate in (
                crate_root / "src" / "lib.rs",
                crate_root / "src" / "main.rs",
            ):
                if candidate.is_file():
                    return candidate
        
        return None

    def resolve_import(
        self, 
        import_stmt: str, 
        from_file: Path, 
        config: PluginConfig
    ) -> Optional[DependencyEdge]:
        stmt = import_stmt.strip()
        if not stmt:
            return None
        
        # mod foo;
        mod_match = re.search(r'\bmod\s+([A-Za-z_]\w*)\s*;', stmt)
        if mod_match:
            mod_name = mod_match.group(1)
            candidates = [
                from_file.parent / f"{mod_name}.rs",
                from_file.parent / mod_name / "mod.rs",
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return DependencyEdge(
                        from_file=str(from_file),
                        to_file=self._to_relative(candidate, config.project_root),
                        import_statement=stmt,
                        import_type="mod",
                        is_external=False,
                    )
            return DependencyEdge(
                from_file=str(from_file),
                to_file=mod_name,
                import_statement=stmt,
                import_type="mod",
                is_external=False,
            )
        
        # extern crate foo;
        extern_match = re.search(r'\bextern\s+crate\s+([A-Za-z_]\w*)', stmt)
        if extern_match:
            crate_name = extern_match.group(1)
            local_crate = self._resolve_workspace_crate(crate_name, config)
            if local_crate:
                return DependencyEdge(
                    from_file=str(from_file),
                    to_file=self._to_relative(local_crate, config.project_root),
                    import_statement=stmt,
                    import_type="extern_crate",
                    is_external=False,
                )
            return DependencyEdge(
                from_file=str(from_file),
                to_file=crate_name,
                import_statement=stmt,
                import_type="extern_crate",
                is_external=True,
            )
        
        # use foo::bar::Baz;
        use_match = re.search(r'\buse\s+([^;]+)', stmt)
        if not use_match:
            return None
        
        use_path = use_match.group(1).strip()
        use_path = re.sub(r'\s+as\s+\w+$', '', use_path).strip()
        if use_path.startswith("pub "):
            use_path = use_path[4:].strip()
        
        if use_path.startswith(("std::", "core::", "alloc::")):
            root_mod = use_path.split("::", 1)[0]
            return DependencyEdge(
                from_file=str(from_file),
                to_file=root_mod,
                import_statement=stmt,
                import_type="use",
                is_external=True,
            )
        
        # crate::<module>::...
        if use_path.startswith("crate::"):
            root_mod = use_path[len("crate::"):].split("::")[0]
            cargo_manifest = self._nearest_cargo_manifest(config, from_file)
            if cargo_manifest:
                src_root = cargo_manifest.parent / "src"
                resolved = self._resolve_module_in_src(src_root, root_mod)
                if resolved:
                    return DependencyEdge(
                        from_file=str(from_file),
                        to_file=self._to_relative(resolved, config.project_root),
                        import_statement=stmt,
                        import_type="use",
                        is_external=False,
                    )
            return DependencyEdge(
                from_file=str(from_file),
                to_file=root_mod,
                import_statement=stmt,
                import_type="use",
                is_external=False,
            )
        
        # self::<module> / super::<module>
        if use_path.startswith(("self::", "super::")):
            segments = use_path.split("::")
            cursor = from_file.parent
            
            while segments and segments[0] == "super":
                cursor = cursor.parent
                segments = segments[1:]
            
            if segments and segments[0] == "self":
                segments = segments[1:]
            
            if segments:
                root_mod = segments[0]
                candidates = [
                    cursor / f"{root_mod}.rs",
                    cursor / root_mod / "mod.rs",
                ]
                for candidate in candidates:
                    if candidate.is_file():
                        return DependencyEdge(
                            from_file=str(from_file),
                            to_file=self._to_relative(candidate, config.project_root),
                            import_statement=stmt,
                            import_type="use",
                            is_external=False,
                        )
        
        # Ambiguous absolute path: try local module first, then workspace crate.
        root_mod = use_path.split("::", 1)[0]
        cargo_manifest = self._nearest_cargo_manifest(config, from_file)
        if cargo_manifest:
            resolved_local = self._resolve_module_in_src(cargo_manifest.parent / "src", root_mod)
            if resolved_local:
                return DependencyEdge(
                    from_file=str(from_file),
                    to_file=self._to_relative(resolved_local, config.project_root),
                    import_statement=stmt,
                    import_type="use",
                    is_external=False,
                )
        
        workspace_crate = self._resolve_workspace_crate(root_mod, config)
        if workspace_crate:
            return DependencyEdge(
                from_file=str(from_file),
                to_file=self._to_relative(workspace_crate, config.project_root),
                import_statement=stmt,
                import_type="use",
                is_external=False,
            )
        
        return DependencyEdge(
            from_file=str(from_file),
            to_file=root_mod,
            import_statement=stmt,
            import_type="use",
            is_external=True,
        )
