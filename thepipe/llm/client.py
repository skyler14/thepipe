"""
Unified LLM Query Framework

Provides a single interface for all LLM operations in thepipe with support for:
- Multiple providers (OpenAI, Anthropic, Ollama, etc.)
- Agent mode: delegate inference to calling agent
- Caching and retry logic
"""

import json
import os
import sys
import time
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Marker strings for agent communication protocol
QUERY_START = "<<<THEPIPE_LLM_QUERY>>>"
QUERY_END = "<<<END_QUERY>>>"
RESPONSE_END = "<<<END_RESPONSE>>>"


@dataclass
class LLMConfig:
    """Configuration for LLM client."""
    provider: str = "openai"  # openai, anthropic, ollama, agent
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: str = "gpt-4o"
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    timeout: int = 300  # seconds
    retry_count: int = 3
    retry_delay: float = 1.0
    cache_enabled: bool = False
    cache_dir: Optional[str] = None
    
    @classmethod
    def from_env(cls, provider: Optional[str] = None) -> "LLMConfig":
        """Create config from environment variables."""
        # Auto-detect agent mode
        if provider is None:
            if os.getenv("ANTIGRAVITY_SESSION") or os.getenv("GEMINI_API_KEY"):
                provider = "agent"
            else:
                provider = "openai"
        
        return cls(
            provider=provider,
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("LLM_SERVER_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_SERVER_BASE_URL"),
            model=os.getenv("LLM_MODEL", "gpt-4o"),
        )


@dataclass
class LLMResponse:
    """Response from LLM query."""
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    raw_response: Optional[Any] = None


