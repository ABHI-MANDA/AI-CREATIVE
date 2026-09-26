"""Learning and creative memory module.

Provides review recording, contextual lesson retrieval, quality scoring,
and performance analytics for the AI Creative Studio pipeline.

All data is persisted via the store layer (PostgreSQL when configured,
falling back to atomic JSON files).
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

from . import config
from .store import load_persistent_collection, save_persistent_collection

# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Collection names (used as PostgreSQL collection keys and JSON file aliases)
# ---------------------------------------------------------------------------
_REVIEWS_COLLECTION = "reviews"
_MEMORY_COLLECTION = "creative_memory"
_QUALITY_COLLECTION = "quality_scores"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_REVIEWS: Dict[str, Any] = {"reviews": []}
_DEFAULT_MEMORY: Dict[str, Any] = {
    "lessons": [],
    "stats": {"reviews": 0, "approved": 0, "rejected": 0},
}
_DEFAULT_QUALITY: Dict[str, Any] = {"scores": []}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def clean(s: Any, n: int = 3000) -> str:
    """Normalise and truncate a string value for safe storage.

    Collapses internal whitespace, strips leading/trailing spaces,
    and truncates to at most ``n`` characters.

    Args:
        s: Input value (will be coerced to str).
        n: Maximum character length. Defaults to 3000.

    Returns:
        Cleaned, truncated string.
    """
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_memory() -> Dict[str, Any]:
    """Load the creative memory collection with a safe default."""
    return load_persistent_collection(_MEMORY_COLLECTION, config.MEMORY_STORE, _DEFAULT_MEMORY)


def _save_memory(data: Dict[str, Any]) -> None:
    """Persist the creative memory collection."""
    save_persistent_collection(_MEMORY_COLLECTION, config.MEMORY_STORE, data)


def _load_quality() -> Dict[str, Any]:
    """Load the quality scores collection with a safe default."""
    quality_path = config.DATA_DIR / "quality_scores.json"
    return load_persistent_collection(_QUALITY_COLLECTION, quality_path, _DEFAULT_QUALITY)


def _save_quality(data: Dict[str, Any]) -> None:
    """Persist the quality scores collection."""
    quality_path = config.DATA_DIR / "quality_scores.json"
    save_persistent_collection(_QUALITY_COLLECTION, quality_path, data)


def _memory(review: Dict[str, Any]) -> None:
    """Extract a lesson from a review record and store it in creative memory.

    Updates approval/rejection counters and, when notes are present,
    appends a structured lesson to the lessons list.

    Args:
        review: A review record as produced by ``record_review``.
    """
    m = _load_memory()
    s = m.setdefault("stats", {"reviews": 0, "approved": 0, "rejected": 0})
    s["reviews"] += 1
    s["approved" if review["approved"] else "rejected"] += 1

    if review.get("notes"):
        lesson = {
            "timestamp": review["timestamp"],
            "creative_type": review["creative_type"],
            "approved": review["approved"],
            "rating": review.get("rating"),
            "lesson": review["notes"],
            "prompt_snippet": clean(review.get("prompt", ""), 500),
            "output": review.get("output", ""),
        }
        m.setdefault("lessons", []).append(lesson)
        m["lessons"] = m["lessons"][-config.MAX_REVIEW_MEMORY :]

    _save_memory(m)
    logger.debug(
        "_memory updated: approved=%s type=%s", review["approved"], review["creative_type"]
    )


# ---------------------------------------------------------------------------
# ORIGINAL: record_review
# ---------------------------------------------------------------------------

def record_review(
    project_id: str,
    ctype: str = "",
    prompt: str = "",
    output_filename: str = "",
    approved: bool = True,
    notes: str = "",
    rating: Optional[float] = None,
    creative_type: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    ctype = ctype or creative_type or "copy"
    """Record a human review decision for a creative output.

    Appends the review to the reviews store and triggers creative memory
    extraction so lessons are immediately available for future prompts.

    Args:
        project_id: Unique identifier for the parent project.
        ctype: Creative type ('copy', 'image', 'video', or custom).
        prompt: The prompt that produced the reviewed output.
        output_filename: Filename of the reviewed creative asset.
        approved: Whether the reviewer approved this output.
        notes: Reviewer notes, corrections, or praise (optional).
        rating: Numeric quality rating, e.g. 1.0–5.0 (optional).

    Returns:
        The persisted review record dict.
    """
    d = load_persistent_collection(
        _REVIEWS_COLLECTION, config.REVIEW_STORE, _DEFAULT_REVIEWS
    )
    r: Dict[str, Any] = {
        "id": f"rev_{int(time.time() * 1000)}",
        "timestamp": time.time(),
        "project_id": project_id,
        "creative_type": ctype,
        "prompt": clean(prompt, 6000),
        "output": output_filename,
        "approved": bool(approved),
        "rating": rating,
        "notes": clean(notes),
    }
    d.setdefault("reviews", []).append(r)
    d["reviews"] = d["reviews"][-config.MAX_REVIEW_MEMORY * 3 :]
    save_persistent_collection(_REVIEWS_COLLECTION, config.REVIEW_STORE, d)
    _memory(r)

    logger.info(
        "Recorded review id=%s project=%s type=%s approved=%s",
        r["id"],
        project_id,
        ctype,
        approved,
    )
    return r


# ---------------------------------------------------------------------------
# ORIGINAL: get_context
# ---------------------------------------------------------------------------

def get_context(ctype: str, limit: int = 8) -> str:
    """Retrieve recent learned lessons for a creative type.

    Lessons are filtered to match the requested ``ctype`` or the special
    'all' wildcard, and the most recent ``limit`` entries are returned.

    Args:
        ctype: Creative type to filter lessons by ('copy', 'image', 'video', …).
        limit: Maximum number of lessons to include. Defaults to 8.

    Returns:
        Formatted 'LEARNED REVIEW LESSONS' block string, or empty string
        if no lessons exist yet.
    """
    m = _load_memory()
    rows = [
        x
        for x in m.get("lessons", [])
        if x.get("creative_type") in (ctype, "all")
    ][-limit:]

    if not rows:
        return ""

    lines = ["LEARNED REVIEW LESSONS:"]
    for x in rows:
        tag = "APPROVED" if x.get("approved") else "CORRECTION"
        rating_str = f" [rating={x['rating']}]" if x.get("rating") is not None else ""
        lines.append(f"- [{tag}]{rating_str} {x.get('lesson', '')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ORIGINAL: summary
# ---------------------------------------------------------------------------

def summary() -> Dict[str, Any]:
    """Return the full creative memory document including stats and lessons.

    Returns:
        Dict with keys 'lessons' (list) and 'stats' (dict with
        reviews/approved/rejected counts).
    """
    return _load_memory()


# ---------------------------------------------------------------------------
# ORIGINAL: clean  (re-exported at module level for backward compatibility)
# — already defined above —
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# NEW: get_enriched_context
# ---------------------------------------------------------------------------

def get_enriched_context(ctype: str, limit: int = 12) -> str:
    """Return an enriched learning context block for prompt injection.

    Extends ``get_context`` by segmenting lessons into approval patterns,
    rejection/correction patterns, and quality score history, ordered by
    recency. Intended for use in multi-variant and platform prompt builders.

    Args:
        ctype: Creative type to filter by ('copy', 'image', 'video', …).
        limit: Maximum total lessons to include across all segments.

    Returns:
        Richly formatted string with separate APPROVED PATTERNS,
        CORRECTION PATTERNS, and QUALITY HISTORY sections.
        Returns empty string if no lessons exist.
    """
    m = _load_memory()
    all_lessons = [
        x
        for x in m.get("lessons", [])
        if x.get("creative_type") in (ctype, "all")
    ][-limit:]

    if not all_lessons:
        logger.debug("get_enriched_context: no lessons found for ctype='%s'.", ctype)
        return ""

    approved = [x for x in all_lessons if x.get("approved")]
    rejected = [x for x in all_lessons if not x.get("approved")]

    sections: List[str] = [f"ENRICHED CREATIVE MEMORY — type: {ctype.upper()}"]

    if approved:
        sections.append("\n✅ APPROVAL PATTERNS (what worked):")
        for x in approved[-5:]:
            rating_str = f" (rated {x['rating']}/5)" if x.get("rating") is not None else ""
            sections.append(f"  + {x.get('lesson', '')}{rating_str}")

    if rejected:
        sections.append("\n❌ CORRECTION PATTERNS (what to avoid):")
        for x in rejected[-5:]:
            sections.append(f"  - {x.get('lesson', '')}")

    # Quality score history from quality collection
    quality_data = _load_quality()
    type_scores = [
        s
        for s in quality_data.get("scores", [])
        if s.get("ctype") == ctype
    ][-8:]

    if type_scores:
        avg_score = sum(s.get("score", 0) for s in type_scores) / len(type_scores)
        sections.append(f"\n📊 QUALITY SCORE HISTORY (last {len(type_scores)} outputs):")
        sections.append(f"  Average score: {avg_score:.2f}")
        for s in type_scores[-3:]:
            sections.append(
                f"  - [{s.get('model_used', 'unknown')}] "
                f"score={s.get('score', 'n/a')} file={s.get('filename', 'n/a')}"
            )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# NEW: record_quality_score
# ---------------------------------------------------------------------------

def record_quality_score(
    project_id: str,
    ctype: str,
    filename: str,
    score: float,
    model_used: str,
) -> None:
    """Record a quality score for a generated creative output.

    Quality scores are stored separately from human reviews and are used
    by ``get_performance_stats`` and ``top_performing_prompts``.

    Args:
        project_id: Unique identifier for the parent project.
        ctype: Creative type ('copy', 'image', 'video', …).
        filename: Filename of the generated creative asset.
        score: Quality score (e.g. 0.0–5.0 or 0–100; caller convention).
        model_used: Name/ID of the model that produced the output.
    """
    data = _load_quality()
    entry: Dict[str, Any] = {
        "id": f"qs_{int(time.time() * 1000)}",
        "timestamp": time.time(),
        "project_id": project_id,
        "ctype": ctype,
        "filename": filename,
        "score": score,
        "model_used": model_used,
    }
    data.setdefault("scores", []).append(entry)
    # Keep last MAX_REVIEW_MEMORY * 5 scores total
    data["scores"] = data["scores"][-(config.MAX_REVIEW_MEMORY * 5) :]
    _save_quality(data)
    logger.info(
        "Quality score recorded: project=%s type=%s score=%s model=%s",
        project_id,
        ctype,
        score,
        model_used,
    )


# ---------------------------------------------------------------------------
# NEW: get_performance_stats
# ---------------------------------------------------------------------------

def get_performance_stats() -> Dict[str, Any]:
    """Aggregate performance statistics across all creative types.

    Combines data from the creative memory (approval rates) and quality
    score store (model performance) into a single analytics dict.

    Returns:
        Dict with keys:
            - ``avg_quality``: dict mapping ctype → float average score
            - ``best_model``: dict mapping ctype → str model name with highest avg score
            - ``approval_rate``: float (0.0–1.0) overall approval percentage
            - ``total_campaigns``: int count of unique project IDs reviewed
            - ``total_reviews``: int total review count
            - ``approved``: int count of approved reviews
            - ``rejected``: int count of rejected reviews
    """
    m = _load_memory()
    stats = m.get("stats", {"reviews": 0, "approved": 0, "rejected": 0})
    lessons = m.get("lessons", [])

    total = stats.get("reviews", 0)
    approved_count = stats.get("approved", 0)
    approval_rate = (approved_count / total) if total > 0 else 0.0

    # Count unique project IDs from reviews
    reviews_data = load_persistent_collection(
        _REVIEWS_COLLECTION, config.REVIEW_STORE, _DEFAULT_REVIEWS
    )
    all_reviews = reviews_data.get("reviews", [])
    unique_projects = len({r.get("project_id") for r in all_reviews if r.get("project_id")})

    # Quality score aggregation
    quality_data = _load_quality()
    all_scores = quality_data.get("scores", [])

    # Group scores by ctype
    by_type: Dict[str, List[float]] = {}
    by_type_model: Dict[str, Dict[str, List[float]]] = {}
    for s in all_scores:
        ctype = s.get("ctype", "unknown")
        score_val = s.get("score")
        model = s.get("model_used", "unknown")
        if score_val is None:
            continue
        by_type.setdefault(ctype, []).append(float(score_val))
        by_type_model.setdefault(ctype, {}).setdefault(model, []).append(float(score_val))

    avg_quality: Dict[str, float] = {
        ct: round(sum(scores) / len(scores), 3)
        for ct, scores in by_type.items()
        if scores
    }

    best_model: Dict[str, str] = {}
    for ct, model_scores in by_type_model.items():
        best_model[ct] = max(
            model_scores,
            key=lambda m_key: sum(model_scores[m_key]) / len(model_scores[m_key]),
        )

    result: Dict[str, Any] = {
        "avg_quality": avg_quality,
        "best_model": best_model,
        "approval_rate": round(approval_rate, 4),
        "total_campaigns": unique_projects,
        "total_reviews": total,
        "approved": approved_count,
        "rejected": stats.get("rejected", 0),
    }

    logger.info(
        "Performance stats: approval_rate=%.2f%% campaigns=%d",
        approval_rate * 100,
        unique_projects,
    )
    return result


# ---------------------------------------------------------------------------
# NEW: top_performing_prompts
# ---------------------------------------------------------------------------

def top_performing_prompts(ctype: str, n: int = 3) -> List[str]:
    """Return prompts from the highest-rated approved outputs for a creative type.

    Merges approval status from review records with quality scores from the
    quality store to surface the prompts most likely to seed strong variants.

    Args:
        ctype: Creative type to filter by ('copy', 'image', 'video', …).
        n: Maximum number of top prompts to return. Defaults to 3.

    Returns:
        List of prompt strings (up to n), ordered by descending quality score.
        Falls back to approved lessons' prompt_snippets when full prompts are
        unavailable in the quality store.
    """
    # Gather approved review records for this ctype
    reviews_data = load_persistent_collection(
        _REVIEWS_COLLECTION, config.REVIEW_STORE, _DEFAULT_REVIEWS
    )
    approved_reviews = [
        r
        for r in reviews_data.get("reviews", [])
        if r.get("creative_type") == ctype and r.get("approved")
    ]

    # Build a filename → prompt map from approved reviews
    filename_to_prompt: Dict[str, str] = {
        r["output"]: r.get("prompt", "")
        for r in approved_reviews
        if r.get("output") and r.get("prompt")
    }

    # Merge with quality scores: find scored+approved outputs
    quality_data = _load_quality()
    scored = [
        s
        for s in quality_data.get("scores", [])
        if s.get("ctype") == ctype and s.get("filename") in filename_to_prompt
    ]

    if scored:
        # Sort by score descending
        scored_sorted = sorted(scored, key=lambda s: s.get("score", 0), reverse=True)
        top_prompts: List[str] = []
        seen: set = set()
        for s in scored_sorted:
            prompt = filename_to_prompt.get(s["filename"], "")
            if prompt and prompt not in seen:
                top_prompts.append(prompt)
                seen.add(prompt)
            if len(top_prompts) >= n:
                break
        logger.info(
            "top_performing_prompts: returning %d scored prompts for ctype='%s'.",
            len(top_prompts),
            ctype,
        )
        return top_prompts

    # Fallback: return prompts from most-recent approved reviews (by rating)
    approved_sorted = sorted(
        approved_reviews,
        key=lambda r: (r.get("rating") or 0, r.get("timestamp", 0)),
        reverse=True,
    )
    fallback: List[str] = []
    seen_fb: set = set()
    for r in approved_sorted:
        prompt = r.get("prompt", "")
        if prompt and prompt not in seen_fb:
            fallback.append(prompt)
            seen_fb.add(prompt)
        if len(fallback) >= n:
            break

    logger.info(
        "top_performing_prompts: returning %d fallback prompts for ctype='%s'.",
        len(fallback),
        ctype,
    )
    return fallback
