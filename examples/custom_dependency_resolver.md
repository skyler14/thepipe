# Custom Dependency Resolver Example for Dart

This example shows how to extend thepipe's dependency mapper to support Dart imports.

## Quick Start

```python
from thepipe.analyzer import register_resolver, DependencyEdge
from pathlib import Path
import re

def resolve_dart_import(import_stmt: str, from_file: str, mapper) -> DependencyEdge:
    """
    Resolve Dart import statements to file paths.
    
    Handles:
    - Relative imports: import '../models/foo.dart'
    - Package imports: import 'package:myapp/bar.dart'
    - Dart SDK imports: import 'dart:async' (marked as external)
    """
    # Extract the import path
    match = re.search(r"import\s+['\"]([^'\"]+)['\"]", import_stmt)
    if not match:
        return None
    
    import_path = match.group(1)
    
    # Dart SDK imports (external)
    if import_path.startswith('dart:'):
        return DependencyEdge(
            from_file=from_file,
            to_file=import_path,
            import_statement=import_stmt,
            is_external=True
        )
    
    # Package imports (external for now, could map to pub cache)
    if import_path.startswith('package:'):
        return DependencyEdge(
            from_file=from_file,
            to_file=import_path,
            import_statement=import_stmt,
            is_external=True
        )
    
    # Relative imports
    from_path = Path(from_file).parent
    target_path = (from_path / import_path).resolve()
    
    # Check if target exists in repo
    if target_path.exists() and target_path.is_relative_to(mapper.repo_root):
        relative_to_root = target_path.relative_to(mapper.repo_root)
        return DependencyEdge(
            from_file=from_file,
            to_file=str(relative_to_root),
            import_statement=import_stmt,
            is_external=False
        )
    
    return None

# Register the resolver
register_resolver('dart', resolve_dart_import)

# Now Dart files will have their dependencies mapped!
from thepipe.scraper import scrape_directory

chunks = scrape_directory(
    '/path/to/flutter/app',
    include_patterns=['**/*.dart'],
    options={'code_relations': 'map'}
)
```

## Resolver Function Signature

```python
def my_language_resolver(
    import_stmt: str,           # Full import line from source
    from_file: str,              # File containing the import
    mapper: DependencyMapper     # Mapper instance (has .repo_root, ._file_index)
) -> Optional[DependencyEdge]:
    """
    Parse import and resolve to file path.
    
    Returns:
        DependencyEdge with resolved path, or None if can't resolve
    """
    pass
```

## Language-Specific Patterns

### Swift
```python
def resolve_swift_import(import_stmt, from_file, mapper):
    # import MyModule
    # import class MyModule.MyClass
    match = re.search(r'import\s+(?:class\s+)?(\w+)', import_stmt)
    if match:
        module = match.group(1)
        # Look for ModuleName.swift in file index
        return mapper._file_index.get(module)
```

### Kotlin
```python
def resolve_kotlin_import(import_stmt, from_file, mapper):
    # import com.example.myapp.MyClass
    match = re.search(r'import\s+([\w.]+)', import_stmt)
    if match:
        import_path = match.group(1).replace('.', '/')
        # Look for com/example/myapp/MyClass.kt
```

### Ruby
```python
def resolve_ruby_import(import_stmt, from_file, mapper):
    # require 'foo/bar'
    # require_relative '../baz'
    if 'require_relative' in import_stmt:
        # Handle relative paths
        match = re.search(r"require_relative\s+['\"]([^'\"]+)['\"]", import_stmt)
    else:
        # Handle gem/library requires
        match = re.search(r"require\s+['\"]([^'\"]+)['\"]", import_stmt)
```

## Installation

Just import and call `register_resolver()` before running thepipe:

```python
# my_extensions.py
from thepipe.analyzer import register_resolver
from my_resolvers import resolve_dart_import, resolve_swift_import

register_resolver('dart', resolve_dart_import)
register_resolver('swift', resolve_swift_import)

# Now in your code
import my_extensions  # Registers resolvers on import
from thepipe.scraper import scrape_directory

chunks = scrape_directory('/', options={'code_relations': 'map'})
```

## Community Contributions

We encourage community members to contribute resolvers! Common patterns:
1. Parse import statement with regex
2. Map module name to file path
3. Handle relative vs absolute imports
4. Mark external dependencies (SDKs, packages)
5. Return DependencyEdge or None

Submit your resolvers as examples to help others!