class LLMClient:
    """
    Unified LLM client for all thepipe operations.
    
    Usage:
        client = LLMClient(provider="openai", api_key="...")
        response = client.query([{"role": "user", "content": "Hello"}])
        
        # Agent mode
        client = LLMClient(provider="agent")
        response = client.query(messages)  # Delegates to calling agent
    """
    
    def __init__(
        self,
        provider: str = "openai",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-4o",
        **kwargs
    ):
        self.config = LLMConfig(
            provider=provider,
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url,
            model=model,
            **{k: v for k, v in kwargs.items() if hasattr(LLMConfig, k)}
        )
        self._client = None
        self._cache: Dict[str, str] = {}
    
    @classmethod
    def from_config(cls, config: LLMConfig) -> "LLMClient":
        """Create client from config object."""
        client = cls.__new__(cls)
        client.config = config
        client._client = None
        client._cache = {}
        return client
    
    @classmethod
    def auto(cls, **overrides) -> "LLMClient":
        """Auto-detect provider and create client."""
        config = LLMConfig.from_env()
        for key, value in overrides.items():
            if hasattr(config, key):
                setattr(config, key, value)
        return cls.from_config(config)
    
    def _get_openai_client(self):
        """Get or create OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI
                client_args = {}
                if self.config.api_key:
                    client_args["api_key"] = self.config.api_key
                if self.config.base_url:
                    client_args["base_url"] = self.config.base_url
                self._client = OpenAI(**client_args)
            except ImportError:
                raise ImportError("OpenAI SDK required. Install with: pip install openai")
        return self._client
    
    def _cache_key(self, messages: List[Dict], **kwargs) -> str:
        """Generate cache key for messages."""
        content = json.dumps({"messages": messages, **kwargs}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def query(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Execute an LLM query.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Override model from config
            temperature: Override temperature
            max_tokens: Override max tokens
            response_format: Optional response format (e.g., {"type": "json_object"})
            **kwargs: Additional provider-specific args
            
        Returns:
            LLMResponse with content and metadata
        """
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature
        
        # Check cache
        if self.config.cache_enabled:
            cache_key = self._cache_key(messages, model=model, temperature=temperature)
            if cache_key in self._cache:
                return LLMResponse(
                    content=self._cache[cache_key],
                    model=model,
                    provider=self.config.provider,
                    cached=True
                )
        
        # Route to appropriate provider
        if self.config.provider == "agent":
            response = self._agent_query(messages, model=model)
        elif self.config.provider in ("openai", "openrouter"):
            response = self._openai_query(
                messages, 
                model=model, 
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                **kwargs
            )
        else:
            raise ValueError(f"Unknown provider: {self.config.provider}")
        
        # Cache response
        if self.config.cache_enabled and not response.cached:
            cache_key = self._cache_key(messages, model=model, temperature=temperature)
            self._cache[cache_key] = response.content
        
        return response
    
    def query_json(
        self,
        messages: List[Dict[str, Any]],
        schema: Optional[Dict] = None,
        **kwargs
    ) -> Dict:
        """
        Query and parse JSON response.
        
        Args:
            messages: List of message dicts
            schema: Optional JSON schema for validation
            **kwargs: Passed to query()
            
        Returns:
            Parsed JSON dict
        """
        response = self.query(
            messages, 
            response_format={"type": "json_object"},
            **kwargs
        )
        
        try:
            return json.loads(response.content)
        except json.JSONDecodeError as e:
            # Try to extract JSON from response
            import re
            match = re.search(r'\{.*\}', response.content, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"Failed to parse JSON from response: {e}")
    
    def _openai_query(
        self,
        messages: List[Dict],
        model: str,
        temperature: float,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict] = None,
        **kwargs
    ) -> LLMResponse:
        """Execute query via OpenAI SDK."""
        client = self._get_openai_client()
        
        # Build request
        request_args = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            request_args["max_tokens"] = max_tokens
        if response_format:
            request_args["response_format"] = response_format
        request_args.update(kwargs)
        
        # Execute with retry
        last_error = None
        for attempt in range(self.config.retry_count):
            try:
                response = client.chat.completions.create(**request_args)
                
                content = response.choices[0].message.content or ""
                usage = response.usage
                
                return LLMResponse(
                    content=content,
                    model=model,
                    provider=self.config.provider,
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                    raw_response=response
                )
            except Exception as e:
                last_error = e
                if attempt < self.config.retry_count - 1:
                    delay = self.config.retry_delay * (2 ** attempt)
                    logger.warning(f"LLM query failed (attempt {attempt+1}), retrying in {delay}s: {e}")
                    time.sleep(delay)
        
        raise last_error or Exception("LLM query failed")
    
    def _agent_query(
        self,
        messages: List[Dict],
        model: str,
    ) -> LLMResponse:
        """
        Delegate query to calling agent via stdin/stdout protocol.
        
        The agent reads the query from stdout, executes it, and writes
        the response to stdin.
        """
        query_payload = {
            "messages": messages,
            "model": model,
            "response_format": "text",
        }
        
        # Output query for agent to process
        print(f"{QUERY_START}", file=sys.stderr, flush=True)
        print(json.dumps(query_payload, indent=2), file=sys.stderr, flush=True)
        print(f"{QUERY_END}", file=sys.stderr, flush=True)
        
        # Also print user-friendly message
        print(f"\n[thepipe] Waiting for agent to provide LLM response...", file=sys.stderr, flush=True)
        print(f"[thepipe] Paste response below, then type {RESPONSE_END} on a new line:", file=sys.stderr, flush=True)
        
        # Read response from stdin
        response_lines = []
        start_time = time.time()
        
        try:
            for line in sys.stdin:
                if time.time() - start_time > self.config.timeout:
                    raise TimeoutError(f"Agent response timeout after {self.config.timeout}s")
                
                line = line.rstrip('\n')
                if line == RESPONSE_END:
                    break
                response_lines.append(line)
        except KeyboardInterrupt:
            raise RuntimeError("Agent query interrupted")
        
        content = '\n'.join(response_lines)
        
        return LLMResponse(
            content=content,
            model=model,
            provider="agent",
            input_tokens=0,  # Agent tracks its own tokens
            output_tokens=0,
        )


# Convenience function for simple queries
def query(
    prompt: str,
    *,
    system: Optional[str] = None,
    provider: str = "openai",
    model: str = "gpt-4o",
    **kwargs
) -> str:
    """
    Simple query interface.
    
    Args:
        prompt: User message
        system: Optional system message
        provider: LLM provider
        model: Model to use
        **kwargs: Additional args
        
    Returns:
        Response content string
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    
    client = LLMClient(provider=provider, model=model, **kwargs)
    response = client.query(messages)
    return response.content
