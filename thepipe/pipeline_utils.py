# thepipe/pipeline_utils.py
"""
Pipeline-based ETL automation using Ploomber with recipe management
Recipes are what run through the pipeline - they're the executable units
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple

from .core import Chunk
from .pipeline.pipeline_recipe_manager import PipelineRecipeManager
from .pipeline.pipeline_recipe_builder import PipelineRecipeBuilder
from .pipeline.pipeline_recipe_deployer import PipelineRecipeDeployer
from .scraper import scrape_directory, scrape_file, scrape_url


class PipelineManager:
    """Main pipeline coordinator using recipes and auto-discovery"""
    
    def __init__(self, options: Optional[Dict[str, Any]] = None, verbose: bool = False):
        self.options = options or {}
        self.verbose = verbose
        
        # Initialize pipeline recipe system
        self.recipe_manager = PipelineRecipeManager(verbose=verbose)
        self.recipe_builder = PipelineRecipeBuilder(options=options, verbose=verbose)
        self.recipe_deployer = PipelineRecipeDeployer(options=options, verbose=verbose)
    
    def process_pipeline_command(self, source: str, command: str, 
                               pipeline_name: Optional[str] = None,
                               target: Optional[str] = None) -> List[Chunk]:
        """
        Process pipeline commands with minimal complexity
        
        Args:
            source: Input source (directory, file, URL)  
            command: build, deploy, list, import, export
            pipeline_name: Name of recipe to run through pipeline
            target: Optional target override for deploy
        """
        
        if command == "build":
            return self._handle_build(source, pipeline_name, target)
        elif command == "deploy":
            return self._handle_deploy(source, pipeline_name, target)
        elif command == "list":
            return self._handle_list(pipeline_name)
        elif command == "import":
            return self._handle_import(source, pipeline_name)
        elif command == "export":
            return self._handle_export(pipeline_name, target)
        else:
            raise ValueError(f"Unknown command: {command}. Use: build, deploy, list, import, export")
    
    def _handle_build(self, source: str, pipeline_name: str, storage_hint: Optional[str] = None) -> List[Chunk]:
        """Build pipeline recipe from source with minimal assumptions"""
        if not pipeline_name:
            raise ValueError("Recipe name required: --pipeline build <name>")
        
        if self.verbose:
            print(f"[thepipe] Building pipeline recipe '{pipeline_name}' from {source}")
        
        # Analyze source (reuse existing thepipe capabilities)
        source_chunks = self._analyze_source(source)
        
        # Build recipe specification
        recipe_spec = self.recipe_builder.build_recipe_from_chunks(
            source_chunks, pipeline_name
        )
        
        # Extract source files for packaging
        source_files = self._extract_source_files(source_chunks)
        
        # Register recipe in pipeline system
        self.recipe_manager.register_recipe(pipeline_name, recipe_spec, source_files)
        
        # Return analysis + recipe info
        result_chunks = source_chunks.copy()
        
        # Add recipe summary
        summary = self._format_recipe_summary(pipeline_name, recipe_spec)
        result_chunks.append(Chunk(
            path=f"pipeline://recipe/{pipeline_name}/summary",
            text=summary
        ))
        
        return result_chunks
    
    def _handle_deploy(self, source: str, pipeline_name: str, target: Optional[str] = None) -> List[Chunk]:
        """Deploy pipeline recipe with optional target override"""
        if not pipeline_name:
            raise ValueError("Recipe name required: --pipeline deploy <name> [target]")
        
        # Load recipe from pipeline registry
        recipe_spec = self.recipe_manager.get_recipe(pipeline_name)
        if not recipe_spec:
            available = [r["name"] for r in self.recipe_manager.list_recipes()]
            error_msg = f"Pipeline recipe '{pipeline_name}' not found.\nAvailable: {', '.join(available)}"
            return [Chunk(path="error://recipe-not-found", text=error_msg)]
        
        # Determine target (override or default from recipe)
        target = self.recipe_manager.get_recipe_target(pipeline_name, target)
        if not target:
            return [Chunk(
                path="error://no-target",
                text=f"No target specified and no default target configured for recipe '{pipeline_name}'"
            )]
        
        if self.verbose:
            print(f"[thepipe] Deploying pipeline recipe '{pipeline_name}'")
            print(f"[thepipe] Data source: {source}")
            print(f"[thepipe] Target: {target}")
        
        # Analyze input data
        input_chunks = self._analyze_source(source)
        
        # Deploy recipe through pipeline
        deployment_result = self.recipe_deployer.deploy_recipe(
            recipe_spec, source, target, input_chunks
        )
        
        return input_chunks + deployment_result
    
    def _handle_list(self, pipeline_name: Optional[str] = None) -> List[Chunk]:
        """List pipeline recipes or show recipe details"""
        if pipeline_name:
            # Show recipe details
            recipe_spec = self.recipe_manager.get_recipe(pipeline_name)
            if not recipe_spec:
                return [Chunk(
                    path=f"pipeline://recipe/not-found/{pipeline_name}",
                    text=f"Pipeline recipe '{pipeline_name}' not found"
                )]
            
            details = self._format_recipe_details(pipeline_name, recipe_spec)
            return [Chunk(path=f"pipeline://recipe/details/{pipeline_name}", text=details)]
        else:
            # List all recipes
            recipes = self.recipe_manager.list_recipes()
            list_text = self._format_recipe_list(recipes)
            return [Chunk(path="pipeline://recipes/list", text=list_text)]
    
    def _handle_import(self, package_path: str, new_name: Optional[str] = None) -> List[Chunk]:
        """Import packaged pipeline recipe"""
        imported_name = self.recipe_manager.import_recipe(package_path)
        
        if imported_name:
            # Optionally rename
            if new_name and new_name != imported_name:
                # This would require additional implementation
                pass
            
            return [Chunk(
                path=f"pipeline://recipe/imported/{imported_name}",
                text=f"Successfully imported pipeline recipe '{imported_name}' from {package_path}"
            )]
        else:
            return [Chunk(
                path="error://import-failed", 
                text=f"Failed to import pipeline recipe from {package_path}"
            )]
    
    def _handle_export(self, pipeline_name: str, output_path: Optional[str] = None) -> List[Chunk]:
        """Export pipeline recipe for sharing"""
        if not pipeline_name:
            raise ValueError("Recipe name required: --pipeline export <name> [path]")
        
        output_path = output_path or f"{pipeline_name}.zip"
        
        success = self.recipe_manager.package_recipe(pipeline_name, output_path)
        
        if success:
            return [Chunk(
                path=f"pipeline://recipe/exported/{pipeline_name}",
                text=f"Pipeline recipe '{pipeline_name}' exported to {output_path}"
            )]
        else:
            return [Chunk(
                path="error://export-failed",
                text=f"Failed to export pipeline recipe '{pipeline_name}'"
            )]
    
    def _analyze_source(self, source: str) -> List[Chunk]:
        """Analyze source using existing thepipe capabilities"""
        try:
            if source.startswith(("http://", "https://")):
                return scrape_url(source, verbose=self.verbose, options=self.options)
            elif os.path.isdir(source):
                return scrape_directory(
                    dir_path=source, verbose=self.verbose, options=self.options
                )
            else:
                return scrape_file(
                    filepath=source, verbose=self.verbose, options=self.options
                )
        except Exception as e:
            if self.verbose:
                print(f"[thepipe] Error analyzing source: {e}")
            return [Chunk(path=source, text=f"Error analyzing source: {str(e)}")]
    
    def _extract_source_files(self, chunks: List[Chunk]) -> Dict[str, str]:
        """Extract source file contents from chunks for recipe packaging"""
        source_files = {}
        
        for chunk in chunks:
            if chunk.path and chunk.text:
                # Skip if it's not a source file
                if any(chunk.path.endswith(ext) for ext in ['.py', '.sql', '.yaml', '.yml']):
                    filename = os.path.basename(chunk.path)
                    source_files[filename] = chunk.text
        
        return source_files
    
    def _format_recipe_summary(self, pipeline_name: str, recipe_spec: Dict[str, Any]) -> str:
        """Format pipeline recipe build summary"""
        metadata = recipe_spec.get("metadata", {})
        
        summary = f"# Pipeline Recipe: {pipeline_name}\n\n"
        summary += f"**Description:** {metadata.get('description', 'Generated ETL pipeline recipe')}\n"
        summary += f"**Created:** {metadata.get('created', 'now')}\n\n"
        
        # Show default targets
        default_targets = metadata.get("default_targets", {})
        if default_targets:
            summary += "## Default Pipeline Targets\n\n"
            for env, target in default_targets.items():
                summary += f"- **{env}**: `{target}`\n"
            summary += "\n"
        
        # Show dependencies
        deps = metadata.get("dependencies", [])
        if deps:
            summary += f"**Pipeline Dependencies:** {', '.join(deps)}\n\n"
        
        # Usage examples
        summary += "## Pipeline Usage\n\n"
        summary += f"```bash\n"
        summary += f"# Deploy recipe with file\n"
        summary += f"thepipe data.csv --pipeline deploy {pipeline_name}\n\n"
        summary += f"# Deploy recipe with custom target\n"
        summary += f"thepipe data.csv --pipeline deploy {pipeline_name} 'postgresql://prod/db'\n\n"
        summary += f"# Deploy recipe with URL\n"
        summary += f"thepipe 'https://api.com/data' --pipeline deploy {pipeline_name}\n"
        summary += f"```\n"
        
        return summary
    
    def _format_recipe_list(self, recipes: List[Dict[str, Any]]) -> str:
        """Format list of all pipeline recipes"""
        if not recipes:
            return """# No Pipeline Recipes Found

