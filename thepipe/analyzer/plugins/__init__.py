from .base import LanguagePlugin, PluginConfig
from .manager import PluginManager, get_plugin_manager
from .go import GoPlugin
from .rust import RustPlugin
from .swift import SwiftPlugin
from .web import WebStackPlugin
import threading

_BUILTINS_REGISTERED = False
_BUILTINS_LOCK = threading.Lock()

def register_builtin_plugins() -> PluginManager:
    """Register built-in language plugins once."""
    global _BUILTINS_REGISTERED
    manager = get_plugin_manager()
    
    if _BUILTINS_REGISTERED:
        return manager

    with _BUILTINS_LOCK:
        if _BUILTINS_REGISTERED:
            return manager

        manager.register_plugin(GoPlugin)
        manager.register_plugin(RustPlugin)
        manager.register_plugin(SwiftPlugin)
        manager.register_plugin(WebStackPlugin)

        _BUILTINS_REGISTERED = True
    return manager

__all__ = [
    'LanguagePlugin',
    'PluginConfig',
    'PluginManager',
    'get_plugin_manager',
    'register_builtin_plugins',
]
