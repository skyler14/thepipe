"""
Built-in Dependency Resolvers for Additional Languages

These resolvers extend thepipe's dependency mapping to support Dart, Swift, and Kotlin.
Import this module before using code_relations with these languages.

Usage:
    from thepipe.analyzer import resolvers  # Auto-registers resolvers
    # Or manually:
    from thepipe.analyzer.resolvers import register_all_resolvers
    register_all_resolvers()
"""

import os
import re
from pathlib import Path
from typing import Optional
import logging

from .types import DependencyEdge
from .dependency_map import register_resolver

logger = logging.getLogger(__name__)

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


# ============================================================================
# DART RESOLVER
# ============================================================================

def resolve_dart_import(
    import_stmt: str, 
    from_file: str, 
    mapper: 'DependencyMapper'
) -> Optional[DependencyEdge]:
    """
    Resolve Dart import statements to file paths.
    
    Dart import patterns:
    - import 'dart:async';              -> Dart SDK (external)
    - import 'package:flutter/x.dart';  -> Package (external)
    - import '../models/foo.dart';      -> Relative path (internal)
    - import 'bar.dart';                -> Same directory (internal)
    """
    # Extract the import path from the statement
    match = re.search(r"import\s+['\"]([^'\"]+)['\"]", import_stmt)
    if not match:
        return None
    
    import_path = match.group(1)
    
    # Dart SDK imports (always external)
    if import_path.startswith('dart:'):
        return DependencyEdge(
            from_file=from_file,
            to_file=import_path,
            import_statement=import_stmt.strip(),
            is_external=True
        )
    
    # Package imports (external - from pub.dev or local packages)
    if import_path.startswith('package:'):
        pkg_match = re.match(r'package:([^/]+)/(.*)', import_path)
        if pkg_match:
            pkg_name = pkg_match.group(1)
            rel_path = pkg_match.group(2)

            local_match = mapper.lookup_dart_package_file(pkg_name, rel_path)

            if local_match:
                return DependencyEdge(
                    from_file=from_file,
                    to_file=local_match,
                    import_statement=import_stmt.strip(),
                    is_external=False
                )

        return DependencyEdge(
            from_file=from_file,
            to_file=import_path,
            import_statement=import_stmt.strip(),
            is_external=True
        )
    
    # Relative imports (internal)
    # Convert from_file to absolute path if needed
    from_path = Path(from_file)
    if not from_path.is_absolute():
        from_path = mapper.repo_root / from_file
    
    from_dir = from_path.parent
    
    # Handle relative paths like '../models/foo.dart' or 'bar.dart'
    try:
        target_path = (from_dir / import_path).resolve()
        
        # Check if target exists and is within repo
        if target_path.exists() and target_path.is_file():
            try:
                rel_to_root = target_path.relative_to(mapper.repo_root)
                return DependencyEdge(
                    from_file=from_file,
                    to_file=str(rel_to_root),
                    import_statement=import_stmt.strip(),
                    is_external=False
                )
            except ValueError:
                # File outside repo root
                pass
    except Exception as e:
        logger.warning(
            f"Dart import resolution failed for '{import_path}'",
            exc_info=True,
        )
    
    return None


# ============================================================================
# SWIFT RESOLVER
# ============================================================================

def resolve_swift_import(
    import_stmt: str, 
    from_file: str, 
    mapper: 'DependencyMapper'
) -> Optional[DependencyEdge]:
    """
    Resolve Swift import statements.
    
    Swift import patterns:
    - import Foundation           -> System framework (external)
    - import UIKit                -> System framework (external)
    - import MyModule             -> Could be local module (need to resolve)
    - @_exported import X         -> Re-exported import
    - @testable import X          -> Test dependencies
    - import class MyModule.Foo   -> Import specific symbol
    
    Note: Swift uses module-based imports, not file-based. 
    Local file dependencies are implicit through the same module.
    We mark system frameworks as external.
    """
    # Extract module name from import statement
    # Captures the last part of the import path, ignoring attributes and kinds
    # e.g., "@testable import class Module.Submodule" -> "Module.Submodule"
    
    # Cleaning the statement first
    clean_stmt = import_stmt.strip()
    
    # 1. Remove attributes (starting with @)
    clean_stmt = re.sub(r'@\w+(?:\([^)]*\))?\s*', '', clean_stmt)
    
    # 2. Remove 'import' keyword
    if clean_stmt.startswith('import '):
        clean_stmt = clean_stmt[7:].strip()
    
    # 3. Remove import kind (struct, class, enum, func, etc.) if present
    # These are usually followed by the module path
    import_kinds = {'typealias', 'struct', 'class', 'enum', 'protocol', 'let', 'var', 'func'}
    parts = clean_stmt.split()
    if parts and parts[0] in import_kinds:
        clean_stmt = " ".join(parts[1:])
        
    module_path = clean_stmt.strip()
    
    if not module_path:
        return None
        
    # Get the top-level module name (e.g., "UIKit" from "UIKit.UIView")
    module_name = module_path.split('.')[0]
    
    if module_name.casefold() in _SWIFT_SYSTEM_FRAMEWORKS_LOWER:
        return DependencyEdge(
            from_file=from_file,
            to_file=module_name,
            import_statement=import_stmt.strip(),
            is_external=True
        )
    
    # Try to find a local module file by convention.
    possible_names = [
        f"{module_name}.swift",
        f"{module_name}/{module_name}.swift",
        f"Sources/{module_name}.swift",
        f"Sources/{module_name}/main.swift", 
    ]
    
    for name in possible_names:
        if name in mapper._file_index:
            return DependencyEdge(
                from_file=from_file,
                to_file=mapper._file_index[name],
                import_statement=import_stmt.strip(),
                is_external=False
            )
    
    return DependencyEdge(
        from_file=from_file,
        to_file=module_name,
        import_statement=import_stmt.strip(),
        is_external=True
    )


