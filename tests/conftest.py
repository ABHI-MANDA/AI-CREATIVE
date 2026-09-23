"""Shared test configuration and fixtures."""
import pytest
import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set test environment before importing app
os.environ["ENVIRONMENT"] = "development"
os.environ["FLASK_DEBUG"] = "0"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only-min-32-chars-long"
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["AGNES_API_KEY"] = ""

# Create a temporary data directory for tests
TEST_DATA_DIR = tempfile.mkdtemp(prefix="ai_creative_test_")

# Override config paths before importing modules that use them
import core.config as config_module
config_module.DATA_DIR = os.path.join(TEST_DATA_DIR, "data")
config_module.OUTPUT_DIR = os.path.join(config_module.DATA_DIR, "outputs")
config_module.REFERENCE_DIR = os.path.join(config_module.DATA_DIR, "reference_assets")
config_module.REVIEW_STORE = os.path.join(config_module.DATA_DIR, "reviews.json")
config_module.PROJECT_STORE = os.path.join(config_module.DATA_DIR, "projects.json")
config_module.MEMORY_STORE = os.path.join(config_module.DATA_DIR, "creative_memory.json")

for path in (config_module.DATA_DIR, config_module.OUTPUT_DIR, config_module.REFERENCE_DIR):
    os.makedirs(path, exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_data():
    """Clean up test data directory after all tests."""
    yield
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_modules():
    """Reset module state before each test."""
    import app as app_module
    import core.store as store_module
    import core.learning as learning_module
    
    with app_module.PROJECTS_LOCK:
        app_module.PROJECTS.clear()
    
    # Clear store files
    for fname in ["projects.json", "reviews.json", "creative_memory.json"]:
        fpath = os.path.join(config_module.DATA_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)
    
    yield
    
    # Cleanup after
    with app_module.PROJECTS_LOCK:
        app_module.PROJECTS.clear()