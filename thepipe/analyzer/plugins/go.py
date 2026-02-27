from pathlib import Path
from typing import Dict, List, Optional, Any
import re
from .base import LanguagePlugin, PluginConfig
from ..types import DependencyEdge

class GoPlugin(LanguagePlugin):
    @property
    def extensions(self) -> List[str]:
        return ['.go']

    @property
    def manifest_files(self) -> List[str]:
        return ['go.mod', 'go.work']

    @property
    def import_queries(self) -> str:
        return """
        (import_spec path: (interpreted_string_literal) @import)
        """

    def parse_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        """Parse go.mod/go.work for module and workspace metadata."""
        data: Dict[str, Any] = {
            "module_name": None,
            "workspace_uses": [],
        }
        
        try:
            content = manifest_path.read_text()
            if manifest_path.name == "go.mod":
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith("module "):
                        parts = line.split()
                        if len(parts) >= 2:
                            data["module_name"] = parts[1].strip()
                        break
            elif manifest_path.name == "go.work":
                in_use_block = False
                for raw_line in content.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("//"):
                        continue
                    
                    if line.startswith("use ("):
                        in_use_block = True
                        continue
                    if in_use_block and line == ")":
                        in_use_block = False
                        continue
                    if in_use_block:
                        data["workspace_uses"].append(line.strip("./"))
                    elif line.startswith("use "):
                        path = line.split(" ", 1)[1].strip()
                        data["workspace_uses"].append(path.strip("./"))
        except Exception:
            pass
        
        return data
    
    def _nearest_manifest_data(
        self,
        config: PluginConfig,
        from_file: Path,
    ) -> Dict[str, Any]:
        """Find manifest data nearest to the importing file."""
        bucket = config.context.get("go", {})
        manifests = bucket.get("manifests", [])
        if not manifests:
            return bucket
        
        best_depth: Optional[int] = None
        best_data: Dict[str, Any] = {}
        
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
                best_data = entry.get("data", {})
        
        return best_data or bucket
    
    def _resolve_local_package(self, target: Path) -> Optional[Path]:
        """Resolve a Go package target to a concrete file."""
        if target.is_file():
            return target
        
        if target.suffix == "" and target.with_suffix(".go").is_file():
            return target.with_suffix(".go")
        
        if target.is_dir():
            main_file = target / "main.go"
            if main_file.is_file():
                return main_file
            
            go_files = sorted(target.glob("*.go"))
            non_tests = [p for p in go_files if not p.name.endswith("_test.go")]
            if non_tests:
                return non_tests[0]
            if go_files:
                return go_files[0]
        
        return None

    def resolve_import(
        self, 
        import_stmt: str, 
        from_file: Path, 
        config: PluginConfig
    ) -> Optional[DependencyEdge]:
        match = re.search(r'"([^"]+)"', import_stmt)
        if not match:
            return None
        
        import_path = match.group(1).strip()
        if not import_path:
            return None
        
        # Relative import (rare in modern Go but still resolvable).
        if import_path.startswith(("./", "../")):
            target = (from_file.parent / import_path).resolve()
            resolved = self._resolve_local_package(target)
            if resolved:
                try:
                    to_file = str(resolved.relative_to(config.project_root))
                except ValueError:
                    to_file = str(resolved)
                return DependencyEdge(
                    from_file=str(from_file),
                    to_file=to_file,
                    import_statement=import_stmt.strip(),
                    is_external=False,
                )
            return DependencyEdge(
                from_file=str(from_file),
                to_file=import_path,
                import_statement=import_stmt.strip(),
                is_external=False,
            )
        
        manifest_data = self._nearest_manifest_data(config, from_file)
        module_name = manifest_data.get("module_name")
        
        # Local module import: module/path/subpkg
        if module_name and (import_path == module_name or import_path.startswith(module_name + "/")):
            suffix = import_path[len(module_name):].lstrip("/")
            target = config.project_root / suffix if suffix else config.project_root
            resolved = self._resolve_local_package(target)
            if resolved:
                try:
                    to_file = str(resolved.relative_to(config.project_root))
                except ValueError:
                    to_file = str(resolved)
            else:
                to_file = suffix or "."
            
            return DependencyEdge(
                from_file=str(from_file),
                to_file=to_file,
                import_statement=import_stmt.strip(),
                is_external=False,
            )
        
        # If the import path maps to an existing folder in repo, treat it as internal.
        direct_target = config.project_root / import_path
        resolved_direct = self._resolve_local_package(direct_target)
        if resolved_direct:
            try:
                to_file = str(resolved_direct.relative_to(config.project_root))
            except ValueError:
                to_file = str(resolved_direct)
            return DependencyEdge(
                from_file=str(from_file),
                to_file=to_file,
                import_statement=import_stmt.strip(),
                is_external=False,
            )
        
        # Standard library packages usually have no dot in their first segment.
        first_segment = import_path.split("/", 1)[0]
        is_external = True
        if "." not in first_segment and module_name and import_path.startswith(module_name):
            is_external = False
        
        return DependencyEdge(
            from_file=str(from_file),
            to_file=import_path,
            import_statement=import_stmt.strip(),
            is_external=is_external,
        )
