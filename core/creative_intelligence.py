"""Reference-to-Creative-DNA extraction.

This is deliberately deterministic first: it produces a stable structured
creative profile from scraped metadata/assets, then optionally enriches it with
a configured vision model. The profile is stored with the project so later
prompt generations can remain consistent.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from . import config
from .ai import chat


def _keywords(text: str, limit: int = 20):
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", (text or "").lower())
    stop = set("the and for with this that from into your our their have has are was were is of to in on at by a an or as be it its".split())
    counts = Counter(w for w in words if w not in stop)
    return [w for w, _ in counts.most_common(limit)]


def build_dna(scraped: dict[str, Any] | None, brief: str = "") -> dict[str, Any]:
    scraped = scraped or {}
    assets = [a for a in scraped.get("assets", []) if not a.get("error")]
    images = [a for a in assets if a.get("type") == "image"]
    videos = [a for a in assets if a.get("type") == "video"]
    signals = [a.get("signals") or {} for a in assets]

    ratios = [float(s["aspect_ratio"]) for s in signals if s.get("aspect_ratio")]
    brightness = [float(s["mean_brightness"]) for s in signals if s.get("mean_brightness") is not None]
    mean_rgb = [s.get("mean_rgb") for s in signals if isinstance(s.get("mean_rgb"), list)]

    avg_rgb = [round(sum(v[i] for v in mean_rgb) / len(mean_rgb), 1) for i in range(3)] if mean_rgb else None
    avg_brightness = round(sum(brightness) / len(brightness), 1) if brightness else None
    avg_ratio = round(sum(ratios) / len(ratios), 3) if ratios else None

    if avg_brightness is None:
        light = "unknown"
    elif avg_brightness < 85:
        light = "dark / dramatic"
    elif avg_brightness > 175:
        light = "bright / airy"
    else:
        light = "balanced"

    if avg_ratio is None:
        composition = "unknown"
    elif avg_ratio > 1.55:
        composition = "landscape / cinematic"
    elif avg_ratio < 0.75:
        composition = "portrait / social-first"
    else:
        composition = "square / balanced"

    return {
        "brand_signals": {
            "title": scraped.get("title") or "",
            "keywords": _keywords(f"{scraped.get('text','')} {brief}"),
        },
        "visual_dna": {
            "lighting": light,
            "composition": composition,
            "average_aspect_ratio": avg_ratio,
            "average_brightness": avg_brightness,
            "average_rgb": avg_rgb,
            "reference_image_count": len(images),
            "reference_video_count": len(videos),
        },
        "media_dna": {
            "video_durations": [s.get("duration_seconds") for s in signals if s.get("duration_seconds")],
            "video_fps": [s.get("fps") for s in signals if s.get("fps")],
        },
        "content_dna": {
            "headline": scraped.get("title") or "",
            "keywords": _keywords(scraped.get("text", ""), 12),
        },
        "source_asset_count": len(assets),
    }


def enrich_with_vision(dna: dict[str, Any], scraped: dict[str, Any] | None) -> str:
    """Return optional vision enrichment; failures never break the pipeline."""
    if not config.OPENROUTER_API_KEY:
        return ""
    paths = []
    for asset in (scraped or {}).get("assets", []):
        if asset.get("type") == "image" and asset.get("path"):
            paths.append(config.ROOT / asset["path"])
        paths.extend(config.ROOT / p for p in (asset.get("frame_paths") or [])[:2])
    paths = [p for p in paths if isinstance(p, Path) and p.exists()][:4]
    if not paths:
        return ""
    try:
        return chat(
            "Act as a senior brand/visual strategist. Analyze these reference assets and return concise JSON-like guidance covering palette, lighting, composition, camera/lens cues, materials, typography treatment, motion cues, audience/tone, and creative do/don't rules. Do not invent brand facts.",
            model=config.OPENROUTER_VISION_MODEL,
            provider="openrouter",
            image_paths=paths,
            timeout=60,
        )
    except Exception:
        return ""