# ============================================================================
# KOTLIN RESOLVER
# ============================================================================

def resolve_kotlin_import(
    import_stmt: str, 
    from_file: str, 
    mapper: 'DependencyMapper'
) -> Optional[DependencyEdge]:
    """
    Resolve Kotlin import statements.
    
    Kotlin import patterns:
    - import kotlin.collections.*      -> Kotlin stdlib (external)
    - import android.os.Bundle         -> Android framework (external)
    - import com.myapp.models.User     -> Could be local (internal)
    - import java.util.List            -> Java stdlib (external)
    """
    # Extract the import path
    match = re.search(r'import\s+([\w.]+)', import_stmt)
    if not match:
        return None
    
    import_path = match.group(1)
    
    # System packages (external)
    external_prefixes = [
        'kotlin.', 'kotlinx.', 'android.', 'androidx.', 'java.', 'javax.',
        'com.google.', 'org.jetbrains.', 'io.ktor.', 'org.json.'
    ]
    
    for prefix in external_prefixes:
        if import_path.startswith(prefix):
            return DependencyEdge(
                from_file=from_file,
                to_file=import_path,
                import_statement=import_stmt.strip(),
                is_external=True
            )
    
    # Try to find local file
    # com.myapp.models.User -> com/myapp/models/User.kt
    file_path = import_path.replace('.', '/') + '.kt'
    
    # Also try just the class name
    class_name = import_path.split('.')[-1]
    
    for candidate in [file_path, f"{class_name}.kt"]:
        if candidate in mapper._file_index:
            return DependencyEdge(
                from_file=from_file,
                to_file=mapper._file_index[candidate],
                import_statement=import_stmt.strip(),
                is_external=False
            )
    
    # Unknown - assume external
    return DependencyEdge(
        from_file=from_file,
        to_file=import_path,
        import_statement=import_stmt.strip(),
        is_external=True
    )


# ============================================================================
# RUBY RESOLVER
# ============================================================================

def resolve_ruby_import(
    import_stmt: str, 
    from_file: str, 
    mapper: 'DependencyMapper'
) -> Optional[DependencyEdge]:
    """
    Resolve Ruby require/require_relative statements.
    
    Ruby import patterns:
    - require 'json'                    -> Gem or stdlib (external)
    - require_relative '../lib/foo'     -> Relative path (internal)
    - require_relative 'bar'            -> Same directory (internal)
    """
    # Check for require_relative (internal)
    match = re.search(r"require_relative\s+['\"]([^'\"]+)['\"]", import_stmt)
    if match:
        rel_path = match.group(1)
        
        # Convert from_file to absolute path if needed
        from_path = Path(from_file)
        if not from_path.is_absolute():
            from_path = mapper.repo_root / from_file
        from_dir = from_path.parent
        
        # Add .rb extension if missing
        if not rel_path.endswith('.rb'):
            rel_path += '.rb'
        
        try:
            target_path = (from_dir / rel_path).resolve()
            if target_path.exists():
                rel_to_root = target_path.relative_to(mapper.repo_root)
                return DependencyEdge(
                    from_file=from_file,
                    to_file=str(rel_to_root),
                    import_statement=import_stmt.strip(),
                    is_external=False
                )
        except Exception:
            pass
        
        return None
    
    # Check for require (usually external)
    match = re.search(r"require\s+['\"]([^'\"]+)['\"]", import_stmt)
    if match:
        required = match.group(1)
        return DependencyEdge(
            from_file=from_file,
            to_file=required,
            import_statement=import_stmt.strip(),
            is_external=True
        )
    
    return None


# ============================================================================
# REGISTRATION
# ============================================================================

def register_all_resolvers():
    """Register all built-in additional language resolvers."""
    register_resolver('dart', resolve_dart_import)
    register_resolver('swift', resolve_swift_import)
    register_resolver('kotlin', resolve_kotlin_import)
    register_resolver('ruby', resolve_ruby_import)
    logger.info("Registered built-in resolvers for: Dart, Swift, Kotlin, Ruby")


# Auto-register when module is imported
register_all_resolvers()
