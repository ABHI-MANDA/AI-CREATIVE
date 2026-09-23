import pytest
import tempfile
import os
import json
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.store import load_json, save_json


def test_save_and_load_json():
    """Test saving and loading JSON with atomic writes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "test.json")
        test_data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        
        save_json(filepath, test_data)
        loaded = load_json(filepath, {})
        
        assert loaded == test_data


def test_load_nonexistent_returns_default():
    """Test that loading a nonexistent file returns the default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "nonexistent.json")
        default = {"default": True}
        
        loaded = load_json(filepath, default)
        
        assert loaded == default


def test_load_corrupted_json_returns_default():
    """Test that loading corrupted JSON returns the default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "corrupted.json")
        with open(filepath, "w") as f:
            f.write("{ invalid json")
        
        default = {"default": True}
        loaded = load_json(filepath, default)
        
        assert loaded == default


def test_atomic_write():
    """Test that save_json uses atomic writes (temp file + rename)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "atomic.json")
        
        save_json(filepath, {"version": 1})
        save_json(filepath, {"version": 2})
        
        loaded = load_json(filepath, {})
        assert loaded == {"version": 2}
        
        # No temp files should remain
        files = os.listdir(tmpdir)
        assert len(files) == 1
        assert files[0] == "atomic.json"