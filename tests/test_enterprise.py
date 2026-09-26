"""Tests for Enterprise AI Creative Studio features:
- Autopilot engine & quality gating
- Brand DNA manager
- Campaign packager
- Enterprise API endpoints
"""
import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import app as app_module
from app import app
from core import config
from core.brand_manager import extract_brand_dna, brand_dna_to_prompt_block, infer_tone
from core.campaign_packager import build_bundle, render_html_summary, validate_platform_specs
from core.autopilot import run_autopilot, _score_quality, AUTOPILOT_QUALITY_THRESHOLD, PLATFORM_SPECS
from core.evaluator import evaluate_output, score_copy_quality, score_video_quality


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def cleanup():
    with app_module.PROJECTS_LOCK:
        app_module.PROJECTS.clear()
    yield
    with app_module.PROJECTS_LOCK:
        app_module.PROJECTS.clear()


# ==========================================
# Brand Manager Tests
# ==========================================

def test_infer_tone():
    text = "Luxury watch with high-end craftsmanship, elegance, and exclusivity."
    tone = infer_tone(text)
    assert isinstance(tone, list)
    assert len(tone) > 0
    assert any("luxury" in t.lower() or "premium" in t.lower() or "sophisticated" in t.lower() for t in tone)


def test_brand_dna_extraction():
    scraped = {
        "title": "Aura Luxury Timepieces",
        "text": "Discover our collection of precision engineered luxury automatic watches.",
        "assets": [
            {
                "type": "image",
                "path": "test.jpg",
                "signals": {
                    "aspect_ratio": 1.0,
                    "mean_brightness": 45.0,  # Dark/dramatic
                    "mean_rgb": [20, 25, 40]
                }
            }
        ]
    }
    dna = extract_brand_dna(scraped, "Promote the new chronograph collection.")
    assert "brand_signals" in dna
    assert "visual_dna" in dna
    assert "tone" in dna
    assert dna["visual_dna"]["lighting"] == "dark / dramatic"
    assert dna["visual_dna"]["composition"] == "square / balanced"


def test_brand_dna_to_prompt_block():
    dna = {
        "tone": ["luxury", "aspirational", "confident"],
        "visual_dna": {
            "lighting": "dark / dramatic",
            "composition": "cinematic widescreen",
            "average_rgb": [15, 20, 35]
        },
        "brand_signals": {
            "title": "Aura Chrono",
            "keywords": ["luxury", "watch", "precision"]
        },
        "guidelines": {
            "do_rules": ["Highlight sapphire glass", "Show macro textures"],
            "dont_rules": ["No cluttered backgrounds", "No low-res graphics"]
        }
    }
    block = brand_dna_to_prompt_block(dna)
    assert "BRAND DNA" in block
    assert "dark / dramatic" in block
    assert "luxury" in block.lower()


# ==========================================
# Evaluator & Scoring Tests
# ==========================================

def test_score_copy_quality():
    good_copy = """
    # Introducing the Velocity Runner
    ## Experience Peak Athletic Performance
    
    Engineered with carbon-fiber spring plates for maximum energy return on every stride.
    Built for marathons, perfected for your daily training.
    
    CTA: Shop the Velocity Runner today and unlock free express shipping.
    
    #Running #Marathon #VelocityRunner #Athletics
    """
    score = score_copy_quality(good_copy)
    assert score["total"] >= 70
    assert score["has_cta"] is True


def test_score_copy_quality_short():
    short_copy = "Buy shoes."
    score = score_copy_quality(short_copy)
    assert score["total"] < 70
    assert "too short" in score.get("reasoning", "").lower() or score["word_count"] < 10


# ==========================================
# Campaign Packager Tests
# ==========================================

