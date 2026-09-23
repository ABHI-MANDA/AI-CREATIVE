import pytest
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENVIRONMENT"] = "development"
os.environ["FLASK_DEBUG"] = "0"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def cleanup():
    """Clean up test data before and after each test."""
    # Clear PROJECTS dict
    import app as app_module
    with app_module.PROJECTS_LOCK:
        app_module.PROJECTS.clear()
    
    # Remove test data files
    for fname in ["projects.json", "reviews.json", "creative_memory.json"]:
        fpath = os.path.join("data", fname)
        if os.path.exists(fpath):
            os.remove(fpath)
    
    yield
    
    # Cleanup after
    with app_module.PROJECTS_LOCK:
        app_module.PROJECTS.clear()


def test_health_endpoint(client):
    """Test the health endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["ok"] is True
    assert data["app"] == "AI Creative Studio"
    assert "environment" in data


def test_create_project(client):
    """Test creating a new project."""
    response = client.post("/api/projects", json={
        "brief": "Test campaign for a new product launch",
        "creative_types": ["copy", "image"]
    })
    assert response.status_code == 201
    data = json.loads(response.data)
    assert "id" in data
    assert data["brief"] == "Test campaign for a new product launch"
    assert data["creative_types"] == ["copy", "image"]
    assert data["stage"] == "created"


def test_create_project_validation(client):
    """Test project creation validation."""
    # Missing brief
    response = client.post("/api/projects", json={
        "creative_types": ["image"]
    })
    assert response.status_code == 400
    
    # Invalid creative type
    response = client.post("/api/projects", json={
        "brief": "Test",
        "creative_types": ["invalid"]
    })
    assert response.status_code == 400


def test_get_project(client):
    """Test retrieving a project."""
    # Create project first
    create_resp = client.post("/api/projects", json={
        "brief": "Test brief",
        "creative_types": ["image"]
    })
    project = json.loads(create_resp.data)
    pid = project["id"]
    
    # Get project
    response = client.get(f"/api/projects/{pid}")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["id"] == pid


def test_get_nonexistent_project(client):
    """Test retrieving a nonexistent project."""
    response = client.get("/api/projects/nonexistent")
    assert response.status_code == 404


def test_models_endpoint(client):
    """Test the models endpoint."""
    response = client.get("/api/models")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "copy" in data
    assert "image" in data
    assert "video" in data
    assert isinstance(data["copy"], list)
    assert len(data["copy"]) > 0


def test_project_models_endpoint(client):
    """Test the project-specific models endpoint."""
    create_resp = client.post("/api/projects", json={
        "brief": "Test brief",
        "creative_types": ["copy", "image"]
    })
    project = json.loads(create_resp.data)
    pid = project["id"]
    
    response = client.get(f"/api/projects/{pid}/models")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "copy" in data
    assert "image" in data
    assert "video" not in data  # Not requested


def test_upload_reference_invalid(client):
    """Test uploading reference without file."""
    create_resp = client.post("/api/projects", json={
        "brief": "Test brief",
        "creative_types": ["image"]
    })
    project = json.loads(create_resp.data)
    pid = project["id"]
    
    response = client.post(f"/api/projects/{pid}/upload-reference")
    assert response.status_code == 400