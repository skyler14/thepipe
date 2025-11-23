# thepipe/pipeline/pipeline_recipe_deployer.py
"""
Pipeline recipe deployment with isolated virtual environments outside repo
Recipes are the executable units that get deployed through the pipeline system
Negative space: No complexity where it's not needed
"""

import os
import sys
import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..core import Chunk
from .pipeline_recipe_manager import PipelineRecipeManager


class PipelineRecipeDeployer:
    """Deploy pipeline recipes with isolated virtual environments"""
    
    def __init__(self, options: Optional[Dict[str, Any]] = None, verbose: bool = False):
        self.options = options or {}
        self.verbose = verbose
        self.recipe_manager = PipelineRecipeManager(verbose=verbose)
        self.global_venv_dir = Path.home() / ".thepipe" / "venvs"
        
    def deploy_recipe(self, recipe_spec: Dict[str, Any], data_source: str,
                     target: str, input_chunks: Optional[List[Chunk]] = None) -> List[Chunk]:
        """Deploy pipeline recipe with isolated environment"""
        
        recipe_name = recipe_spec.get("metadata", {}).get("name", "unknown")
        
        if self.verbose:
            print(f"[thepipe] Deploying pipeline recipe '{recipe_name}'")
            print(f"[thepipe] Data source: {data_source}")
            print(f"[thepipe] Target: {target}")
        
        # Use temporary deployment directory
        with tempfile.TemporaryDirectory() as temp_dir:
            deploy_dir = Path(temp_dir) / "deploy"
            deploy_dir.mkdir()
            
            try:
                # Setup deployment environment
                self._setup_deployment(recipe_spec, deploy_dir, data_source, target)
                
                # Ensure virtual environment exists
                venv_path = self._ensure_venv(recipe_spec)
                
                # Execute recipe
                execution_result = self._execute_recipe(deploy_dir, venv_path, recipe_spec)
                
                # Collect results
                result_chunks = self._collect_results(
                    deploy_dir, execution_result, input_chunks
                )
                
                return result_chunks
                
            except Exception as e:
                if self.verbose:
                    print(f"[thepipe] Pipeline recipe deployment failed: {e}")
                return [Chunk(
                    path="pipeline://deployment/error",
                    text=f"Pipeline recipe deployment failed: {str(e)}"
                )]
    
    def _setup_deployment(self, recipe_spec: Dict[str, Any], deploy_dir: Path,
                         data_source: str, target: str):
        """Setup deployment directory with minimal configuration"""
        
        # Write pipeline files
        if "pipeline_yaml" in recipe_spec:
            pipeline_content = self._adapt_pipeline_for_deployment(
                recipe_spec["pipeline_yaml"], data_source, target
            )
            (deploy_dir / "pipeline.yaml").write_text(pipeline_content)
        
        # Write client files
        if "client_files" in recipe_spec:
            for filename, content in recipe_spec["client_files"].items():
                adapted_content = self._inject_credentials(content)
                (deploy_dir / filename).write_text(adapted_content)
        
        # Write source files
        if "source_files" in recipe_spec:
            source_dir = deploy_dir / "source"
            source_dir.mkdir(exist_ok=True)
            
            for filename, content in recipe_spec["source_files"].items():
                adapted_content = self._adapt_source_for_deployment(
                    content, data_source, target
                )
                (source_dir / filename).write_text(adapted_content)
        
        # Create environment file
        self._create_env_file(deploy_dir, data_source, target)
    
    def _adapt_pipeline_for_deployment(self, pipeline_yaml: str, 
                                     data_source: str, target: str) -> str:
        """Adapt pipeline.yaml for specific deployment"""
        import yaml
        
        try:
            config = yaml.safe_load(pipeline_yaml)
        except:
            return pipeline_yaml  # Return original if parsing fails
        
        # Inject data source and target as parameters
        if "tasks" in config:
            for task in config["tasks"]:
                if isinstance(task, dict):
                    # Add parameters to task
                    if "params" not in task:
                        task["params"] = {}
                    task["params"]["data_source"] = data_source
                    task["params"]["target"] = target
        
        return yaml.dump(config, default_flow_style=False)
    
    def _inject_credentials(self, content: str) -> str:
        """Inject credentials from options into client files"""
        # Add credential loading at the top
        credential_inject = """
# Auto-injected credential handling for pipeline recipe
import os
"""
        
        # Add credentials from pipeline_credentials
        credentials = self.options.get("pipeline_credentials", {})
        for key, value in credentials.items():
            credential_inject += f"os.environ['{key}'] = '{value}'\n"
        
        # Find import section and inject after it
        lines = content.split('\n')
        import_end = 0
        
        for i, line in enumerate(lines):
            if line.strip() and not line.strip().startswith(('#', 'import', 'from')):
                import_end = i
                break
        
        # Insert credentials after imports
        lines.insert(import_end, credential_inject)
        return '\n'.join(lines)
    
    def _adapt_source_for_deployment(self, content: str, data_source: str, target: str) -> str:
        """Adapt source files for deployment"""
        # Minimal adaptation - just inject parameters
        adapted = f"""
# Auto-generated deployment parameters for pipeline recipe
DATA_SOURCE = "{data_source}"
TARGET = "{target}"

"""
        adapted += content
        return adapted
    
    def _create_env_file(self, deploy_dir: Path, data_source: str, target: str):
        """Create .env file with deployment parameters"""
        env_vars = {
            "DATA_SOURCE": data_source,
            "TARGET": target,
            "PYTHONPATH": str(deploy_dir)
        }
        
        # Add credentials
        env_vars.update(self.options.get("pipeline_credentials", {}))
        
        env_content = "\n".join(f"{k}={v}" for k, v in env_vars.items())
        (deploy_dir / ".env").write_text(env_content)
        
        # Also set in current environment
        os.environ.update(env_vars)
    
    def _ensure_venv(self, recipe_spec: Dict[str, Any]) -> Path:
        """Ensure virtual environment exists for pipeline recipe"""
        recipe_name = recipe_spec.get("metadata", {}).get("name", "unknown")
        venv_path = self.global_venv_dir / recipe_name
        
        if venv_path.exists() and (venv_path / "pyvenv.cfg").exists():
            if self.verbose:
                print(f"[thepipe] Using existing venv for recipe: {venv_path}")
            return venv_path
        
        if self.verbose:
            print(f"[thepipe] Creating venv for pipeline recipe: {venv_path}")
        
        # Clean up if partially created
        if venv_path.exists():
            shutil.rmtree(venv_path)
        
        # Create virtual environment
        subprocess.run([
            sys.executable, "-m", "venv", str(venv_path)
        ], check=True, capture_output=True)
        
        # Install dependencies
        self._install_dependencies(venv_path, recipe_spec)
        
        return venv_path
    
    def _install_dependencies(self, venv_path: Path, recipe_spec: Dict[str, Any]):
        """Install dependencies in virtual environment"""
        # Determine pip executable path
        if sys.platform == "win32":
            pip_exec = venv_path / "Scripts" / "pip.exe"
        else:
            pip_exec = venv_path / "bin" / "pip"
        
        # Get dependencies from recipe
        dependencies = recipe_spec.get("metadata", {}).get("dependencies", [])
        
        if not dependencies:
            dependencies = ["ploomber", "pandas", "sqlalchemy"]
        
        if self.verbose:
            print(f"[thepipe] Installing pipeline recipe dependencies: {', '.join(dependencies)}")
        
        # Install packages
        try:
            subprocess.run([
                str(pip_exec), "install", "--quiet"
            ] + dependencies, check=True, capture_output=True)
            
            if self.verbose:
                print(f"[thepipe] Pipeline recipe dependencies installed successfully")
                
        except subprocess.CalledProcessError as e:
            if self.verbose:
                print(f"[thepipe] Warning: Some dependencies failed to install: {e}")
    
    def _execute_recipe(self, deploy_dir: Path, venv_path: Path, 
                       recipe_spec: Dict[str, Any]) -> Dict[str, Any]:
        """Execute pipeline recipe using virtual environment"""
        
        # Determine python executable
        if sys.platform == "win32":
            python_exec = venv_path / "Scripts" / "python.exe"
        else:
            python_exec = venv_path / "bin" / "python"
        
        # Change to deployment directory
        original_cwd = os.getcwd()
        os.chdir(deploy_dir)
        
        try:
            if self.verbose:
                print(f"[thepipe] Executing pipeline recipe with {python_exec}")
            
            # Run ploomber build
            result = subprocess.run([
                str(python_exec), "-m", "ploomber", "build"
            ], capture_output=True, text=True, timeout=300)
            
            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0,
                "python_exec": str(python_exec),
                "venv_path": str(venv_path)
            }
            
        except subprocess.TimeoutExpired:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": "Pipeline recipe execution timed out (5 minutes)",
                "success": False
            }
        except Exception as e:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "success": False
            }
        finally:
            os.chdir(original_cwd)
    
    def _collect_results(self, deploy_dir: Path, execution_result: Dict[str, Any],
                        input_chunks: Optional[List[Chunk]]) -> List[Chunk]:
        """Collect pipeline recipe deployment results"""
        result_chunks = []
        
        # Add input analysis if available
        if input_chunks:
            result_chunks.extend(input_chunks)
        
        # Add execution summary
        success = execution_result["success"]
        summary_text = f"# Pipeline Recipe Deployment {'✅ Success' if success else '❌ Failed'}\n\n"
        
        if success:
            summary_text += f"**Status:** Completed successfully\n"
            summary_text += f"**Virtual Environment:** {execution_result.get('venv_path', 'unknown')}\n"
            
            if execution_result['stdout']:
                summary_text += f"\n## Pipeline Output\n```\n{execution_result['stdout'][:1000]}"
                if len(execution_result['stdout']) > 1000:
                    summary_text += "\n... (truncated)"
                summary_text += "\n```\n"
        else:
            summary_text += f"**Status:** Failed\n"
            summary_text += f"**Error:** {execution_result['stderr']}\n"
            
            if execution_result['stdout']:
                summary_text += f"\n**Debug Output:**\n```\n{execution_result['stdout']}\n```\n"
        
        result_chunks.append(Chunk(
            path="pipeline://deployment/summary",
            text=summary_text
        ))
        
        # Collect output files if successful
        if success:
            output_chunks = self._collect_outputs(deploy_dir)
            result_chunks.extend(output_chunks)
        
        return result_chunks
    
    def _collect_outputs(self, deploy_dir: Path) -> List[Chunk]:
        """Collect output files from pipeline recipe deployment"""
        output_chunks = []
        
        # Common output locations
        output_locations = [
            deploy_dir / "output",
            deploy_dir / "data",
            deploy_dir / "results"
        ]
        
        for output_dir in output_locations:
            if output_dir.exists():
                for output_file in output_dir.rglob("*"):
                    if output_file.is_file() and output_file.stat().st_size < 1024 * 1024:  # < 1MB
                        try:
                            if output_file.suffix.lower() in ['.csv', '.json', '.txt', '.md']:
                                content = output_file.read_text()
                                
                                # Truncate large files
                                if len(content) > 2000:
                                    content = content[:2000] + "\n\n... (truncated, full file available)"
                                
                                output_chunks.append(Chunk(
                                    path=f"pipeline://output/{output_file.relative_to(deploy_dir)}",
                                    text=content
                                ))
                            else:
                                # Binary file info
                                size = output_file.stat().st_size
                                output_chunks.append(Chunk(
                                    path=f"pipeline://output/{output_file.relative_to(deploy_dir)}",
                                    text=f"Binary file: {output_file.name} ({size:,} bytes)"
                                ))
                        except Exception as e:
                            output_chunks.append(Chunk(
                                path=f"pipeline://output/{output_file.relative_to(deploy_dir)}",
                                text=f"Error reading file: {e}"
                            ))
        
        return output_chunks
    
    def cleanup_venv(self, recipe_name: str) -> bool:
        """Clean up virtual environment for pipeline recipe"""
        return self.recipe_manager.cleanup_venv(recipe_name)