Create your first pipeline recipe:
```bash
thepipe ./etl_code --pipeline build my_first_recipe
```

Or import an existing recipe:
```bash
thepipe recipe.zip --pipeline import
```
"""
        
        list_text = f"# Available Pipeline Recipes ({len(recipes)})\n\n"
        
        for recipe in recipes:
            name = recipe["name"]
            display_name = recipe["display_name"]
            description = recipe["description"]
            
            list_text += f"## {display_name}\n"
            list_text += f"**Name:** `{name}`\n"
            list_text += f"**Description:** {description}\n"
            
            # Show default targets if available
            default_targets = recipe.get("default_targets", {})
            if default_targets:
                targets = ", ".join(default_targets.keys())
                list_text += f"**Pipeline Targets:** {targets}\n"
            
            list_text += f"**Deploy:** `thepipe <data> --pipeline deploy {name}`\n\n"
        
        return list_text
    
    def _format_recipe_details(self, pipeline_name: str, recipe_spec: Dict[str, Any]) -> str:
        """Format detailed pipeline recipe information"""
        metadata = recipe_spec.get("metadata", {})
        
        details = f"# Pipeline Recipe: {pipeline_name}\n\n"
        details += f"**Description:** {metadata.get('description', 'No description')}\n"
        details += f"**Version:** {metadata.get('version', 'unknown')}\n"
        details += f"**Created:** {metadata.get('created', 'unknown')}\n\n"
        
        # Expected inputs
        expected_inputs = metadata.get("expected_inputs", ["any"])
        details += f"**Accepts:** {', '.join(expected_inputs)} data sources\n\n"
        
        # Default targets
        default_targets = metadata.get("default_targets", {})
        if default_targets:
            details += "## Pipeline Deployment Targets\n\n"
            for env, target in default_targets.items():
                details += f"- **{env}**: `{target}`\n"
            details += "\n"
        
        # Dependencies
        deps = metadata.get("dependencies", [])
        if deps:
            details += f"**Pipeline Dependencies:** {', '.join(deps)}\n\n"
        
        # Outputs
        outputs = metadata.get("outputs", [])
        if outputs:
            details += f"**Pipeline Outputs:** {', '.join(outputs)}\n\n"
        
        # Usage examples
        details += "## Pipeline Usage Examples\n\n"
        details += "```bash\n"
        details += f"# Basic pipeline deployment\n"
        details += f"thepipe data.csv --pipeline deploy {pipeline_name}\n\n"
        
        if len(default_targets) > 1:
            first_env = next(iter(default_targets.keys()))
            details += f"# Deploy to specific pipeline target\n"
            details += f"thepipe data.csv --pipeline deploy {pipeline_name} '{default_targets[first_env]}'\n\n"
        
        details += f"# Export recipe for sharing\n"
        details += f"thepipe . --pipeline export {pipeline_name} recipe_package.zip\n"
        details += "```\n"
        
        return details


def parse_pipeline_arguments(args) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse --pipeline arguments with support for target override
    
    Formats:
    --pipeline build pipeline_name [storage_location]
    --pipeline deploy pipeline_name [target] 
    --pipeline list [pipeline_name]
    --pipeline import package_path [new_name]
    --pipeline export pipeline_name [output_path]
    
    Returns:
        (command, pipeline_name, third_arg)
    """
    if not hasattr(args, 'pipeline') or not args.pipeline:
        return None, None, None
    
    pipeline_args = args.pipeline
    
    if len(pipeline_args) == 0:
        raise ValueError("Pipeline command required. Use: build, deploy, list, import, export")
    
    command = pipeline_args[0]
    pipeline_name = pipeline_args[1] if len(pipeline_args) > 1 else None
    third_arg = pipeline_args[2] if len(pipeline_args) > 2 else None
    
    return command, pipeline_name, third_arg