def test_build_bundle():
    project = {
        "id": "proj_test_123",
        "brief": "Launch of premium organic coffee brand",
    }
    outputs = [
        {
            "creative_type": "copy",
            "filename": "test_copy.txt",
            "url": "/outputs/test_copy.txt",
            "model_used": "openrouter • meta-llama",
            "platform": "instagram_feed"
        },
        {
            "creative_type": "image",
            "filename": "test_image.png",
            "url": "/outputs/test_image.png",
            "model_used": "openrouter • flux",
            "platform": "instagram_feed"
        }
    ]
    brand_dna = {"tone": ["organic", "warm"], "visual_dna": {"lighting": "natural / warm"}}
    quality_scores = {"test_copy.txt": 88, "test_image.png": 92}

    bundle = build_bundle(outputs, project, brand_dna, quality_scores)
    assert bundle["campaign_name"] == project["brief"][:60]
    assert bundle["summary"]["total_assets"] == 2
    assert bundle["summary"]["copy_count"] == 1
    assert bundle["summary"]["image_count"] == 1
    assert bundle["summary"]["avg_quality_score"] == 90.0
    assert len(bundle["html_summary"]) > 0


def test_render_html_summary():
    bundle = {
        "campaign_name": "Ultra Glow Skincare Launch",
        "created_at": 1727330000.0,
        "summary": {
            "total_assets": 3,
            "copy_count": 1,
            "image_count": 1,
            "video_count": 1,
            "avg_quality_score": 88.5,
            "platforms_covered": ["instagram_feed", "tiktok"],
            "models_used": ["openrouter • gpt-4o", "agnes • video-v2"]
        },
        "brand_dna": {
            "tone": ["fresh", "radiant", "clean"],
            "visual_dna": {"lighting": "bright / airy"}
        }
    }
    html = render_html_summary(bundle)
    assert "Ultra Glow Skincare Launch" in html
    assert "88.5" in html or "88" in html
    assert "instagram_feed" in html


# ==========================================
# Autopilot End-to-End Orchestration Tests
# ==========================================

def test_autopilot_run_local_fallback():
    """Autopilot should successfully run to completion using local fallback when no keys are present."""
    project = {
        "id": "proj_autopilot_test",
        "brief": "Minimalist urban bicycle commuter campaign",
        "creative_types": ["copy", "image"],
        "scraped": {
            "url": "",
            "title": "Urban Commuter Bicycles",
            "text": "Lightweight, durable, elegant city cycles built for daily commutes.",
            "assets": []
        }
    }
    
    events_received = []
    def mock_emit(event, data):
        events_received.append((event, data))
        
    bundle = run_autopilot(
        project,
        emit_fn=mock_emit,
        creative_types=["copy", "image"],
        platforms=["instagram_feed", "facebook_feed"]
    )
    
    assert bundle is not None
    assert "bundle_id" in bundle
    assert bundle["summary"]["total_assets"] >= 2
    assert bundle["summary"]["copy_count"] >= 1
    assert bundle["summary"]["image_count"] >= 1
    assert bundle["summary"]["avg_quality_score"] > 0
    
    # Verify events were streamed
    event_names = [e[0] for e in events_received]
    assert "autopilot_progress" in event_names
    assert "autopilot_complete" in event_names


# ==========================================
# Enterprise API Route Tests
# ==========================================

def test_api_brand_dna(client):
    create_resp = client.post("/api/projects", json={
        "brief": "Eco-friendly reusable bottles",
        "creative_types": ["copy"]
    })
    pid = json.loads(create_resp.data)["id"]
    
    resp = client.get(f"/api/brand/{pid}/dna")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "brand_dna" in data
    assert "visual_dna" in data["brand_dna"]


def test_api_autopilot_launch_and_status(client):
    create_resp = client.post("/api/projects", json={
        "brief": "Smart wearable fitness tracker launch",
        "creative_types": ["copy", "image"]
    })
    pid = json.loads(create_resp.data)["id"]
    
    launch_resp = client.post("/api/autopilot/launch", json={
        "project_id": pid,
        "creative_types": ["copy", "image"],
        "platforms": ["instagram_feed"]
    })
    assert launch_resp.status_code in (200, 202)
    data = json.loads(launch_resp.data)
    assert data["status"] == "launched"
    assert "job_id" in data
    
    # Check status endpoint
    status_resp = client.get(f"/api/autopilot/status/{pid}")
    assert status_resp.status_code == 200
    sdata = json.loads(status_resp.data)
    assert "status" in sdata
