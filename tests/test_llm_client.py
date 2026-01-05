"""
Tests for the unified LLM client module.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from thepipe.llm import LLMClient, LLMConfig, LLMResponse


class TestLLMConfig:
    """Tests for LLMConfig dataclass."""
    
    def test_default_values(self):
        config = LLMConfig()
        assert config.provider == "openai"
        assert config.model == "gpt-4o"
        assert config.temperature == 0.0
        assert config.timeout == 300
    
    def test_from_env_with_api_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            config = LLMConfig.from_env(provider="openai")
            assert config.api_key == "test-key"
            assert config.provider == "openai"
    
    def test_from_env_auto_detect_agent(self):
        # When ANTIGRAVITY_SESSION is set, should auto-detect agent mode
        with patch.dict(os.environ, {"ANTIGRAVITY_SESSION": "test-session"}, clear=False):
            config = LLMConfig.from_env()
            assert config.provider == "agent"


class TestLLMClient:
    """Tests for LLMClient."""
    
    def test_client_creation(self):
        client = LLMClient(provider="openai", api_key="test-key", model="gpt-4")
        assert client.config.provider == "openai"
        assert client.config.api_key == "test-key"
        assert client.config.model == "gpt-4"
    
    def test_auto_client(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "auto-key"}):
            client = LLMClient.auto(model="gpt-3.5-turbo")
            assert client.config.api_key == "auto-key"
            assert client.config.model == "gpt-3.5-turbo"
    
    def test_cache_key_generation(self):
        client = LLMClient()
        messages = [{"role": "user", "content": "Hello"}]
        key1 = client._cache_key(messages, model="gpt-4")
        key2 = client._cache_key(messages, model="gpt-4")
        key3 = client._cache_key(messages, model="gpt-3.5")
        
        assert key1 == key2  # Same messages + model = same key
        assert key1 != key3  # Different model = different key
    
    @patch("openai.OpenAI")
    def test_openai_query(self, mock_openai_class):
        # Setup mock
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello back!"
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        mock_client.chat.completions.create.return_value = mock_response
        
        # Execute
        client = LLMClient(provider="openai", api_key="test-key")
        response = client.query([{"role": "user", "content": "Hello"}])
        
        # Verify
        assert response.content == "Hello back!"
        assert response.provider == "openai"
        assert response.input_tokens == 10
        assert response.output_tokens == 5
    
    def test_query_json(self):
        client = LLMClient(provider="openai", api_key="test-key")
        
        # Mock the query method
        with patch.object(client, 'query') as mock_query:
            mock_query.return_value = LLMResponse(
                content='{"title": "Test", "value": 42}',
                model="gpt-4",
                provider="openai"
            )
            
            result = client.query_json([{"role": "user", "content": "Extract"}])
            
            assert result == {"title": "Test", "value": 42}


class TestNamedPipeAgentMode:
    """Tests for the named pipe (FIFO) agent communication."""
    
    def test_pipe_creation(self):
        """Test that pipes are created in the correct location."""
        pipe_dir = Path(tempfile.gettempdir()) / "thepipe_pipes"
        
        # Verify we can create pipes there
        pipe_dir.mkdir(exist_ok=True)
        test_pipe = pipe_dir / f"test_{os.getpid()}"
        
        if test_pipe.exists():
            test_pipe.unlink()
        
        os.mkfifo(str(test_pipe))
        assert test_pipe.exists()
        
        # Cleanup
        test_pipe.unlink()
    
    def test_fifo_bidirectional_communication(self):
        """Test that FIFOs work for bidirectional communication."""
        pipe_dir = Path(tempfile.gettempdir()) / "thepipe_pipes"
        pipe_dir.mkdir(exist_ok=True)
        
        query_pipe = pipe_dir / f"test_query_{os.getpid()}"
        response_pipe = pipe_dir / f"test_response_{os.getpid()}"
        
        # Clean up any existing pipes
        for p in [query_pipe, response_pipe]:
            if p.exists():
                p.unlink()
            os.mkfifo(str(p))
        
        test_query = {"messages": [{"role": "user", "content": "Test"}]}
        test_response = "This is the response"
        received_query = None
        
        def agent_simulator():
            """Simulates an agent reading query and writing response."""
            nonlocal received_query
            # Read query
            with open(query_pipe, 'r') as qp:
                received_query = json.loads(qp.read())
            # Write response
            with open(response_pipe, 'w') as rp:
                rp.write(test_response)
        
        # Start agent simulator in background
        agent_thread = threading.Thread(target=agent_simulator)
        agent_thread.start()
        
        # thepipe side: write query, read response
        with open(query_pipe, 'w') as qp:
            qp.write(json.dumps(test_query))
        
        with open(response_pipe, 'r') as rp:
            received_response = rp.read()
        
        agent_thread.join(timeout=5)
        
        # Verify
        assert received_query == test_query
        assert received_response == test_response
        
        # Cleanup
        for p in [query_pipe, response_pipe]:
            if p.exists():
                p.unlink()
    
    def test_agent_query_integration(self):
        """Integration test for agent mode query via FIFOs."""
        client = LLMClient(provider="agent")
        
        test_response = "Agent processed this response"
        
        def mock_agent():
            """Mock agent that reads query and writes response."""
            time.sleep(0.1)  # Wait for pipes to be created
            
            pipe_dir = Path(tempfile.gettempdir()) / "thepipe_pipes"
            query_pipe = pipe_dir / f"query_{os.getpid()}"
            response_pipe = pipe_dir / f"response_{os.getpid()}"
            
            # Wait for query pipe to exist
            for _ in range(50):
                if query_pipe.exists():
                    break
                time.sleep(0.1)
            
            # Read query
            with open(query_pipe, 'r') as qp:
                query = json.loads(qp.read())
            
            # Write response
            with open(response_pipe, 'w') as rp:
                rp.write(test_response)
        
        # Start mock agent
        agent_thread = threading.Thread(target=mock_agent)
        agent_thread.start()
        
        # Execute query
        try:
            response = client.query([{"role": "user", "content": "Test message"}])
            assert response.content == test_response
            assert response.provider == "agent"
        finally:
            agent_thread.join(timeout=10)


class TestLLMResponse:
    """Tests for LLMResponse dataclass."""
    
    def test_response_creation(self):
        response = LLMResponse(
            content="Test content",
            model="gpt-4",
            provider="openai",
            input_tokens=100,
            output_tokens=50
        )
        
        assert response.content == "Test content"
        assert response.model == "gpt-4"
        assert response.cached == False
    
    def test_cached_response(self):
        response = LLMResponse(
            content="Cached",
            model="gpt-4",
            provider="openai",
            cached=True
        )
        assert response.cached == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
