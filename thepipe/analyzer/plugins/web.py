from pathlib import Path
from typing import Dict, List, Optional, Any, Set
import json
import re
from .base import LanguagePlugin, PluginConfig
from ..types import DependencyEdge

class WebStackPlugin(LanguagePlugin):
    _MODULE_EXTENSIONS = (
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".vue", ".svelte", ".html", ".htm", ".css", ".scss", ".sass",
        ".json",
    )
    
    @property
    def extensions(self) -> List[str]:
        # Include web surface formats; AST queries are gated per language.
        return ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte', '.html', '.htm']

    @property
    def import_query_languages(self) -> Optional[Set[str]]:
        # JS/TS queries only; Vue/Svelte/HTML require language injection.
        # TODO: Add Vue/Svelte language injection so we can extract script imports.
        return {"javascript", "typescript", "tsx"}
    
    @property
    def context_keys(self) -> List[str]:
        return ["web", "javascript", "typescript", "webstack"]

    @property
    def manifest_files(self) -> List[str]:
        return ['package.json', 'tsconfig.json', 'jsconfig.json']

    @property
    def import_queries(self) -> str:
        # Prefer capturing the literal import path directly when tree-sitter can
        # see it. Resolver regex remains as a fallback for more complex forms.
        return """
        (import_statement source: (string) @import)
        (export_statement source: (string) @import)
        (call_expression
          function: (identifier) @func (#eq? @func "require")
          arguments: (arguments (string) @import)
        )
        (call_expression
          function: (import)
          arguments: (arguments (string) @import)
        )
        """

    def parse_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        """Parse package and TS config manifests for import resolution context."""
        data: Dict[str, Any] = {"manifest_dir": str(manifest_path.parent)}
        
        try:
            if manifest_path.name == 'package.json':
                pkg = json.loads(manifest_path.read_text())
                data['package_name'] = pkg.get('name')
                data['main'] = pkg.get('main')
                data['module'] = pkg.get('module')
                data['types'] = pkg.get('types')
                data['source'] = pkg.get('source')
                
                workspaces = pkg.get('workspaces', [])
                if isinstance(workspaces, dict):
                    workspaces = workspaces.get('packages', [])
                if not isinstance(workspaces, list):
                    workspaces = []
                data['workspaces'] = workspaces
                data['workspace_packages'] = {}
                
                for pattern in workspaces:
                    if not isinstance(pattern, str):
                        continue
                    for match in manifest_path.parent.glob(pattern):
                        pkg_json = match / "package.json"
                        if not pkg_json.is_file():
                            continue
                        try:
                            nested_pkg = json.loads(pkg_json.read_text())
                            nested_name = nested_pkg.get("name")
                            if nested_name:
                                data['workspace_packages'][nested_name] = str(match)
                        except Exception:
                            continue
            
            elif manifest_path.name.endswith('config.json'):
                # Handle tsconfig paths
                cfg = json.loads(manifest_path.read_text())
                compiler = cfg.get('compilerOptions', {})
                data['base_url'] = compiler.get('baseUrl', '')
                data['paths'] = compiler.get('paths', {})
        except Exception:
            pass
        return data
    
    def _to_relative(self, path: Path, project_root: Path) -> str:
        """Convert path to project-relative string if possible."""
        try:
            return str(path.resolve().relative_to(project_root.resolve()))
        except Exception:
            return str(path.resolve())
    
    def _resolve_module_candidate(self, base: Path) -> Optional[Path]:
        """Resolve JS/TS module path by trying common extensions and index files."""
        candidates: List[Path] = []
        
        if base.suffix:
            candidates.append(base)
        else:
            candidates.append(base)
            for ext in self._MODULE_EXTENSIONS:
                candidates.append(base.with_suffix(ext))
        
        if base.is_dir():
            for ext in self._MODULE_EXTENSIONS:
                candidates.append(base / f"index{ext}")
        
        for ext in self._MODULE_EXTENSIONS:
            candidates.append(Path(str(base) + ext))
        
        for ext in self._MODULE_EXTENSIONS:
            candidates.append(Path(str(base) + f"/index{ext}"))
        
        seen = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return candidate
        
        return None
    
    def _iter_manifest_entries(self, config: PluginConfig) -> List[Dict[str, Any]]:
        """Yield manifest entries from all supported web context keys."""
        entries: List[Dict[str, Any]] = []
        seen = set()
        
        for key in self.context_keys:
            bucket = config.context.get(key, {})
            for entry in bucket.get("manifests", []):
                path = entry.get("path")
                if not path or path in seen:
                    continue
                seen.add(path)
                entries.append(entry)
        
        return entries
    
    def _split_package_import(self, import_path: str) -> tuple[str, str]:
        """
        Split package import into package name and subpath.
        
        Examples:
            react -> (react, "")
            lodash/fp -> (lodash, fp)
            @scope/pkg/utils -> (@scope/pkg, utils)
        """
        if import_path.startswith("@"):
            parts = import_path.split("/")
            if len(parts) >= 2:
                package_name = "/".join(parts[:2])
                subpath = "/".join(parts[2:])
                return package_name, subpath
            return import_path, ""
        
        parts = import_path.split("/", 1)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1]
    
    def _resolve_alias_import(
        self,
        import_path: str,
        config: PluginConfig,
    ) -> Optional[Path]:
        """Resolve tsconfig/jsconfig alias paths."""
        for entry in self._iter_manifest_entries(config):
            data = entry.get("data", {})
            paths_map = data.get("paths", {})
            if not isinstance(paths_map, dict):
                continue
            
            base_dir = Path(data.get("manifest_dir", ""))
            base_url = data.get("base_url") or ""
            search_root = (base_dir / base_url).resolve()
            
            for alias, targets in paths_map.items():
                target_values = targets if isinstance(targets, list) else [targets]
                
                wildcard_value = ""
                if "*" in alias:
                    prefix, suffix = alias.split("*", 1)
                    if not import_path.startswith(prefix):
                        continue
                    if suffix and not import_path.endswith(suffix):
                        continue
                    end = len(import_path) - len(suffix) if suffix else len(import_path)
                    wildcard_value = import_path[len(prefix):end]
                elif import_path != alias:
                    continue
                
                for target in target_values:
                    if not isinstance(target, str):
                        continue
                    resolved_target = target.replace("*", wildcard_value)
                    candidate = (search_root / resolved_target).resolve()
                    resolved = self._resolve_module_candidate(candidate)
                    if resolved:
                        return resolved
        
        return None
    
    def _resolve_workspace_import(
        self,
        import_path: str,
        config: PluginConfig,
    ) -> Optional[Path]:
        """Resolve imports that point to local workspace packages."""
        package_name, subpath = self._split_package_import(import_path)
        
        for entry in self._iter_manifest_entries(config):
            data = entry.get("data", {})
            workspace_packages = data.get("workspace_packages", {})
            
            if isinstance(workspace_packages, dict) and package_name in workspace_packages:
                package_root = Path(workspace_packages[package_name]).resolve()
                if subpath:
                    resolved = self._resolve_module_candidate(package_root / subpath)
                    if resolved:
                        return resolved
                else:
                    for field in ("source", "module", "main", "types"):
                        value = data.get(field)
                        if isinstance(value, str):
                            resolved = self._resolve_module_candidate(package_root / value)
                            if resolved:
                                return resolved
                    for fallback in ("src/index", "index"):
                        resolved = self._resolve_module_candidate(package_root / fallback)
                        if resolved:
                            return resolved
            
            package_declared = data.get("package_name")
            manifest_dir = data.get("manifest_dir")
            if package_declared == package_name and manifest_dir:
                package_root = Path(manifest_dir).resolve()
                if subpath:
                    resolved = self._resolve_module_candidate(package_root / subpath)
                    if resolved:
                        return resolved
                else:
                    for field in ("source", "module", "main", "types"):
                        value = data.get(field)
                        if isinstance(value, str):
                            resolved = self._resolve_module_candidate(package_root / value)
                            if resolved:
                                return resolved
                    for fallback in ("src/index", "index"):
                        resolved = self._resolve_module_candidate(package_root / fallback)
                        if resolved:
                            return resolved
        
        return None

    def resolve_import(
        self, 
        import_stmt: str, 
        from_file: Path, 
        config: PluginConfig
    ) -> Optional[DependencyEdge]:
        import_stmt = import_stmt.strip()
        match = None
        if import_stmt and import_stmt[0] in {"'", '"', "`"}:
            match = re.search(r'^([\"\'`])([^\"\'`]+)\1', import_stmt)
        else:
            # Handle ES module import/export statements.
            match = re.search(r'\bfrom\s+([\"\'`])([^\"\'`]+)\1', import_stmt)
            if not match:
                match = re.search(r'\bimport\s+([\"\'`])([^\"\'`]+)\1', import_stmt)
            if not match:
                # Handle require()/dynamic import() calls.
                match = re.search(r'^(?:require|import)\s*\(\s*([\"\'`])([^\"\'`]+)\1', import_stmt)
        if not match:
            return None
        
        import_path = match.group(2).strip()
        if not import_path:
            return None
        
        # Relative or project-absolute path import.
        if import_path.startswith((".", "/")):
            if import_path.startswith("/"):
                candidate = config.project_root / import_path.lstrip("/")
            else:
                candidate = from_file.parent / import_path
            resolved = self._resolve_module_candidate(candidate.resolve())
            if resolved:
                return DependencyEdge(
                    from_file=str(from_file),
                    to_file=self._to_relative(resolved, config.project_root),
                    import_statement=import_stmt.strip(),
                    import_type="import",
                    is_external=False,
                )
            
            return DependencyEdge(
                from_file=str(from_file),
                to_file=import_path,
                import_statement=import_stmt.strip(),
                import_type="import",
                is_external=False,
            )
        
        alias_target = self._resolve_alias_import(import_path, config)
        if alias_target:
            return DependencyEdge(
                from_file=str(from_file),
                to_file=self._to_relative(alias_target, config.project_root),
                import_statement=import_stmt.strip(),
                import_type="import",
                is_external=False,
            )
        
        workspace_target = self._resolve_workspace_import(import_path, config)
        if workspace_target:
            return DependencyEdge(
                from_file=str(from_file),
                to_file=self._to_relative(workspace_target, config.project_root),
                import_statement=import_stmt.strip(),
                import_type="import",
                is_external=False,
            )
        
        return DependencyEdge(
            from_file=str(from_file),
            to_file=import_path,
            import_statement=import_stmt.strip(),
            import_type="import",
            is_external=True,
        )
