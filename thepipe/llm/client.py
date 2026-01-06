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
    
    @classmethod
    def from_options(
        cls,
        options: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> "LLMClient":
        """
        Create client from thepipe options dict.
        
        This is the primary way to get an LLMClient in thepipe code.
        It reads llm_provider from options and falls back to env vars.
        
        Args:
            options: thepipe options dict (may contain 'llm_provider')
            api_key: Explicit API key (overrides options)
            base_url: Explicit base URL
            model: Explicit model name
        """
        options = options or {}
        
        # Determine provider from options, default to openai
        provider = options.get('llm_provider', 'openai')
        
        # If explicit API key provided, always use openai
        if api_key:
            provider = 'openai'
        
        return cls(
            provider=provider,
            api_key=api_key or options.get('api_key'),
            base_url=base_url or options.get('api_base'),
            model=model or options.get('model', 'gpt-4o'),
        )
    
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
        timeout: int = 300,  # 5 minute default timeout
    ) -> LLMResponse:
        """
        Delegate query to calling agent via Named Pipes (FIFOs).
        
        Named pipes provide true bidirectional, synchronous communication:
        1. thepipe creates two FIFOs: query_pipe and response_pipe
        2. thepipe writes the query to query_pipe
        3. Agent reads from query_pipe, processes the LLM call
        4. Agent writes response to response_pipe
        5. thepipe reads response and continues
        
        This allows thepipe to pause execution while the agent handles
        the LLM inference, then seamlessly resume with the response.
        """
        import tempfile
        import stat
        import select
        import threading
        
        # Create unique pipe names using PID + timestamp to avoid collisions
        pid = os.getpid()
        timestamp = int(time.time() * 1000)  # Millisecond precision
        pipe_dir = Path(tempfile.gettempdir()) / "thepipe_pipes"
        pipe_dir.mkdir(mode=0o700, exist_ok=True)  # Secure permissions
        
        query_pipe = pipe_dir / f"query_{pid}_{timestamp}"
        response_pipe = pipe_dir / f"response_{pid}_{timestamp}"
        
        # Track created pipes for cleanup
        created_pipes = []
        
        def cleanup_pipes():
            """Clean up FIFO pipes."""
            for pipe in created_pipes:
                try:
                    if pipe.exists():
                        pipe.unlink()
                except Exception as e:
                    logger.debug(f"Failed to clean up pipe {pipe}: {e}")
        
        try:
            # Create named pipes (FIFOs)
            for pipe in [query_pipe, response_pipe]:
                if pipe.exists():
                    pipe.unlink()  # Remove stale pipe
                os.mkfifo(str(pipe), mode=0o600)  # Secure permissions
                created_pipes.append(pipe)
            
            # Prepare query payload
            query_payload = {
                "type": "llm_query",
                "messages": messages,
                "model": model,
                "response_format": "text",
                "query_pipe": str(query_pipe),
                "response_pipe": str(response_pipe),
                "timeout": timeout,
            }
            
            # Notify agent via stderr about the pipes
            print(f"\n{QUERY_START}", file=sys.stderr, flush=True)
            print(f"QUERY_PIPE: {query_pipe}", file=sys.stderr, flush=True)
            print(f"RESPONSE_PIPE: {response_pipe}", file=sys.stderr, flush=True)
            print(json.dumps(query_payload, indent=2), file=sys.stderr, flush=True)
            print(f"{QUERY_END}", file=sys.stderr, flush=True)
            
            # Use threading with timeout for pipe operations
            write_result = {"error": None}
            read_result = {"content": None, "error": None}
            
            def write_query():
                try:
                    with open(query_pipe, 'w') as qp:
                        qp.write(json.dumps(query_payload))
                        qp.flush()
                except Exception as e:
                    write_result["error"] = e
            
            def read_response():
                try:
                    with open(response_pipe, 'r') as rp:
                        read_result["content"] = rp.read()
                except Exception as e:
                    read_result["error"] = e
            
            # Start write in thread (blocks until reader connects)
            logger.info(f"Writing query to {query_pipe}")
            start_time = time.time()
            
            # Split timeout between write and read phases
            write_timeout = timeout // 2
            
            write_thread = threading.Thread(target=write_query, daemon=True)
            write_thread.start()
            write_thread.join(timeout=write_timeout)
            
            if write_thread.is_alive():
                raise TimeoutError(f"Timeout after {write_timeout}s waiting for agent to read query")
            if write_result["error"]:
                raise write_result["error"]
            
            # Use remaining timeout for read
            elapsed = time.time() - start_time
            remaining_timeout = max(timeout - elapsed, 10)  # At least 10s for read
            
            # Read response in thread with timeout
            logger.info(f"Waiting for response on {response_pipe} (timeout: {remaining_timeout:.0f}s)")
            read_thread = threading.Thread(target=read_response, daemon=True)
            read_thread.start()
            read_thread.join(timeout=remaining_timeout)
            
            if read_thread.is_alive():
                raise TimeoutError(f"Timeout after {remaining_timeout:.0f}s waiting for agent response")
            if read_result["error"]:
                raise read_result["error"]
            
            content = read_result["content"]
            if content is None:
                raise RuntimeError("No response received from agent")
            
            elapsed = time.time() - start_time
            logger.info(f"Received response in {elapsed:.2f}s")
            
            return LLMResponse(
                content=content.strip(),
                model=model,
                provider="agent",
                input_tokens=0,  # Agent tracks its own tokens
                output_tokens=0,
            )
            
        except TimeoutError as e:
            logger.error(f"Agent query timed out: {e}")
            raise
        except Exception as e:
            logger.error(f"Agent query failed: {e}")
            raise RuntimeError(f"Agent communication failed: {e}")
        finally:
            cleanup_pipes()


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
