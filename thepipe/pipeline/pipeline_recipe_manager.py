# thepipe/pipeline/pipeline_recipe_manager.py
"""
Pipeline recipe management with manifest auto-discovery
Recipes are the executable units that run through the pipeline system
Uses negative space programming - what's NOT there is as important as what is
"""

import os
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import shutil
import tempfile

from ..core import Chunk


class PipelineRecipeManager:
    """Manages pipeline recipes with auto-discovery and minimal configuration"""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.recipes_dir = Path(__file__).parent / "recipes"
        self.manifest_path = self.recipes_dir / "manifest.json"
        self.global_venv_dir = Path.home() / ".thepipe" / "venvs"
        
        # Ensure structure exists
        self.recipes_dir.mkdir(parents=True, exist_ok=True)
        self.global_venv_dir.mkdir(parents=True, exist_ok=True)
        
        # Load or create manifest
        self.manifest = self._load_or_create_manifest()
        
        # Auto-discover recipes not in manifest
        if self.manifest.get("global_settings", {}).get("auto_discover", True):
            self._auto_discover_recipes()
    
    def _load_or_create_manifest(self) -> Dict[str, Any]:
        """Load manifest or create default if missing"""
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path) as f:
                    return json.load(f)
            except Exception as e:
                if self.verbose:
                    print(f"[thepipe] Corrupt manifest, creating new: {e}")
        
        # Create default manifest
        default_manifest = {
            "pipeline_recipes": {},
            "global_settings": {
                "venv_location": str(self.global_venv_dir),
                "default_storage": "local",
                "auto_discover": True,
                "cleanup_venvs": True
            },
            "version": "1.0.0",
            "last_updated": datetime.now().isoformat()
        }
        
        self._save_manifest(default_manifest)
        return default_manifest
    
    def _save_manifest(self, manifest: Dict[str, Any]):
        """Save manifest with timestamp update"""
        manifest["last_updated"] = datetime.now().isoformat()
        with open(self.manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        self.manifest = manifest
    
    def _auto_discover_recipes(self):
        """Auto-discover recipe folders not in manifest"""
        discovered = []
        
        for item in self.recipes_dir.iterdir():
            if item.is_dir() and item.name not in self.manifest["pipeline_recipes"]:
                recipe_file = item / "recipe.yaml"
                if recipe_file.exists():
                    try:
                        recipe_config = self._load_recipe_config(item.name)
                        if recipe_config:
                            discovered.append(item.name)
                            if self.verbose:
                                print(f"[thepipe] Auto-discovered pipeline recipe: {item.name}")
                    except Exception as e:
                        if self.verbose:
                            print(f"[thepipe] Error discovering {item.name}: {e}")
        
        if discovered:
            if self.verbose:
                print(f"[thepipe] Auto-discovered {len(discovered)} new pipeline recipes")
            self._save_manifest(self.manifest)
    
    def register_recipe(self, recipe_name: str, recipe_spec: Dict[str, Any], 
                       source_files: Optional[Dict[str, str]] = None) -> bool:
        """Register a new pipeline recipe"""
        try:
            recipe_dir = self.recipes_dir / recipe_name
            recipe_dir.mkdir(exist_ok=True)
            
            # Create recipe.yaml
            recipe_config = {
                "name": recipe_spec.get("metadata", {}).get("name", recipe_name),
                "version": "1.0.0",
                "description": recipe_spec.get("metadata", {}).get("description", ""),
                "created": datetime.now().isoformat(),
                "default_targets": self._extract_default_targets(recipe_spec),
                "dependencies": self._extract_dependencies(recipe_spec),
                "expected_inputs": ["any"],  # Minimal assumption
                "outputs": self._extract_outputs(recipe_spec)
            }
            
            with open(recipe_dir / "recipe.yaml", 'w') as f:
                yaml.dump(recipe_config, f, default_flow_style=False)
            
            # Save pipeline files
            if "pipeline_yaml" in recipe_spec:
                (recipe_dir / "pipeline.yaml").write_text(recipe_spec["pipeline_yaml"])
            
            if "client_files" in recipe_spec:
                for filename, content in recipe_spec["client_files"].items():
                    (recipe_dir / filename).write_text(content)
            
            # Save source files if provided
            if source_files:
                source_dir = recipe_dir / "source"
                source_dir.mkdir(exist_ok=True)
                for filename, content in source_files.items():
                    (source_dir / filename).write_text(content)
            
            # Create minimal README
            readme_content = f"""# {recipe_config['name']}

{recipe_config['description']}

## Pipeline Usage
```bash
thepipe <data_source> --pipeline deploy {recipe_name}
```

Created: {recipe_config['created']}
"""
            (recipe_dir / "README.md").write_text(readme_content)
            
            # Update manifest
            self.manifest["pipeline_recipes"][recipe_name] = recipe_config
            self._save_manifest(self.manifest)
            
            if self.verbose:
                print(f"[thepipe] Pipeline recipe '{recipe_name}' registered successfully")
            
            return True
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Failed to register pipeline recipe '{recipe_name}': {e}")
            return False
    
    def get_recipe(self, recipe_name: str) -> Optional[Dict[str, Any]]:
        """Get pipeline recipe specification"""
        if recipe_name not in self.manifest["pipeline_recipes"]:
            # Try auto-discovery
            self._auto_discover_recipes()
            if recipe_name not in self.manifest["pipeline_recipes"]:
                return None
        
        recipe_dir = self.recipes_dir / recipe_name
        if not recipe_dir.exists():
            return None
        
        try:
            # Load recipe config
            recipe_config = self._load_recipe_config(recipe_name)
            
            # Load pipeline files
            recipe_spec = {"metadata": recipe_config}
            
            pipeline_file = recipe_dir / "pipeline.yaml"
            if pipeline_file.exists():
                recipe_spec["pipeline_yaml"] = pipeline_file.read_text()
            
            clients_file = recipe_dir / "clients.py"
            if clients_file.exists():
                recipe_spec["client_files"] = {"clients.py": clients_file.read_text()}
            
            # Load source files
            source_dir = recipe_dir / "source"
            if source_dir.exists():
                recipe_spec["source_files"] = {}
                for source_file in source_dir.rglob("*"):
                    if source_file.is_file():
                        rel_path = source_file.relative_to(source_dir)
                        recipe_spec["source_files"][str(rel_path)] = source_file.read_text()
            
            return recipe_spec
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error loading pipeline recipe '{recipe_name}': {e}")
            return None
    
    def list_recipes(self) -> List[Dict[str, Any]]:
        """List all available pipeline recipes"""
        # Auto-discover first
        self._auto_discover_recipes()
        
        recipes = []
        for recipe_name, recipe_info in self.manifest["pipeline_recipes"].items():
            recipe_dir = self.recipes_dir / recipe_name
            if recipe_dir.exists():
                recipes.append({
                    "name": recipe_name,
                    "display_name": recipe_info.get("name", recipe_name),
                    "description": recipe_info.get("description", ""),
                    "version": recipe_info.get("version", "unknown"),
                    "created": recipe_info.get("created", ""),
                    "default_targets": recipe_info.get("default_targets", {}),
                    "path": str(recipe_dir)
                })
        
        return sorted(recipes, key=lambda x: x["created"], reverse=True)
    
    def get_recipe_target(self, recipe_name: str, target_override: Optional[str] = None) -> Optional[str]:
        """Get deployment target for recipe (override or default)"""
        if target_override:
            return target_override
        
        recipe_config = self._load_recipe_config(recipe_name)
        if not recipe_config:
            return None
        
        # Use first available default target
        default_targets = recipe_config.get("default_targets", {})
        if default_targets:
            # Prefer 'dev' then 'prod' then first available
            for env in ["dev", "development", "prod", "production"]:
                if env in default_targets:
                    return default_targets[env]
            
            # Return first available
            return next(iter(default_targets.values()))
        
        return None
    
    def get_venv_path(self, recipe_name: str) -> Path:
        """Get virtual environment path for pipeline recipe"""
        return self.global_venv_dir / recipe_name
    
    def cleanup_venv(self, recipe_name: str) -> bool:
        """Clean up virtual environment for pipeline recipe"""
        venv_path = self.get_venv_path(recipe_name)
        if venv_path.exists():
            try:
                shutil.rmtree(venv_path)
                if self.verbose:
                    print(f"[thepipe] Cleaned up venv for pipeline recipe {recipe_name}")
                return True
            except Exception as e:
                if self.verbose:
                    print(f"[thepipe] Failed to cleanup venv: {e}")
        return False
    
    def package_recipe(self, recipe_name: str, output_path: str) -> bool:
        """Package pipeline recipe for easy sharing (drag-and-drop)"""
        try:
            recipe_dir = self.recipes_dir / recipe_name
            if not recipe_dir.exists():
                return False
            
            output_file = Path(output_path)
            
            if output_file.suffix == '.zip':
                # Create ZIP package
                import zipfile
                with zipfile.ZipFile(output_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for file_path in recipe_dir.rglob("*"):
                        if file_path.is_file():
                            arcname = file_path.relative_to(recipe_dir.parent)
                            zipf.write(file_path, arcname)
            else:
                # Create directory copy
                if output_file.exists():
                    shutil.rmtree(output_file)
                shutil.copytree(recipe_dir, output_file / recipe_name)
            
            if self.verbose:
                print(f"[thepipe] Pipeline recipe '{recipe_name}' packaged to {output_path}")
            
            return True
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Failed to package pipeline recipe: {e}")
            return False
    
    def import_recipe(self, package_path: str) -> Optional[str]:
        """Import a packaged pipeline recipe (drag-and-drop)"""
        try:
            package_file = Path(package_path)
            
            if package_file.suffix == '.zip':
                # Extract ZIP package
                import zipfile
                with tempfile.TemporaryDirectory() as temp_dir:
                    with zipfile.ZipFile(package_file, 'r') as zipf:
                        zipf.extractall(temp_dir)
                    
                    # Find recipe directory
                    temp_path = Path(temp_dir)
                    recipe_dirs = [d for d in temp_path.iterdir() if d.is_dir() and (d / "recipe.yaml").exists()]
                    
                    if not recipe_dirs:
                        if self.verbose:
                            print("[thepipe] No valid pipeline recipe found in package")
                        return None
                    
                    recipe_dir = recipe_dirs[0]
                    recipe_name = recipe_dir.name
                    
                    # Copy to recipes directory
                    target_dir = self.recipes_dir / recipe_name
                    if target_dir.exists():
                        if self.verbose:
                            print(f"[thepipe] Pipeline recipe '{recipe_name}' already exists, skipping")
                        return recipe_name
                    
                    shutil.copytree(recipe_dir, target_dir)
            
            elif package_file.is_dir():
                # Copy directory
                recipe_name = package_file.name
                target_dir = self.recipes_dir / recipe_name
                
                if target_dir.exists():
                    if self.verbose:
                        print(f"[thepipe] Pipeline recipe '{recipe_name}' already exists, skipping")
                    return recipe_name
                
                shutil.copytree(package_file, target_dir)
            
            else:
                if self.verbose:
                    print("[thepipe] Unsupported package format")
                return None
            
            # Auto-discover the new recipe
            self._auto_discover_recipes()
            
            if self.verbose:
                print(f"[thepipe] Pipeline recipe '{recipe_name}' imported successfully")
            
            return recipe_name
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Failed to import pipeline recipe: {e}")
            return None
    
    def _load_recipe_config(self, recipe_name: str) -> Optional[Dict[str, Any]]:
        """Load recipe.yaml configuration"""
        recipe_file = self.recipes_dir / recipe_name / "recipe.yaml"
        if not recipe_file.exists():
            return None
        
        try:
            with open(recipe_file) as f:
                config = yaml.safe_load(f)
                
            # Update manifest if recipe was auto-discovered
            if recipe_name not in self.manifest["pipeline_recipes"]:
                self.manifest["pipeline_recipes"][recipe_name] = config
                self._save_manifest(self.manifest)
            
            return config
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error loading pipeline recipe config: {e}")
            return None
    
    def _extract_default_targets(self, recipe_spec: Dict[str, Any]) -> Dict[str, str]:
        """Extract default deployment targets from recipe spec"""
        targets = {}
        
        # Look for database connections in clients
        for client in recipe_spec.get("clients", []):
            connection_uri = client.get("connection_uri", "")
            db_type = client.get("db_type", "").lower()
            
            if "localhost" in connection_uri or "127.0.0.1" in connection_uri:
                targets["dev"] = connection_uri
            elif db_type == "duckdb":
                targets["dev"] = connection_uri
            else:
                targets["prod"] = connection_uri
        
        # Default fallback
        if not targets:
            targets["dev"] = "duckdb:///dev.db"
        
        return targets
    
    def _extract_dependencies(self, recipe_spec: Dict[str, Any]) -> List[str]:
        """Extract Python dependencies from recipe spec"""
        deps = ["ploomber", "pandas"]
        
        # Add database-specific dependencies
        for client in recipe_spec.get("clients", []):
            db_type = client.get("db_type", "").lower()
            if db_type == "postgresql":
                deps.append("psycopg2-binary")
            elif db_type == "mysql":
                deps.append("pymysql")
            elif db_type == "duckdb":
                deps.append("duckdb")
        
        return list(set(deps))  # Remove duplicates
    
    def _extract_outputs(self, recipe_spec: Dict[str, Any]) -> List[str]:
        """Extract expected outputs from recipe spec"""
        outputs = []
        
        for task in recipe_spec.get("tasks", []):
            product = task.get("product_name", "")
            if product:
                outputs.append(product)
        
        return outputs if outputs else ["processed_data"]
    
    def export_recipe(self, recipe_name: str, export_path: str) -> bool:
        """
        Export a pipeline recipe to a directory for sharing or deployment
        
        Args:
            recipe_name: Name of pipeline recipe to export
            export_path: Directory to export to
            
        Returns:
            True if export successful
        """
        try:
            recipe_spec = self.get_recipe(recipe_name)
            if not recipe_spec:
                return False
            
            export_dir = Path(export_path)
            export_dir.mkdir(parents=True, exist_ok=True)
            
            # Export main specification
            spec_file = export_dir / "pipeline_spec.json"
            with open(spec_file, 'w') as f:
                json.dump(recipe_spec, f, indent=2)
            
            # Export Ploomber files
            if "pipeline_yaml" in recipe_spec:
                yaml_file = export_dir / "pipeline.yaml"
                yaml_file.write_text(recipe_spec["pipeline_yaml"])
            
            if "client_files" in recipe_spec:
                for filename, content in recipe_spec["client_files"].items():
                    file_path = export_dir / filename
                    file_path.write_text(content)
            
            # Create README
            readme_content = f"""# Pipeline Recipe: {recipe_name}

{recipe_spec.get('metadata', {}).get('description', 'No description available')}

## Usage

1. Install Ploomber: `pip install ploomber`
2. Run pipeline: `ploomber build`

## Files

- `pipeline.yaml`: Main pipeline specification
- `clients.py`: Database client configurations  
- `pipeline_spec.json`: Complete pipeline metadata

Created: {recipe_spec.get('metadata', {}).get('created', 'Unknown')}
"""
            
            readme_file = export_dir / "README.md"
            readme_file.write_text(readme_content)
            
            if self.verbose:
                print(f"[thepipe] Pipeline recipe '{recipe_name}' exported to {export_path}")
            
            return True
            
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error exporting pipeline recipe: {e}")
            return False
