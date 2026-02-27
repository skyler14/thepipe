"""
Base Language Plugin

Defines the abstract base class that all language plugins must implement.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Set
from pathlib import Path
from dataclasses import dataclass
from ..types import DependencyEdge

@dataclass
class PluginConfig:
    """Configuration passed to plugins"""
    project_root: Path
    # Context populated by manifests (e.g. {'dart': {'package_name': 'foo'}})
    context: Dict[str, Any] 

class LanguagePlugin(ABC):
    """
    Abstract base class for language-specific analysis logic.
    """
    
    @property
    @abstractmethod
    def extensions(self) -> List[str]:
        """File extensions handled by this plugin (e.g., ['.dart'])"""
        pass
        
    @property
    def manifest_files(self) -> List[str]:
        """List of manifest filenames (e.g. ['pubspec.yaml'])"""
        return []
    
    @property
    def context_keys(self) -> List[str]:
        """
        Context keys where this plugin expects manifest data.
        
        By default this uses the plugin class name minus "Plugin", lowercased.
        Example: SwiftPlugin -> ["swift"].
        """
        name = self.__class__.__name__
        if name.endswith("Plugin"):
            name = name[:-6]
        key = name.lower()
        return [key] if key else []

    # --- AST / Grammar ---

    @property
    def import_queries(self) -> str:
        """Tree-sitter query for imports"""
        return ""
        
    @property
    def function_queries(self) -> str:
        """Tree-sitter query for functions/methods"""
        return ""
        
    @property
    def class_queries(self) -> str:
        """Tree-sitter query for classes/structs/types"""
        return ""

    # --- Resolution ---

    def parse_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        """
        Parse a manifest file and return structured data (e.g., package name, 
        source mapping, dependency versions).
        """
        return {}

    def resolve_import(
        self, 
        import_stmt: str, 
        from_file: Path, 
        config: PluginConfig
    ) -> Optional[DependencyEdge]:
        """
        Resolve an import statement to a file path or external dependency.
        
        Args:
            import_stmt: The raw import string (e.g., "import 'package:foo/bar.dart'")
            from_file: Absolute path to the file containing the import
            config: PluginConfig containing project root and context
            
        Returns:
            DependencyEdge if resolved, None otherwise.
        """
        return None
