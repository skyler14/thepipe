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

import re
from pathlib import Path
from typing import Optional
import logging

from .types import DependencyEdge
from .dependency_map import register_resolver

logger = logging.getLogger(__name__)


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
        logger.debug(f"Dart import resolution failed for '{import_path}': {e}")
    
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
    
    Note: Swift uses module-based imports, not file-based. 
    Local file dependencies are implicit through the same module.
    We mark system frameworks as external.
    """
    # Extract module name from import statement
    match = re.search(r'import\s+(?:class\s+|struct\s+|enum\s+|func\s+)?(\w+)', import_stmt)
    if not match:
        return None
    
    module_name = match.group(1)
    
    # Common Swift system frameworks (external)
    swift_system_frameworks = {
        'Foundation', 'UIKit', 'SwiftUI', 'Combine', 'CoreData',
        'CoreGraphics', 'CoreLocation', 'CoreMotion', 'CoreFoundation',
        'Darwin', 'Dispatch', 'ObjectiveC', 'os', 'Swift', 'XCTest',
        'HealthKit', 'WatchKit', 'ClockKit', 'WatchConnectivity',
        'AVFoundation', 'MapKit', 'StoreKit', 'CloudKit', 'GameKit'
    }
    
    if module_name in swift_system_frameworks:
        return DependencyEdge(
            from_file=from_file,
            to_file=module_name,
            import_statement=import_stmt.strip(),
            is_external=True
        )
    
    # Try to find local module file (ModuleName.swift)
    possible_names = [
        f"{module_name}.swift",
        f"{module_name}/{module_name}.swift",
    ]
    
    for name in possible_names:
        if name in mapper._file_index:
            return DependencyEdge(
                from_file=from_file,
                to_file=mapper._file_index[name],
                import_statement=import_stmt.strip(),
                is_external=False
            )
    
    # Unknown module - assume external (SPM package, etc.)
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
