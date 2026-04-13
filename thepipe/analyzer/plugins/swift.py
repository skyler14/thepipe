from pathlib import Path
from typing import Dict, List, Optional, Any
import re
from .base import LanguagePlugin, PluginConfig
from ..types import DependencyEdge

_SWIFT_SYSTEM_FRAMEWORKS = {
    'Accessibility', 'ActivityKit', 'AppKit', 'ARKit', 'AVFoundation',
    'ClockKit', 'CloudKit', 'Combine', 'Contacts', 'CoreBluetooth',
    'CoreData', 'CoreFoundation', 'CoreGraphics', 'CoreImage', 'CoreLocation',
    'CoreML', 'CoreMotion', 'CryptoKit', 'Darwin', 'Dispatch', 'EventKit',
    'FileProvider', 'Foundation', 'GameKit', 'GroupActivities', 'HealthKit',
    'Intents', 'LocalAuthentication', 'MapKit', 'MediaPlayer', 'Metal',
    'MetalKit', 'MusicKit', 'NaturalLanguage', 'Network', 'ObjectiveC',
    'Observation', 'OSLog', 'PassKit', 'PDFKit', 'PencilKit', 'Photos',
    'QuartzCore', 'RealityKit', 'ReplayKit', 'SafariServices', 'SceneKit',
    'ShazamKit', 'Speech', 'SpriteKit', 'StoreKit', 'Swift', 'SwiftData',
    'SwiftUI', 'TipKit', 'UIKit', 'UserNotifications', 'VideoToolbox',
    'Vision', 'WatchConnectivity', 'WatchKit', 'WebKit', 'WidgetKit',
    'XCTest', 'os',
}
_SWIFT_SYSTEM_FRAMEWORKS_LOWER = {name.casefold() for name in _SWIFT_SYSTEM_FRAMEWORKS}

class SwiftPlugin(LanguagePlugin):
    @property
    def extensions(self) -> List[str]:
        return ['.swift']

    @property
    def manifest_files(self) -> List[str]:
        return ['Package.swift']

    @property
    def import_queries(self) -> str:
        return """
        (import_declaration) @import
        """

    @property
    def function_queries(self) -> str:
        return """
        (function_declaration name: (simple_identifier) @name) @func
        (init_declaration) @func
        (deinit_declaration) @func
        """

    @property
    def class_queries(self) -> str:
        return """
        (class_declaration) @class
        (protocol_declaration name: (type_identifier) @name) @class
        """

    def parse_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        """
        Parse Package.swift for target-to-path mappings.
        Since Package.swift is executable, we use heuristics to find `.target(...)` calls.
        """
        data = {'targets': {}}
        try:
            content = manifest_path.read_text()
            # Regex to capture target name and optional path
            # cases:
            # .target(name: "Name")
            # .target(name: "Name", path: "CustomPath")
            # .target(name: "Name", ..., path: "Sources/Custom")
            
            target_matches = re.finditer(r'\.target\s*\(\s*name:\s*"([^"]+)"(?:[^)]*?path:\s*"([^"]+)")?', content)
            
            for match in target_matches:
                name = match.group(1)
                custom_path = match.group(2)
                
                if custom_path:
                    data['targets'][name] = custom_path
                else:
                    # Default convention
                    data['targets'][name] = f"Sources/{name}"
                    
        except Exception:
            pass
        return data

    def resolve_import(
        self, 
        import_stmt: str, 
        from_file: Path, 
        config: PluginConfig
    ) -> Optional[DependencyEdge]:
        """
        Resolve Swift import statements using manifest context.
        """
        # Cleaning the statement first
        clean_stmt = import_stmt.strip()
        # 1. Remove attributes (starting with @)
        clean_stmt = re.sub(r'@\w+\s+', '', clean_stmt)
        # 2. Remove 'import' keyword
        if clean_stmt.startswith('import '):
            clean_stmt = clean_stmt[7:].strip()
        # 3. Remove import kind
        import_kinds = {'typealias', 'struct', 'class', 'enum', 'protocol', 'let', 'var', 'func'}
        parts = clean_stmt.split()
        if parts and parts[0] in import_kinds:
            clean_stmt = " ".join(parts[1:])
            
        module_path = clean_stmt.strip()
        if not module_path:
            return None
            
        module_name = module_path.split('.')[0]
        
        if module_name.casefold() in _SWIFT_SYSTEM_FRAMEWORKS_LOWER:
            return DependencyEdge(from_file=str(from_file), to_file=module_name, import_statement=import_stmt.strip(), is_external=True)
            
        # Check Manifest Context for local targets
        # Context should look like: {'swift': {'targets': {'MyTarget': 'Sources/MyTarget'}}}
        swift_ctx = config.context.get('swift', {})
        targets = swift_ctx.get('targets', {})
        
        if module_name in targets:
            # Map module to its source directory
            target_path = targets[module_name]
            # In a real implementation, we'd probably map to the directory itself
            # or a main file within it. For now, we point to the directory.
            # Ideally, we need to find the specific files, but mapping to the directory
            # node is often sufficient for high-level graphs.
            
            # Resolve relative to project root
            resolved_path = (config.project_root / target_path).resolve()
            
            try:
                rel_path = resolved_path.relative_to(config.project_root)
                return DependencyEdge(from_file=str(from_file), to_file=str(rel_path), import_statement=import_stmt.strip(), is_external=False)
            except ValueError:
                pass
                
        # Fallback to external
        return DependencyEdge(from_file=str(from_file), to_file=module_name, import_statement=import_stmt.strip(), is_external=True)
