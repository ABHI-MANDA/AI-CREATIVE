"""Deterministic post-generation quality evaluation.

This is intentionally conservative. It reports measurable technical signals and
basic completeness checks rather than pretending a numeric score is equivalent
to human creative judgment.
"""
from __future__ import annotations

from pathlib import Path
import re

from PIL import Image


def evaluate_output(path: Path, creative_type: str, prompt: str = "") -> dict:
    result = {
        "status": "pass",
        "checks": {},
        "warnings": [],
        "evaluated_at": __import__("time").time(),
    }
    if not path.exists() or path.stat().st_size == 0:
        result["status"] = "fail"
        result["warnings"].append("Output file is missing or empty.")
        return result

    result["checks"]["file_size_bytes"] = path.stat().st_size

    if creative_type == "image":
        try:
            with Image.open(path) as im:
                result["checks"].update({
                    "width": im.width,
                    "height": im.height,
                    "format": im.format,
                    "has_alpha": "A" in im.getbands(),
                })
                if min(im.width, im.height) < 512:
                    result["status"] = "warn"
                    result["warnings"].append("Image is below the recommended 512px minimum on one axis.")
        except Exception as exc:
            result["status"] = "fail"
            result["warnings"].append(f"Image validation failed: {exc}")

    elif creative_type == "video":
        try:
            import cv2
            cap = cv2.VideoCapture(str(path))
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            duration = frames / fps if fps else 0
            cap.release()
            result["checks"].update({"width": width, "height": height, "fps": round(fps, 2), "frames": frames, "duration_seconds": round(duration, 2)})
            if frames <= 0 or width <= 0 or height <= 0:
                result["status"] = "fail"
                result["warnings"].append("Video contains no readable frames.")
        except Exception as exc:
            result["status"] = "fail"
            result["warnings"].append(f"Video validation failed: {exc}")

    elif creative_type == "copy":
        text = path.read_text(encoding="utf-8", errors="replace")
        result["checks"]["characters"] = len(text)
        result["checks"]["words"] = len(re.findall(r"\b\w+\b", text))
        result["checks"]["has_cta"] = bool(re.search(r"\b(learn more|discover|book|contact|shop|explore|get started|apply)\b", text, re.I))
        if len(text.strip()) < 100:
            result["status"] = "warn"
            result["warnings"].append("Copy output is unusually short for a campaign package.")

    return result
