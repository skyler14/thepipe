"""
Semantic Tagger

Tags code locations with semantic concerns like #oauth, #async, #database, etc.
"""

import re
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field

from .types import SemanticTag, FileAnalysis, ASTNode


# Pattern-based semantic tags
SEMANTIC_PATTERNS: Dict[str, List[str]] = {
    "oauth": [
        r'\boauth\b', r'\bsso\b', r'\bopenid\b', r'\bbearer_token\b',
        r'\baccess_token\b', r'\brefresh_token\b', r'\bJWT\b', r'\bjwt\b',
    ],
    "auth": [
        r'\bauthenticat', r'\bauthoriz', r'\blogin\b', r'\blogout\b',
        r'\bpassword\b', r'\bcredential', r'\bpermission',
    ],
    "state_machine": [
        r'\bstate\s*=', r'\benum.*state\b', r'\bFSM\b', r'\btransition\b',
        r'\bstate_machine\b', r'\bStateMachine\b',
    ],
    "networking": [
        r'\brequests\b', r'\burllib\b', r'\bsocket\b', r'\bhttp\b',
        r'\bfetch\b', r'\baxios\b', r'\baiohttp\b', r'\bwebsocket\b',
    ],
    "async": [
        r'\basync\s+def\b', r'\bawait\b', r'\.then\s*\(', r'\bPromise\b',
        r'\bcallback\b', r'\basyncio\b', r'\bEventLoop\b',
    ],
    "crypto": [
        r'\bencrypt', r'\bdecrypt', r'\bhash\b', r'\bsha256\b',
        r'\brsa\b', r'\baes\b', r'\bhmac\b', r'\bcrypto\b',
    ],
    "database": [
        r'\bquery\b', r'\bSELECT\b', r'\bINSERT\b', r'\bUPDATE\b',
        r'\borm\b', r'\bmigration', r'\bsqlalchemy\b', r'\bprisma\b',
        r'\bmongoose\b', r'\bdatabase\b', r'\bconnection\b',
    ],
    "testing": [
        r'\bdef test_', r'\bassert\b', r'\bmock\b', r'\bpatch\b',
        r'\bfixture\b', r'\bpytest\b', r'\bjest\b', r'\bdescribe\b',
    ],
    "config": [
        r'\bconfig\b', r'\bsettings\b', r'\benvironment\b',
        r'\benv\.', r'\.env\b', r'\bload_dotenv\b',
    ],
    "logging": [
        r'\blogger\b', r'\blogging\b', r'\blog\.', r'\bdebug\b',
        r'\binfo\b', r'\bwarning\b', r'\berror\b',
    ],
    "file_io": [
        r'\bopen\s*\(', r'\bread\s*\(', r'\bwrite\s*\(',
        r'\bPath\b', r'\bpathlib\b', r'\bos\.path\b',
    ],
    "parsing": [
        r'\bparser\b', r'\bparse\b', r'\bast\b', r'\btree_sitter\b',
        r'\bBeautifulSoup\b', r'\blxml\b', r'\bjson\.load',
    ],
    "ml": [
        r'\btorch\b', r'\btensorflow\b', r'\bkeras\b', r'\bmodel\b',
        r'\bpredict\b', r'\btrain\b', r'\bepoch\b', r'\bembedding\b',
    ],
    "api": [
        r'\bendpoint\b', r'\broute\b', r'\b@app\.\b', r'\bREST\b',
        r'\bGraphQL\b', r'\bOpenAPI\b', r'\bswagger\b',
    ],
}


class SemanticTagger:
    """Tags code with semantic concerns"""
    
    def __init__(self, custom_patterns: Optional[Dict[str, List[str]]] = None):
        self.patterns = {**SEMANTIC_PATTERNS}
        if custom_patterns:
            for tag, pats in custom_patterns.items():
                if tag in self.patterns:
                    self.patterns[tag].extend(pats)
                else:
                    self.patterns[tag] = pats
        
        # Compile patterns
        self._compiled: Dict[str, re.Pattern] = {}
        for tag, pats in self.patterns.items():
            combined = '|'.join(f'({p})' for p in pats)
            self._compiled[tag] = re.compile(combined, re.IGNORECASE)
    
    def tag_content(self, content: str, filepath: str = "") -> List[SemanticTag]:
        """
        Scan content for semantic patterns.
        
        Returns list of tags found.
        """
        tags = []
        
        for tag, pattern in self._compiled.items():
            matches = list(pattern.finditer(content))
            if matches:
                # Find line numbers
                locations = []
                for match in matches[:5]:  # Limit to 5 locations per tag
                    line_num = content[:match.start()].count('\n') + 1
                    locations.append((filepath, line_num))
                
                tags.append(SemanticTag(
                    tag=tag,
                    confidence=min(1.0, len(matches) * 0.2),  # More matches = higher confidence
                    source="pattern",
                    file=filepath,
                    line=locations[0][1] if locations else None,
                ))
        
        # Also check for explicit @tag comments
        explicit_tags = self._extract_comment_tags(content, filepath)
        tags.extend(explicit_tags)
        
        return tags
    
    def _extract_comment_tags(self, content: str, filepath: str) -> List[SemanticTag]:
        """Extract explicit @tag: xyz or #tag comments"""
        tags = []
        
        # Match @tag: name or # tag: name
        tag_pattern = re.compile(r'[@#]\s*tag:\s*(\w+)', re.IGNORECASE)
        concern_pattern = re.compile(r'[@#]\s*concern:\s*(\w+)', re.IGNORECASE)
        
        for pattern in [tag_pattern, concern_pattern]:
            for match in pattern.finditer(content):
                line_num = content[:match.start()].count('\n') + 1
                tags.append(SemanticTag(
                    tag=match.group(1).lower(),
                    confidence=1.0,  # Explicit tags have full confidence
                    source="comment",
                    file=filepath,
                    line=line_num,
                ))
        
        return tags
    
    def tag_file(self, filepath: str) -> List[SemanticTag]:
        """Tag a file by reading its contents"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return self.tag_content(content, filepath)
        except Exception:
            return []
    
    def tag_analysis(self, analysis: FileAnalysis, source: str) -> List[SemanticTag]:
        """Tag based on FileAnalysis + source"""
        return self.tag_content(source, analysis.path)


def build_semantic_index(
    file_tags: Dict[str, List[SemanticTag]]
) -> Dict[str, List[str]]:
    """
    Build an index from tag -> [files].
    
    Useful for quick lookups like "find all oauth-related files".
    """
    index: Dict[str, Set[str]] = {}
    
    for filepath, tags in file_tags.items():
        for tag in tags:
            if tag.tag not in index:
                index[tag.tag] = set()
            index[tag.tag].add(filepath)
    
    return {tag: list(files) for tag, files in index.items()}
