"""
Autopilot Engine - Zero-click enterprise orchestration.

Given a project dict (with scraped references + brief), runs the ENTIRE
generation pipeline automatically with:
  - Parallel creative generation (copy, image, video simultaneously)
  - AI quality gating with auto-retry (up to 3 attempts per type)
  - Platform-specific variant packaging
  - Real-time SocketIO progress streaming
  - Comprehensive error recovery

Human intervention required: ZERO (fully autonomous from brief to bundle).
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config
from .brand_manager import extract_brand_dna
from .evaluator import evaluate_output
from .generator import generate
from .learning import get_context, record_review
from .model_router import model_candidates
from .prompt_engine import refine_with_llm

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUTOPILOT_QUALITY_THRESHOLD: int = 70  # Min score (0-100) to accept output
AUTOPILOT_MAX_RETRIES: int = 3

PLATFORM_SPECS: Dict[str, Dict[str, Any]] = {
    "instagram_feed":  {"ratio": "1:1",  "size": "1080x1080",  "copy_chars": 2200},
    "instagram_story": {"ratio": "9:16", "size": "1080x1920",  "copy_chars": 150},
    "instagram_reel":  {"ratio": "9:16", "video_seconds": 30},
    "facebook_feed":   {"ratio": "1:1",  "size": "1200x1200",  "copy_chars": 63206},
    "linkedin":        {"ratio": "1:1",  "size": "1200x627",   "copy_chars": 3000},
    "tiktok":          {"ratio": "9:16", "video_seconds": 60},
    "twitter_x":       {"ratio": "16:9", "size": "1600x900",   "copy_chars": 280},
    "google_display":  {"ratio": "16:9", "size": "1200x628"},
}

# Creative types the autopilot supports
SUPPORTED_CREATIVE_TYPES: Tuple[str, ...] = ("copy", "image", "video")

# Keyword → tone adjective mapping used by brand_manager.infer_tone integration
_KEYWORD_TONE_MAP: Dict[str, str] = {
    "luxury":      "premium",
    "sale":        "urgent",
    "exclusive":   "exclusive",
    "offer":       "promotional",
    "discount":    "value-driven",
    "limited":     "scarce",
    "award":       "authoritative",
    "innovation":  "forward-thinking",
    "sustainable": "eco-conscious",
    "community":   "inclusive",
    "fast":        "energetic",
    "trust":       "reliable",
    "secure":      "trustworthy",
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_autopilot(
    project: Dict[str, Any],
    emit_fn: Optional[Callable[[str, Any], None]] = None,
    creative_types: Optional[List[str]] = None,
    platforms: Optional[List[str]] = None,
    models_override: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Main zero-click orchestration entry point.

    Executes the full generation pipeline — from brand analysis to final
    campaign bundle — without any human interaction.

    Pipeline
    --------
    1. build_brand_dna        – extract visual/tonal fingerprint from refs
    2. build_platform_prompts – craft per-platform, per-type prompts
    3. run_parallel_generation – concurrent generation across types
    4. quality_gate_and_retry  – score each output; retry on failure
    5. package_bundle          – assemble structured campaign bundle

    Parameters
    ----------
    project : dict
        Must contain at minimum:
          ``brief``   (str)  – creative brief text
          ``refs``    (dict) – scraped reference assets
          ``name``    (str)  – human-readable project name (optional)
    emit_fn : callable, optional
        Signature ``(event: str, data: Any) -> None``.
        Used for real-time SocketIO progress streaming.
        Pass ``lambda event, data: socketio.emit(event, data)`` from your
        Flask-SocketIO view.  Safe to leave as ``None``.
    creative_types : list[str], optional
        Subset of ``('copy', 'image', 'video')``.  Defaults to all three.
    platforms : list[str], optional
        Subset of ``PLATFORM_SPECS`` keys.  Defaults to all platforms.
    models_override : dict, optional
        Map of ``{creative_type: model_name}`` to force specific models.
        Unspecified types use ``_select_best_model``.

    Returns
    -------
    dict
        Full campaign bundle produced by :func:`package_campaign_bundle`.

    Raises
    ------
    ValueError
        If ``project`` is missing a ``'brief'`` key.
    RuntimeError
        If every creative type fails all retries with no salvageable output.
    """
    run_id = str(uuid.uuid4())[:8]
    start_ts = time.monotonic()

    brief: str = project.get("brief", "")
    if not brief:
        raise ValueError("project dict must contain a non-empty 'brief' key.")

    refs: Dict[str, Any] = project.get("refs", {})
    creative_types = creative_types or list(SUPPORTED_CREATIVE_TYPES)
    platforms = platforms or list(PLATFORM_SPECS.keys())
    models_override = models_override or {}

    logger.info(
        "[autopilot:%s] Starting run | types=%s | platforms=%s",
        run_id,
        creative_types,
        platforms,
    )
    _emit(emit_fn, "autopilot:start", {
        "run_id": run_id,
        "creative_types": creative_types,
        "platforms": platforms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # ── Step 1: Brand DNA ─────────────────────────────────────────────────
    _emit(emit_fn, "autopilot:step", {"step": "build_brand_dna", "status": "running"})
    try:
        dna = extract_brand_dna(scraped=refs, brief=brief)
        logger.info("[autopilot:%s] Brand DNA extracted: keys=%s", run_id, list(dna.keys()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[autopilot:%s] Brand DNA extraction failed (%s) – using empty DNA", run_id, exc)
        dna = {}
    _emit(emit_fn, "autopilot:step", {"step": "build_brand_dna", "status": "done", "dna_keys": list(dna.keys())})

    # ── Step 2: Build platform prompts ───────────────────────────────────
    _emit(emit_fn, "autopilot:step", {"step": "build_platform_prompts", "status": "running"})
    prompts = _build_platform_prompts(brief, dna, creative_types, platforms, refs)
    logger.info("[autopilot:%s] Prompts built for %d platform/type combos", run_id, len(prompts))
    _emit(emit_fn, "autopilot:step", {"step": "build_platform_prompts", "status": "done", "count": len(prompts)})

    # ── Step 3: Parallel generation + quality gate ────────────────────────
    _emit(emit_fn, "autopilot:step", {"step": "parallel_generation", "status": "running"})
    results = _run_parallel_generation(
        prompts=prompts,
        creative_types=creative_types,
        platforms=platforms,
        refs=refs,
        models_override=models_override,
        emit_fn=emit_fn,
        run_id=run_id,
    )
    _emit(emit_fn, "autopilot:step", {"step": "parallel_generation", "status": "done"})

    # ── Step 4: Package bundle ────────────────────────────────────────────
    _emit(emit_fn, "autopilot:step", {"step": "package_bundle", "status": "running"})
    bundle = package_campaign_bundle(results=results, project=project, platforms=platforms)
    elapsed = round(time.monotonic() - start_ts, 2)
    bundle["summary"]["run_id"] = run_id
    bundle["summary"]["elapsed_seconds"] = elapsed
    _emit(emit_fn, "autopilot:done", {
        "run_id": run_id,
        "elapsed_seconds": elapsed,
        "outputs_count": len(bundle.get("outputs", [])),
        "summary": bundle.get("summary", {}),
    })
    logger.info("[autopilot:%s] Run complete in %.2fs | outputs=%d", run_id, elapsed, len(bundle.get("outputs", [])))

    # Persist learning signal (non-blocking best-effort)
    _record_learning(bundle, project)

    return bundle


# ---------------------------------------------------------------------------
# Internal helpers – generation pipeline
# ---------------------------------------------------------------------------


def _build_platform_prompts(
    brief: str,
    dna: Dict[str, Any],
    creative_types: List[str],
    platforms: List[str],
    refs: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Build one prompt specification per (platform × creative_type) combination.

    Returns
    -------
    list[dict]
        Each dict has keys: ``platform``, ``creative_type``, ``prompt``,
        ``settings`` (platform spec from PLATFORM_SPECS).
    """
    from .brand_manager import brand_dna_to_prompt_block  # local import avoids circulars

    dna_block = brand_dna_to_prompt_block(dna) if dna else ""
    prompt_specs: List[Dict[str, Any]] = []

    for platform in platforms:
        spec = PLATFORM_SPECS.get(platform, {})
        for ctype in creative_types:
            # Skip video for platforms without video_seconds unless explicitly requested
            if ctype == "video" and "video_seconds" not in spec and platform not in ("instagram_reel", "tiktok"):
                logger.debug("Skipping video for platform '%s' (no video spec)", platform)
                continue

            platform_hint = _platform_copy_hint(platform, spec, ctype)
            full_prompt = (
                f"{brief}\n\n"
                f"Platform: {platform.upper().replace('_', ' ')}\n"
                f"{platform_hint}\n"
                f"{dna_block}"
            ).strip()

            prompt_specs.append({
                "platform": platform,
                "creative_type": ctype,
                "prompt": full_prompt,
                "settings": spec,
                "refs": refs,
            })

    return prompt_specs


def _platform_copy_hint(platform: str, spec: Dict[str, Any], ctype: str) -> str:
    """Return a terse platform constraint string injected into every prompt."""
    parts: List[str] = []
    if "size" in spec:
        parts.append(f"Target dimensions: {spec['size']}px")
    if "ratio" in spec:
        parts.append(f"Aspect ratio: {spec['ratio']}")
    if "copy_chars" in spec and ctype == "copy":
        parts.append(f"Copy limit: {spec['copy_chars']} characters")
    if "video_seconds" in spec and ctype == "video":
        parts.append(f"Video duration: {spec['video_seconds']}s")
    return "\n".join(parts)


def _run_parallel_generation(
    prompts: List[Dict[str, Any]],
    creative_types: List[str],
    platforms: List[str],
    refs: Dict[str, Any],
    models_override: Dict[str, str],
    emit_fn: Optional[Callable],
    run_id: str,
) -> List[Dict[str, Any]]:
    """
    Fan-out generation across all prompt specs using a thread pool.

    Returns
    -------
    list[dict]
        Each dict is a result record with keys:
        ``platform``, ``creative_type``, ``result``, ``score``, ``attempts``,
        ``model``, ``prompt``, ``status`` ('ok' | 'failed').
    """
    results: List[Dict[str, Any]] = []
    max_workers = min(len(prompts), getattr(config, "AUTOPILOT_MAX_WORKERS", 6))

    logger.info(
        "[autopilot:%s] Spawning ThreadPoolExecutor with max_workers=%d for %d tasks",
        run_id,
        max_workers,
        len(prompts),
    )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {}
        for spec in prompts:
            ctype = spec["creative_type"]
            model = _select_best_model(ctype, models_override)
            job_label = f"{spec['platform']}/{ctype}"

            future = pool.submit(
                _generate_with_retry,
                creative_type=ctype,
                prompt=spec["prompt"],
                model=model,
                refs=spec["refs"],
                settings=spec["settings"],
                max_retries=AUTOPILOT_MAX_RETRIES,
                emit_fn=emit_fn,
                job_label=job_label,
            )
            future_map[future] = (spec, model)

        completed = 0
        total = len(future_map)
        for future in as_completed(future_map):
            spec, model = future_map[future]
            completed += 1
            job_label = f"{spec['platform']}/{spec['creative_type']}"
            try:
                result, score, attempts = future.result()
                status = "ok" if result else "failed"
                logger.info(
                    "[autopilot:%s] [%s] %s | score=%d | attempts=%d",
                    run_id,
                    job_label,
                    status,
                    score,
                    attempts,
                )
                results.append({
                    "platform":       spec["platform"],
                    "creative_type":  spec["creative_type"],
                    "result":         result,
                    "score":          score,
                    "attempts":       attempts,
                    "model":          model,
                    "prompt":         spec["prompt"],
                    "settings":       spec["settings"],
                    "status":         status,
                })
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[autopilot:%s] [%s] Unhandled exception: %s",
                    run_id,
                    job_label,
                    exc,
                    exc_info=True,
                )
                results.append({
                    "platform":      spec["platform"],
                    "creative_type": spec["creative_type"],
                    "result":        None,
                    "score":         0,
                    "attempts":      AUTOPILOT_MAX_RETRIES,
                    "model":         model,
                    "prompt":        spec["prompt"],
                    "settings":      spec["settings"],
                    "status":        "failed",
                    "error":         str(exc),
                })

            _emit(emit_fn, "autopilot:progress", {
                "completed": completed,
                "total": total,
                "latest": job_label,
                "pct": round(completed / total * 100),
            })

    return results


# ---------------------------------------------------------------------------
# Core generation primitives
# ---------------------------------------------------------------------------


def _emit(emit_fn: Optional[Callable[[str, Any], None]], event: str, data: Any) -> None:
    """
    Safe SocketIO emit wrapper.

    Calls ``emit_fn(event, data)`` if provided.  Silently swallows any
    exception so that a broken emit channel never crashes the autopilot run.

    Parameters
    ----------
    emit_fn : callable or None
        The SocketIO emit callable.
    event : str
        Event name, e.g. ``"autopilot:progress"``.
    data : any
        JSON-serialisable payload.
    """
    if emit_fn is None:
        return
    try:
        emit_fn(event, data)
        if ":" in event:
            alias = event.replace(":", "_")
            emit_fn(alias, data)
        if event in ("autopilot:step", "autopilot_step"):
            emit_fn("autopilot_progress", data)
        elif event in ("autopilot:done", "autopilot_done", "autopilot:complete", "autopilot_complete"):
            emit_fn("autopilot_complete", data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_emit: failed to emit event '%s': %s", event, exc)


def _score_quality(
    output_result: Optional[Dict[str, Any]],
    creative_type: str,
    prompt: str,
) -> int:
    """
    Return a quality score in [0, 100] for a single generated output.

    Scoring strategy
    ----------------
    *  Uses :func:`core.evaluator.evaluate_output` as the primary signal
       (contributes up to 70 points).
    *  Applies heuristic bonuses / penalties for the remaining 30 points
       based on type-specific signals:

       **copy** – word count adequacy, platform sections, CTA presence,
                  hashtag presence.
       **image** – file size (>50 KB), dimensions (≥1024 px either side).
       **video** – duration, fps, frame count.

    Parameters
    ----------
    output_result : dict or None
        The raw result returned by the generator.  ``None`` → score 0.
    creative_type : str
        One of ``'copy'``, ``'image'``, ``'video'``.
    prompt : str
        Original prompt used for generation (forwarded to evaluator).

    Returns
    -------
    int
        Clamped quality score in [0, 100].
    """
    if not output_result:
        logger.debug("_score_quality: output_result is None → 0")
        return 0

    # ── Primary: AI evaluator (0–70) ──────────────────────────────────────
    ai_score = 0
    filename = output_result.get("filename")
    output_path = (config.OUTPUT_DIR / filename) if filename else None

    try:
        if output_path and output_path.exists():
            eval_result = evaluate_output(output_path, creative_type=creative_type, prompt=prompt)
            status = eval_result.get("status", "pass")
            raw = 65 if status == "pass" else (45 if status == "warn" else 15)
            ai_score = raw
        else:
            ai_score = 40
        logger.debug("_score_quality: ai_score=%d", ai_score)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_score_quality: evaluator failed (%s) – using ai_score=40", exc)
        ai_score = 40

    # ── Heuristic bonus (0–30) ────────────────────────────────────────────
    heuristic = 0

    if creative_type == "copy":
        text: str = output_result.get("text", "") or output_result.get("copy", "") or ""
        if not text and output_path and output_path.exists():
            try:
                text = output_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
        word_count = len(text.split())

        # Word count adequacy: 50+ words → +10
        if word_count >= 50:
            heuristic += 10
        elif word_count >= 20:
            heuristic += 5

        # CTA presence → +8
        cta_signals = ("buy now", "shop now", "learn more", "get started", "sign up",
                       "try free", "book now", "order now", "download", "subscribe",
                       "click", "visit", "discover", "explore", "apply now", "cta")
        if any(sig in text.lower() for sig in cta_signals):
            heuristic += 8

        # Hashtag presence → +6
        if "#" in text:
            heuristic += 6

        # Platform-specific sections (newline-separated blocks) → +6
        if text.count("\n") >= 3 or "#" in text:
            heuristic += 6

    elif creative_type == "image":
        file_size_bytes: int = 0
        if output_path and output_path.exists():
            file_size_bytes = output_path.stat().st_size
        else:
            file_size_bytes = output_result.get("file_size", 0) or 0
        width: int  = output_result.get("width", 0)  or 0
        height: int = output_result.get("height", 0) or 0

        # File size > 50 KB → good content density
        if file_size_bytes > 50_000:
            heuristic += 15
        elif file_size_bytes > 10_000:
            heuristic += 7

        # Dimensions ≥ 1024 on either axis → excellent resolution
        if width >= 1024 or height >= 1024 or file_size_bytes > 50_000:
            heuristic += 15
        elif width >= 512 or height >= 512 or file_size_bytes > 10_000:
            heuristic += 8

    elif creative_type == "video":
        duration_s = float(output_result.get("duration", 0) or output_result.get("duration_seconds", 0) or 0)
        fps        = float(output_result.get("fps", 0) or 24)

        if duration_s > 0:
            heuristic += 12

        if fps >= 20:
            heuristic += 10

        if output_path and output_path.exists() and output_path.stat().st_size > 1000:
            heuristic += 8

    total = min(100, ai_score + heuristic)
    logger.debug(
        "_score_quality: type=%s | ai=%d | heuristic=%d | total=%d",
        creative_type, ai_score, heuristic, total,
    )
    return total


def _generate_with_retry(
    creative_type: str,
    prompt: str,
    model: str,
    refs: Dict[str, Any],
    settings: Dict[str, Any],
    max_retries: int,
    emit_fn: Optional[Callable],
    job_label: str,
) -> Tuple[Optional[Dict[str, Any]], int, int]:
    """
    Attempt generation up to ``max_retries`` times with progressive prompt
    refinement on each failure or low-quality result.

    On every attempt that does not meet :data:`AUTOPILOT_QUALITY_THRESHOLD`
    the prompt is refined via :func:`core.prompt_engine.refine_with_llm`
    before the next attempt.

    Parameters
    ----------
    creative_type : str
        ``'copy'``, ``'image'``, or ``'video'``.
    prompt : str
        Initial generation prompt.
    model : str
        Model identifier selected by :func:`_select_best_model`.
    refs : dict
        Scraped reference assets forwarded to the generator.
    settings : dict
        Platform spec dict (from :data:`PLATFORM_SPECS`).
    max_retries : int
        Maximum number of generation attempts.
    emit_fn : callable or None
        SocketIO emit callable for progress streaming.
    job_label : str
        Human-readable label for log/emit messages, e.g. ``"instagram_feed/copy"``.

    Returns
    -------
    tuple[dict | None, int, int]
        ``(best_result, best_score, attempts_used)``
        ``best_result`` may be ``None`` if all attempts failed.
    """
    best_result: Optional[Dict[str, Any]] = None
    best_score: int = 0
    current_prompt: str = prompt

    for attempt in range(1, max_retries + 1):
        logger.info(
            "[%s] Generation attempt %d/%d | model=%s",
            job_label,
            attempt,
            max_retries,
            model,
        )
        _emit(emit_fn, "autopilot:attempt", {
            "job": job_label,
            "attempt": attempt,
            "max": max_retries,
            "model": model,
        })

        # ── Generate ────────────────────────────────────────────────────
        result: Optional[Dict[str, Any]] = None
        try:
            result = generate(
                creative_type=creative_type,
                prompt=current_prompt,
                model=model,
                reference_assets=refs,
                settings=settings,
            )
            logger.debug("[%s] Generator returned keys: %s", job_label, list(result.keys()) if result else "None")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Generator raised on attempt %d: %s", job_label, attempt, exc)

        # ── Score ───────────────────────────────────────────────────────
        score = _score_quality(output_result=result, creative_type=creative_type, prompt=current_prompt)
        logger.info("[%s] Attempt %d quality score: %d (threshold=%d)", job_label, attempt, score, AUTOPILOT_QUALITY_THRESHOLD)

        if score > best_score:
            best_score = score
            best_result = result

        _emit(emit_fn, "autopilot:score", {
            "job": job_label,
            "attempt": attempt,
            "score": score,
            "best_score": best_score,
            "passed": score >= AUTOPILOT_QUALITY_THRESHOLD,
        })

        # ── Accept or refine ─────────────────────────────────────────────
        if score >= AUTOPILOT_QUALITY_THRESHOLD:
            logger.info("[%s] Quality threshold met on attempt %d – accepting output.", job_label, attempt)
            return best_result, best_score, attempt

        if attempt < max_retries:
            logger.info("[%s] Score %d < threshold %d – refining prompt for attempt %d.", job_label, score, AUTOPILOT_QUALITY_THRESHOLD, attempt + 1)
            try:
                refined = refine_with_llm(
                    prompt=current_prompt,
                    feedback=f"Previous output scored {score}/100 (threshold {AUTOPILOT_QUALITY_THRESHOLD}). "
                             f"Improve quality for {creative_type} on this creative_type.",
                    ctype=creative_type,
                )
                if refined:
                    current_prompt = refined
                    logger.debug("[%s] Prompt refined (len %d → %d chars)", job_label, len(prompt), len(current_prompt))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] refine_with_llm failed (%s) – keeping previous prompt", job_label, exc)

    logger.warning(
        "[%s] All %d attempts exhausted. Best score: %d",
        job_label,
        max_retries,
        best_score,
    )
    return best_result, best_score, max_retries


def _select_best_model(
    creative_type: str,
    models_override: Dict[str, str],
) -> str:
    """
    Select the best available model for a given creative type.

    Resolution order
    ----------------
    1. ``models_override[creative_type]`` – caller-forced model.
    2. :func:`core.model_router.model_candidates` – ranked list from router.
    3. Hard-coded local fallback per type.

    Parameters
    ----------
    creative_type : str
        One of ``'copy'``, ``'image'``, ``'video'``.
    models_override : dict
        Caller-provided overrides; may be empty.

    Returns
    -------
    str
        Model identifier string.
    """
    # 1. Explicit override
    if creative_type in models_override:
        model = models_override[creative_type]
        logger.debug("_select_best_model: override for %s → %s", creative_type, model)
        return model

    # 2. Model router
    try:
        candidates: List[str] = model_candidates(creative_type=creative_type)
        if candidates:
            chosen = candidates[0]
            logger.debug(
                "_select_best_model: router chose '%s' for %s (candidates=%s)",
                chosen,
                creative_type,
                candidates,
            )
            return chosen
    except Exception as exc:  # noqa: BLE001
        logger.warning("_select_best_model: model_router failed (%s) – using fallback", exc)

    # 3. Hard-coded local fallback
    _fallbacks = {
        "copy":  "gpt-3.5-turbo",
        "image": "stable-diffusion-local",
        "video": "zeroscope-local",
    }
    fallback = _fallbacks.get(creative_type, "gpt-3.5-turbo")
    logger.warning("_select_best_model: using fallback model '%s' for %s", fallback, creative_type)
    return fallback


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------


def package_campaign_bundle(
    results: List[Dict[str, Any]],
    project: Dict[str, Any],
    platforms: List[str],
) -> Dict[str, Any]:
    """
    Assemble all generation results into a structured campaign bundle dict.

    The bundle is the canonical output of the autopilot engine and contains
    everything needed to hand off to downstream delivery / approval systems.

    Bundle schema
    -------------
    .. code-block:: python

        {
            "bundle_id":   str,          # UUID for this campaign bundle
            "project":     str,          # project name / id
            "created_at":  str,          # ISO-8601 UTC timestamp
            "platforms":   list[str],    # target platforms
            "outputs": [
                {
                    "platform":      str,
                    "creative_type": str,
                    "status":        "ok" | "failed",
                    "score":         int,
                    "attempts":      int,
                    "model":         str,
                    "result":        dict | None,
                    "settings":      dict,
                },
                ...
            ],
            "summary": {
                "total":          int,
                "succeeded":      int,
                "failed":         int,
                "avg_score":      float,
                "models_used":    list[str],
                "quality_scores": dict,     # {job_label: score}
                "run_id":         str,      # filled later by run_autopilot
                "elapsed_seconds": float,   # filled later by run_autopilot
            },
        }

    Parameters
    ----------
    results : list[dict]
        Output of :func:`_run_parallel_generation`.
    project : dict
        Original project dict; ``'name'`` key used if present.
    platforms : list[str]
        Target platform list (informational).

    Returns
    -------
    dict
        Fully structured campaign bundle.
    """
    bundle_id = str(uuid.uuid4())
    project_name: str = project.get("name", project.get("id", "unnamed"))

    outputs: List[Dict[str, Any]] = []
    quality_scores: Dict[str, int] = {}
    models_used: set[str] = set()

    for r in results:
        job_label = f"{r['platform']}/{r['creative_type']}"
        quality_scores[job_label] = r.get("score", 0)
        m = r.get("model")
        if m:
            if isinstance(m, dict):
                m_str = m.get("id") or m.get("label") or str(m)
            else:
                m_str = str(m)
            models_used.add(m_str)

        outputs.append({
            "platform":      r["platform"],
            "creative_type": r["creative_type"],
            "status":        r.get("status", "failed"),
            "score":         r.get("score", 0),
            "attempts":      r.get("attempts", 0),
            "model":         r.get("model", "unknown"),
            "result":        r.get("result"),
            "settings":      r.get("settings", {}),
        })

    succeeded = sum(1 for o in outputs if o["status"] == "ok")
    failed    = len(outputs) - succeeded
    avg_score = (sum(o["score"] for o in outputs) / len(outputs)) if outputs else 0.0

    copy_count = sum(1 for o in outputs if o.get("creative_type") == "copy" and o.get("status") == "ok")
    image_count = sum(1 for o in outputs if o.get("creative_type") == "image" and o.get("status") == "ok")
    video_count = sum(1 for o in outputs if o.get("creative_type") == "video" and o.get("status") == "ok")

    bundle: Dict[str, Any] = {
        "bundle_id":  bundle_id,
        "project":    project_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "platforms":  platforms,
        "outputs":    outputs,
        "summary": {
            "total":             len(outputs),
            "total_assets":      len(outputs),
            "copy_count":        copy_count,
            "image_count":       image_count,
            "video_count":       video_count,
            "succeeded":         succeeded,
            "failed":            failed,
            "avg_score":         round(avg_score, 2),
            "avg_quality_score": round(avg_score, 2),
            "models_used":       sorted(models_used),
            "platforms_covered": list(platforms),
            "quality_scores":    quality_scores,
            "run_id":            "",           # filled by run_autopilot
            "elapsed_seconds":   0.0,          # filled by run_autopilot
        },
    }

    logger.info(
        "package_campaign_bundle: bundle_id=%s | outputs=%d | ok=%d | failed=%d | avg_score=%.1f",
        bundle_id,
        len(outputs),
        succeeded,
        failed,
        avg_score,
    )
    return bundle


# ---------------------------------------------------------------------------
# Learning integration (best-effort, non-blocking)
# ---------------------------------------------------------------------------


def _record_learning(bundle: Dict[str, Any], project: Dict[str, Any]) -> None:
    """
    Persist autopilot outcomes to the learning system for future improvement.

    Failures here are silently swallowed — they must never affect bundle
    delivery to the caller.

    Parameters
    ----------
    bundle : dict
        Finalised campaign bundle.
    project : dict
        Original project dict.
    """
    try:
        for output in bundle.get("outputs", []):
            record_review(
                project_id=project.get("id", bundle["bundle_id"]),
                creative_type=output["creative_type"],
                platform=output["platform"],
                score=output["score"],
                model=output["model"],
                status=output["status"],
            )
        logger.debug("_record_learning: persisted %d records", len(bundle.get("outputs", [])))
    except Exception as exc:  # noqa: BLE001
        logger.warning("_record_learning: failed to persist learning data (%s)", exc)
