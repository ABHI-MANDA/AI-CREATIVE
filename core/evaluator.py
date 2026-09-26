"""Deterministic post-generation quality evaluation.

This is intentionally conservative. It reports measurable technical signals and
basic completeness checks rather than pretending a numeric score is equivalent
to human creative judgment.

Enhancements (v2):
  - ``ai_score_output`` — vision-model scoring via OpenRouter for images;
    heuristic scoring for copy and video.
  - ``score_copy_quality`` — standalone heuristic copy scorer.
  - ``score_video_quality`` — cv2-based video technical scorer.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default score returned when AI scoring fails entirely
_DEFAULT_SCORE = 50

# Vision model prompt for image scoring
_IMAGE_SCORE_PROMPT = """\
Score this AI-generated {type} ad creative on a scale of 0-100 for:
1. Visual quality & realism (0-25)
2. Brand-appropriate composition (0-25)
3. Message clarity & CTA strength (0-25)
4. Technical quality (resolution, no artifacts) (0-25)

Return ONLY a JSON object with no markdown fencing:
{{"total": 85, "visual": 20, "composition": 22, "message": 21, "technical": 22, "reasoning": "..."}}"""

# Heuristic copy-quality thresholds
_COPY_MIN_WORDS_GOOD = 100
_COPY_MIN_WORDS_PASS = 40

# CTA keywords matched case-insensitively
_CTA_PATTERN = re.compile(
    r"\b(learn more|discover|book|contact|shop|explore|get started|apply|"
    r"sign up|subscribe|download|buy now|try free|start now|claim|order)\b",
    re.IGNORECASE,
)

# Headline heuristics (markdown header #, all-caps word 3+ chars, or line ending with "!")
_HEADLINE_PATTERN = re.compile(
    r"(?m)^\s*#+\s+.+$|^([A-Z]{3,}[\s!?]*){2,}$|^.{5,}!$"
)

# Hashtag pattern
_HASHTAG_PATTERN = re.compile(r"#\w{2,}")

# Platform section marker (e.g., "## Instagram", "**Facebook:**")
_SECTION_PATTERN = re.compile(
    r"(?m)^\s*#+\s+\w|^\s*[\*_]{1,2}\w.+[\*_]{1,2}\s*:?\s*$"
)

# Video quality thresholds
_VIDEO_MIN_FPS   = 23.0
_VIDEO_MIN_FRAMES = 24
_VIDEO_MIN_WIDTH  = 640
_VIDEO_MIN_HEIGHT = 360


# ---------------------------------------------------------------------------
# Original deterministic evaluator (preserved exactly)
# ---------------------------------------------------------------------------


def evaluate_output(path: Path, creative_type: str, prompt: str = "") -> dict:
    """Evaluate a generated output file using deterministic technical checks.

    Checks file existence, file size, and type-specific signals:

    - **image**: PIL-based dimension and format check.
    - **video**: cv2-based frame/fps/resolution check.
    - **copy**: character/word count and basic CTA presence.

    Args:
        path:          Absolute path to the output file.
        creative_type: One of ``"image"``, ``"video"``, or ``"copy"``.
        prompt:        The original generation prompt (currently unused but
                       reserved for future relevance checks).

    Returns:
        A dict with keys ``status`` (``"pass"`` | ``"warn"`` | ``"fail"``),
        ``checks`` (dict of measured signals), ``warnings`` (list of str),
        and ``evaluated_at`` (Unix timestamp float).
    """
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


# ---------------------------------------------------------------------------
# AI-powered scorer
# ---------------------------------------------------------------------------


def ai_score_output(
    path: Path,
    creative_type: str,
    prompt: str,
    model: str | None = None,
    timeout: int = 30,
) -> dict:
    """Score a generated output using AI vision or heuristic analysis.

    Dispatches to the appropriate scorer based on ``creative_type``:

    - **image** — sends the image to an OpenRouter vision model and parses the
      returned JSON score.  Falls back to heuristic if the API call fails.
    - **copy** — calls :func:`score_copy_quality` (fully offline).
    - **video** — calls :func:`score_video_quality` (cv2-based, fully offline).

    This function **never raises**.  If scoring fails for any reason the
    returned dict will contain ``{"total": 50, "reasoning": "<error detail>",
    "scored_by": "fallback"}``.

    Args:
        path:          Absolute path to the generated output file.
        creative_type: One of ``"image"``, ``"video"``, or ``"copy"``.
        prompt:        Original generation prompt (used as context for the
                       vision model).
        model:         Override the OpenRouter vision model.  When ``None`` the
                       value of ``config.OPENROUTER_VISION_MODEL`` is used.
        timeout:       HTTP timeout in seconds for the vision API call.

    Returns:
        A dict with at least a ``"total"`` key (int 0-100) plus type-specific
        sub-scores and a ``"reasoning"`` string.
    """
    creative_type = str(creative_type).lower()
    logger.info("ai_score_output: scoring '%s' as type='%s'.", path.name, creative_type)

    try:
        if creative_type == "image":
            return _score_image_ai(path, prompt, model=model, timeout=timeout)
        elif creative_type == "copy":
            text = path.read_text(encoding="utf-8", errors="replace")
            return score_copy_quality(text)
        elif creative_type == "video":
            return score_video_quality(path)
        else:
            logger.warning("ai_score_output: unknown creative_type '%s'; returning default.", creative_type)
            return {
                "total":     _DEFAULT_SCORE,
                "reasoning": f"Unknown creative type '{creative_type}'.",
                "scored_by": "fallback",
            }
    except Exception as exc:  # pragma: no cover — safety net
        logger.error("ai_score_output: unexpected error for '%s': %s", path.name, exc)
        return {
            "total":     _DEFAULT_SCORE,
            "reasoning": f"Scoring failed with unexpected error: {exc}",
            "scored_by": "fallback",
        }


def _score_image_ai(
    path: Path,
    prompt: str,
    model: str | None,
    timeout: int,
) -> dict:
    """Internal: score an image via OpenRouter vision model.

    Falls back to a conservative default dict on any failure so the caller
    always gets a valid result.

    Args:
        path:    Path to the image file.
        prompt:  Original generation prompt for context.
        model:   Vision model ID override.
        timeout: HTTP request timeout.

    Returns:
        Score dict with keys: ``total``, ``visual``, ``composition``,
        ``message``, ``technical``, ``reasoning``, ``scored_by``.
    """
    _fallback: dict = {
        "total":       _DEFAULT_SCORE,
        "visual":      12,
        "composition": 12,
        "message":     13,
        "technical":   13,
        "reasoning":   "Vision scoring unavailable; using default.",
        "scored_by":   "fallback",
    }

    try:
        from .ai import chat, file_data_uri  # type: ignore[import]
        from . import config                  # type: ignore[import]
    except ImportError as exc:
        logger.warning("_score_image_ai: cannot import AI helpers — %s", exc)
        return _fallback

    if not path.exists():
        _fallback["reasoning"] = f"File not found: {path}"
        return _fallback

    vision_model = model or config.OPENROUTER_VISION_MODEL
    instruction  = _IMAGE_SCORE_PROMPT.format(type="image")

    # If a generation prompt was supplied, include it for context
    if prompt:
        instruction = f"Context — original generation prompt: {prompt[:500]}\n\n{instruction}"

    try:
        raw_response = chat(
            instruction=instruction,
            model=vision_model,
            provider="openrouter",
            image_paths=[path],
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("_score_image_ai: vision API call failed — %s", exc)
        _fallback["reasoning"] = f"Vision API error: {exc}"
        return _fallback

    # Parse JSON from response (model may wrap it in markdown fences)
    json_str = _extract_json(raw_response)
    if not json_str:
        logger.warning("_score_image_ai: could not extract JSON from response: %.200s", raw_response)
        _fallback["reasoning"] = "Vision API returned non-JSON response."
        return _fallback

    try:
        score_data: dict = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.warning("_score_image_ai: JSON parse error — %s | raw: %.200s", exc, json_str)
        _fallback["reasoning"] = f"JSON parse error: {exc}"
        return _fallback

    # Validate and clamp the total score
    total = int(score_data.get("total", _DEFAULT_SCORE))
    total = max(0, min(100, total))

    result = {
        "total":       total,
        "visual":      int(score_data.get("visual",      0)),
        "composition": int(score_data.get("composition", 0)),
        "message":     int(score_data.get("message",     0)),
        "technical":   int(score_data.get("technical",   0)),
        "reasoning":   str(score_data.get("reasoning",   "")),
        "scored_by":   f"vision:{vision_model}",
    }
    logger.info(
        "_score_image_ai: '%s' scored %d/100 by %s.",
        path.name, total, vision_model,
    )
    return result


def _extract_json(text: str) -> str | None:
    """Extract the first ``{...}`` block from a string, stripping fences."""
    # Strip common markdown code fences
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Copy quality scorer
# ---------------------------------------------------------------------------


def score_copy_quality(text: str) -> dict:
    """Score copy text quality using offline heuristics.

    Criteria evaluated:

    - **word_count** — more words → higher score (capped benefit above 100).
    - **has_headline** — detects all-caps or exclamation-marked headline lines.
    - **has_cta** — presence of call-to-action keywords.
    - **has_hashtags** — at least one ``#hashtag``.
    - **has_platform_sections** — Markdown headings or bold labels suggesting
      per-platform copy blocks.

    The scoring is additive with partial credit:

    +-----------------------+--------+
    | Signal                | Points |
    +=======================+========+
    | Word count ≥ 100      | 35     |
    | Word count ≥ 40       | 20     |
    | has_headline          | 20     |
    | has_cta               | 25     |
    | has_hashtags          | 10     |
    | has_platform_sections | 10     |
    +-----------------------+--------+
    | **Maximum**           | **100**|
    +-----------------------+--------+

    Args:
        text: Raw copy string.

    Returns:
        A dict with keys: ``total`` (int 0-100), ``word_count`` (int),
        ``has_headline`` (bool), ``has_cta`` (bool), ``has_hashtags`` (bool),
        ``has_platform_sections`` (bool), ``reasoning`` (str),
        ``scored_by`` (str).
    """
    words             = re.findall(r"\b\w+\b", text)
    word_count        = len(words)
    has_headline      = bool(_HEADLINE_PATTERN.search(text))
    has_cta           = bool(_CTA_PATTERN.search(text))
    has_hashtags      = bool(_HASHTAG_PATTERN.search(text))
    has_platform_secs = bool(_SECTION_PATTERN.search(text))

    score = 0
    reasons: list[str] = []

    # Word count
    if word_count >= _COPY_MIN_WORDS_GOOD:
        score += 35
        reasons.append(f"Good length ({word_count} words).")
    elif word_count >= _COPY_MIN_WORDS_PASS:
        score += 20
        reasons.append(f"Acceptable length ({word_count} words).")
    else:
        reasons.append(f"Short copy ({word_count} words).")

    if has_headline:
        score += 20
        reasons.append("Headline detected.")
    else:
        reasons.append("No clear headline.")

    if has_cta:
        score += 25
        reasons.append("CTA keyword present.")
    else:
        reasons.append("No CTA keyword found.")

    if has_hashtags:
        score += 10
        reasons.append("Hashtags present.")

    if has_platform_secs:
        score += 10
        reasons.append("Platform sections detected.")

    total = min(100, score)
    logger.info("score_copy_quality: total=%d, words=%d, cta=%s.", total, word_count, has_cta)

    return {
        "total":                total,
        "word_count":           word_count,
        "has_headline":         has_headline,
        "has_cta":              has_cta,
        "has_hashtags":         has_hashtags,
        "has_platform_sections": has_platform_secs,
        "reasoning":            " ".join(reasons),
        "scored_by":            "heuristic:copy",
    }


# ---------------------------------------------------------------------------
# Video quality scorer
# ---------------------------------------------------------------------------


def score_video_quality(path: Path) -> dict:
    """Score a video file on technical quality using cv2.

    Criteria:

    +--------------------------+--------+
    | Signal                   | Points |
    +==========================+========+
    | FPS ≥ 23                 | 25     |
    | Frames ≥ 24              | 25     |
    | Width  ≥ 640             | 25     |
    | Height ≥ 360             | 25     |
    +--------------------------+--------+
    | **Maximum**              | **100**|
    +--------------------------+--------+

    If cv2 is not installed or the file cannot be opened, returns the
    ``_DEFAULT_SCORE`` with a descriptive reasoning string.

    Args:
        path: Absolute path to the video file.

    Returns:
        A dict with keys: ``total`` (int 0-100), ``fps`` (float),
        ``frames`` (int), ``duration`` (float seconds), ``width`` (int),
        ``height`` (int), ``reasoning`` (str), ``scored_by`` (str).
    """
    _fallback: dict[str, Any] = {
        "total":     _DEFAULT_SCORE,
        "fps":       0.0,
        "frames":    0,
        "duration":  0.0,
        "width":     0,
        "height":    0,
        "reasoning": "cv2 unavailable or file unreadable; using default score.",
        "scored_by": "fallback",
    }

    if not path.exists():
        _fallback["reasoning"] = f"File not found: {path}"
        logger.warning("score_video_quality: file not found — %s", path)
        return _fallback

    try:
        import cv2
    except ImportError:
        logger.warning("score_video_quality: cv2 not installed.")
        _fallback["reasoning"] = "cv2 not installed; technical scoring skipped."
        return _fallback

    try:
        cap      = cv2.VideoCapture(str(path))
        fps      = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frames   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
    except Exception as exc:
        logger.error("score_video_quality: cv2 error for '%s': %s", path.name, exc)
        _fallback["reasoning"] = f"cv2 read error: {exc}"
        return _fallback

    duration = round(frames / fps, 2) if fps else 0.0
    score    = 0
    reasons: list[str] = []

    if fps >= _VIDEO_MIN_FPS:
        score += 25
        reasons.append(f"Good FPS ({fps:.1f}).")
    else:
        reasons.append(f"Low FPS ({fps:.1f}; expected ≥ {_VIDEO_MIN_FPS}).")

    if frames >= _VIDEO_MIN_FRAMES:
        score += 25
        reasons.append(f"Adequate frame count ({frames}).")
    else:
        reasons.append(f"Few frames ({frames}; expected ≥ {_VIDEO_MIN_FRAMES}).")

    if width >= _VIDEO_MIN_WIDTH:
        score += 25
        reasons.append(f"Width OK ({width}px).")
    else:
        reasons.append(f"Low width ({width}px; expected ≥ {_VIDEO_MIN_WIDTH}).")

    if height >= _VIDEO_MIN_HEIGHT:
        score += 25
        reasons.append(f"Height OK ({height}px).")
    else:
        reasons.append(f"Low height ({height}px; expected ≥ {_VIDEO_MIN_HEIGHT}).")

    total = min(100, score)
    logger.info(
        "score_video_quality: '%s' scored %d/100 (fps=%.1f, frames=%d, %dx%d, duration=%.1fs).",
        path.name, total, fps, frames, width, height, duration,
    )

    return {
        "total":     total,
        "fps":       round(fps, 2),
        "frames":    frames,
        "duration":  duration,
        "width":     width,
        "height":    height,
        "reasoning": " ".join(reasons),
        "scored_by": "cv2:technical",
    }
