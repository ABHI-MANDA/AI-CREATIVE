import pytest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.learning import record_review, get_context, summary, clean
from core import config


def test_clean():
    """Test the clean function."""
    assert clean("  hello  world  ") == "hello world"
    assert clean(None) == ""
    assert clean("x" * 5000, n=100) == "x" * 100


def test_record_review():
    """Test recording a review."""
    # Use a unique project ID to avoid conflicts
    project_id = f"test_{int(time.time() * 1000)}"
    
    review = record_review(
        project_id=project_id,
        ctype="image",
        prompt="Test prompt for image generation",
        output_filename="test_image.png",
        approved=True,
        notes="Looks good",
        rating=5
    )
    
    assert review["project_id"] == project_id
    assert review["creative_type"] == "image"
    assert review["approved"] is True
    assert review["rating"] == 5
    assert review["notes"] == "Looks good"
    
    # Check that it was saved
    mem = summary()
    assert mem["stats"]["reviews"] >= 1
    assert mem["stats"]["approved"] >= 1


def test_record_review_rejected():
    """Test recording a rejected review."""
    project_id = f"test_reject_{int(time.time() * 1000)}"
    
    review = record_review(
        project_id=project_id,
        ctype="video",
        prompt="Test video prompt",
        output_filename="test_video.mp4",
        approved=False,
        notes="Too dark, increase brightness",
        rating=2
    )
    
    assert review["approved"] is False
    assert review["rating"] == 2
    assert review["notes"] == "Too dark, increase brightness"
    
    mem = summary()
    assert mem["stats"]["rejected"] >= 1


def test_get_context():
    """Test retrieving context for a creative type."""
    project_id = f"test_ctx_{int(time.time() * 1000)}"
    
    # Add some reviews
    record_review(project_id, "image", "Prompt 1", "out1.png", True, "Good", 5)
    record_review(project_id, "image", "Prompt 2", "out2.png", False, "Too dark", 2)
    record_review(project_id, "copy", "Prompt 3", "out3.txt", True, "Great copy", 5)
    
    context = get_context("image")
    
    assert "LEARNED REVIEW LESSONS" in context
    assert "APPROVED" in context or "CORRECTION" in context
    assert "Good" in context
    assert "Too dark" in context
    
    # Copy context should not include image lessons
    copy_context = get_context("copy")
    assert "Great copy" in copy_context