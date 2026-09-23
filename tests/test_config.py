import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config


def test_config_loading():
    """Test that configuration loads correctly."""
    assert config.APP_NAME == "AI Creative Studio"
    assert config.HOST == "127.0.0.1"
    assert config.PORT == 5000
    assert config.ENVIRONMENT in ("development", "production")


def test_env_int():
    """Test env_int helper function."""
    from core.config import env_int
    assert env_int("NONEXISTENT", 10) == 10
    assert env_int("NONEXISTENT", 5, minimum=10) == 10


def test_api_key_filtering():
    """Test that placeholder API keys are filtered out."""
    from core.config import api_key
    import os
    
    os.environ["TEST_KEY"] = "your-key-here"
    assert api_key("TEST_KEY") == ""
    
    os.environ["TEST_KEY"] = "real-key-123"
    assert api_key("TEST_KEY") == "real-key-123"
    
    del os.environ["TEST_KEY"]