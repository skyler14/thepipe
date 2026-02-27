"""
Plugin Manager

Handles registration and retrieval of language plugins.
"""

import logging
from typing import Dict, List, Type, Optional, Set
from .base import LanguagePlugin

logger = logging.getLogger(__name__)

class PluginManager:
    _instance = None
    
    def __init__(self):
        self._plugins: Dict[str, LanguagePlugin] = {} # extension -> plugin instance
        self._manifest_map: Dict[str, LanguagePlugin] = {} # manifest_name -> plugin instance
        self._registered_plugins: Set[str] = set()
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_plugin(self, plugin_cls: Type[LanguagePlugin]):
        """Register a plugin class."""
        plugin_name = plugin_cls.__name__
        if plugin_name in self._registered_plugins:
            return
        
        try:
            plugin = plugin_cls()
            for ext in plugin.extensions:
                self._plugins[ext.lower()] = plugin
            
            for manifest in plugin.manifest_files:
                self._manifest_map[manifest] = plugin
            
            self._registered_plugins.add(plugin_name)
                
            logger.info(f"Registered plugin {plugin_cls.__name__} for {plugin.extensions}")
        except Exception as e:
            logger.error(f"Failed to register plugin {plugin_cls.__name__}: {e}")

    def get_plugin_for_extension(self, extension: str) -> Optional[LanguagePlugin]:
        """Get plugin for a specific file extension (e.g. '.py')."""
        return self._plugins.get(extension.lower())

    def get_plugin_for_manifest(self, filename: str) -> Optional[LanguagePlugin]:
        """Get plugin capable of parsing this manifest."""
        return self._manifest_map.get(filename)

    def get_all_manifest_files(self) -> List[str]:
        """Get list of all supported manifest filenames."""
        return list(self._manifest_map.keys())

# Global singleton accessor
def get_plugin_manager() -> PluginManager:
    return PluginManager.get_instance()
