"""
Campaign Packager.

Assembles generated copy, image, and video outputs into a structured
campaign bundle. Produces:
  - Structured bundle dict (JSON-serializable)
  - HTML campaign summary card (for in-browser preview)
  - Per-platform asset manifest
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Type → display label + emoji icon
ASSET_TYPE_META: dict[str, dict[str, str]] = {
    "copy":  {"label": "Copy",  "icon": "📝"},
    "image": {"label": "Image", "icon": "🖼️"},
    "video": {"label": "Video", "icon": "🎬"},
}

# Quality badge thresholds (score out of 100)
QUALITY_GREEN  = 80
QUALITY_AMBER  = 60

# Fallback platform specs used when autopilot.PLATFORM_SPECS is unavailable.
# Each spec describes the technical constraints for a target platform.
_FALLBACK_PLATFORM_SPECS: dict[str, dict[str, Any]] = {
    "instagram": {
        "allowed_types":       ["image", "video", "copy"],
        "image_min_px":        1080,
        "video_max_seconds":   60,
        "copy_max_chars":      2200,
    },
    "facebook": {
        "allowed_types":       ["image", "video", "copy"],
        "image_min_px":        1200,
        "video_max_seconds":   240,
        "copy_max_chars":      63206,
    },
    "twitter": {
        "allowed_types":       ["image", "video", "copy"],
        "image_min_px":        900,
        "video_max_seconds":   140,
        "copy_max_chars":      280,
    },
    "linkedin": {
        "allowed_types":       ["image", "video", "copy"],
        "image_min_px":        1200,
        "video_max_seconds":   600,
        "copy_max_chars":      3000,
    },
    "youtube": {
        "allowed_types":       ["video", "copy"],
        "image_min_px":        1280,
        "video_max_seconds":   43200,
        "copy_max_chars":      5000,
    },
    "tiktok": {
        "allowed_types":       ["video", "copy"],
        "image_min_px":        1080,
        "video_max_seconds":   180,
        "copy_max_chars":      2200,
    },
    "email": {
        "allowed_types":       ["copy", "image"],
        "image_min_px":        600,
        "video_max_seconds":   0,
        "copy_max_chars":      100000,
    },
}


def _load_platform_specs() -> dict[str, dict[str, Any]]:
    """Return platform specs from autopilot if available, else use fallback.

    This defensive import keeps campaign_packager fully functional even when
    the autopilot module has not been written yet.
    """
    try:
        from .autopilot import PLATFORM_SPECS  # type: ignore[import]
        logger.debug("Loaded PLATFORM_SPECS from autopilot module.")
        return PLATFORM_SPECS  # type: ignore[return-value]
    except (ImportError, AttributeError):
        logger.debug("autopilot.PLATFORM_SPECS unavailable; using built-in fallback specs.")
        return _FALLBACK_PLATFORM_SPECS


PLATFORM_SPECS: dict[str, dict[str, Any]] = _load_platform_specs()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_output_path(filename: str) -> Path:
    """Return the absolute Path for an output file from ``config.OUTPUT_DIR``.

    Args:
        filename: Basename (or relative sub-path) of the output file.

    Returns:
        Absolute :class:`~pathlib.Path` pointing into ``config.OUTPUT_DIR``.
    """
    return config.OUTPUT_DIR / filename


# ---------------------------------------------------------------------------
# Core assembly
# ---------------------------------------------------------------------------


def build_bundle(
    outputs: list[dict],
    project: dict,
    brand_dna: dict,
    quality_scores: dict,
) -> dict:
    """Assemble all generator outputs into a structured campaign bundle.

    Args:
        outputs:       List of dicts returned by ``generator.generate()``.  Each
                       dict must contain at least ``filename``, ``model_used``,
                       ``url``, and ``creative_type``.  ``platform`` is optional.
        project:       Project metadata dict (must contain ``name`` key at
                       minimum; all other keys are included verbatim).
        brand_dna:     Brand DNA dict (palette, tone, etc.) as produced by the
                       brand analyser.
        quality_scores: Mapping of ``filename → score_dict`` as returned by
                        ``evaluator.ai_score_output()``.  Missing filenames
                        default to ``{"total": 50}``.

    Returns:
        A JSON-serializable bundle dict with the following top-level keys:
        ``bundle_id``, ``campaign_name``, ``created_at``, ``outputs``,
        ``summary``, ``brand_dna``, and ``html_summary``.

    Raises:
        ValueError: If ``outputs`` is ``None``.
    """
    if outputs is None:
        raise ValueError("`outputs` must be a list, not None.")

    bundle_id      = f"bndl_{uuid.uuid4().hex[:12]}"
    campaign_name  = (project or {}).get("name") or (project or {}).get("title") or ((project or {}).get("brief") or "")[:60] or "Untitled Campaign"
    created_at     = time.time()

    logger.info(
        "Building bundle %s for campaign '%s' with %d output(s).",
        bundle_id,
        campaign_name,
        len(outputs),
    )

    # ---- Enrich each output ------------------------------------------------
    enriched: list[dict] = []
    models_seen: set[str] = set()
    platforms_seen: set[str] = set()
    copy_count = image_count = video_count = 0
    score_total = 0.0

    for raw in outputs:
        out: dict = dict(raw)  # shallow copy — never mutate caller's data

        filename      = out.get("filename", "")
        creative_type = str(out.get("creative_type", "")).lower()
        platform      = str(out.get("platform", "")).lower().strip()

        # Quality score
        score_val = quality_scores.get(filename) if quality_scores else None
        if isinstance(score_val, (int, float)):
            score_dict = {"total": int(score_val)}
        elif isinstance(score_val, dict):
            score_dict = score_val
        else:
            score_dict = {"total": 50}
        out["quality_score"] = score_dict.get("total", 50)
        out["quality_detail"] = score_dict

        # Platform labels (single string or list)
        raw_platforms: list[str] = []
        if platform:
            raw_platforms = [p.strip() for p in platform.split(",") if p.strip()]
        out["platforms"] = raw_platforms
        platforms_seen.update(raw_platforms)

        # Model tracking
        model = out.get("model_used", "")
        if model:
            models_seen.add(model)

        # Type counters
        if creative_type == "copy":
            copy_count += 1
        elif creative_type == "image":
            image_count += 1
        elif creative_type == "video":
            video_count += 1

        score_total += float(out["quality_score"])
        enriched.append(out)

    n = len(enriched)
    avg_quality = round(score_total / n, 1) if n else 0.0

    summary: dict = {
        "total_assets":      n,
        "copy_count":        copy_count,
        "image_count":       image_count,
        "video_count":       video_count,
        "models_used":       sorted(models_seen),
        "avg_quality_score": avg_quality,
        "platforms_covered": sorted(platforms_seen),
    }

    bundle: dict = {
        "bundle_id":     bundle_id,
        "campaign_name": campaign_name,
        "created_at":    created_at,
        "outputs":       enriched,
        "summary":       summary,
        "brand_dna":     brand_dna or {},
        "html_summary":  "",          # filled below
    }

    bundle["html_summary"] = render_html_summary(bundle)

    logger.info(
        "Bundle %s ready — %d assets, avg_quality=%.1f, platforms=%s.",
        bundle_id,
        n,
        avg_quality,
        sorted(platforms_seen),
    )
    return bundle


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

# Colour tokens used by the HTML card (no external stylesheet required)
_CSS = """
  body{font-family:system-ui,sans-serif;margin:0;background:#f5f5f5}
  .card{background:#fff;border-radius:12px;box-shadow:0 2px 16px rgba(0,0,0,.10);
        max-width:720px;margin:24px auto;padding:28px 32px;box-sizing:border-box}
  h2{margin:0 0 4px;font-size:1.45rem;color:#1a1a2e}
  .ts{color:#888;font-size:.82rem;margin-bottom:18px}
  .counters{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:20px}
  .counter{background:#f0f4ff;border-radius:8px;padding:12px 18px;
           min-width:110px;text-align:center}
  .counter .num{font-size:1.9rem;font-weight:700;color:#2d3af0}
  .counter .lbl{font-size:.8rem;color:#555;margin-top:2px}
  .section{margin-bottom:16px}
  .section h4{margin:0 0 8px;font-size:.92rem;text-transform:uppercase;
              letter-spacing:.06em;color:#444}
  .badge{display:inline-block;border-radius:999px;padding:3px 11px;
         font-size:.8rem;font-weight:600;color:#fff;margin:2px 3px}
  .green{background:#22a55c} .amber{background:#d97706} .red{background:#dc2626}
  .platform-tag{display:inline-block;background:#e8eeff;color:#2d3af0;
                border-radius:6px;padding:2px 10px;font-size:.8rem;margin:2px 3px}
  .model-tag{display:inline-block;background:#f0fdf4;color:#166534;
             border-radius:6px;padding:2px 10px;font-size:.8rem;margin:2px 3px}
  .swatch-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
  .swatch{width:32px;height:32px;border-radius:6px;border:1px solid #ddd;
          display:inline-block;title-attr:'';}
  .avg-score{display:inline-block;font-size:1.15rem;font-weight:700;padding:4px 16px;
             border-radius:8px;margin-left:8px}
  table.assets{width:100%;border-collapse:collapse;font-size:.83rem}
  table.assets th{text-align:left;border-bottom:2px solid #eee;padding:6px 8px;color:#666}
  table.assets td{padding:6px 8px;border-bottom:1px solid #f2f2f2;vertical-align:top}
  table.assets tr:last-child td{border-bottom:none}
"""


def _quality_badge(score: float) -> str:
    """Return an HTML badge <span> colour-coded by score threshold."""
    if score >= QUALITY_GREEN:
        cls, label = "green", "Good"
    elif score >= QUALITY_AMBER:
        cls, label = "amber", "Fair"
    else:
        cls, label = "red", "Low"
    return f'<span class="badge {cls}">{int(score)} — {label}</span>'


def _avg_score_style(score: float) -> str:
    if score >= QUALITY_GREEN:
        return "background:#d1fae5;color:#065f46"
    if score >= QUALITY_AMBER:
        return "background:#fef3c7;color:#92400e"
    return "background:#fee2e2;color:#991b1b"


def render_html_summary(bundle: dict) -> str:
    """Render a self-contained HTML card for the campaign bundle.

    No external CSS or JavaScript dependencies are required — the card is fully
    inline and suitable for embedding in any HTML page or email.

    Args:
        bundle: A bundle dict as returned by :func:`build_bundle`.

    Returns:
        A UTF-8 HTML snippet string.
    """
    summary       = bundle.get("summary", {})
    brand_dna     = bundle.get("brand_dna", {})
    outputs       = bundle.get("outputs", [])
    campaign_name = bundle.get("campaign_name", "Untitled Campaign")
    created_at    = bundle.get("created_at", time.time())
    bundle_id     = bundle.get("bundle_id", "")

    ts_str = time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(created_at))
    avg_q  = summary.get("avg_quality_score", 0.0)

    # ---- counters ----------------------------------------------------------
    counters_html = ""
    for key, meta in ASSET_TYPE_META.items():
        cnt = summary.get(f"{key}_count", 0)
        counters_html += (
            f'<div class="counter">'
            f'<div class="num">{meta["icon"]}&nbsp;{cnt}</div>'
            f'<div class="lbl">{meta["label"]} assets</div>'
            f'</div>'
        )
    counters_html += (
        f'<div class="counter">'
        f'<div class="num">{summary.get("total_assets", 0)}</div>'
        f'<div class="lbl">Total assets</div>'
        f'</div>'
    )

    # ---- quality avg badge -------------------------------------------------
    avg_style    = _avg_score_style(avg_q)
    avg_html     = (
        f'<span class="avg-score" style="{avg_style}">'
        f'Avg quality: {avg_q:.1f}/100</span>'
    )

    # ---- platforms ---------------------------------------------------------
    platforms_html = "".join(
        f'<span class="platform-tag">{p}</span>'
        for p in summary.get("platforms_covered", [])
    ) or '<span style="color:#aaa">—</span>'

    # ---- models used -------------------------------------------------------
    models_html = "".join(
        f'<span class="model-tag">{m}</span>'
        for m in summary.get("models_used", [])
    ) or '<span style="color:#aaa">—</span>'

    # ---- brand palette swatches -------------------------------------------
    palette     = brand_dna.get("palette") or brand_dna.get("colors") or []
    swatches_html = ""
    if palette:
        swatches_html = '<div class="section"><h4>Brand palette</h4><div class="swatch-row">'
        for colour in palette[:12]:
            # Colour may be a hex string or a dict with a 'hex' key
            if isinstance(colour, dict):
                hex_val = colour.get("hex") or colour.get("value") or ""
            else:
                hex_val = str(colour)
            if hex_val:
                swatches_html += (
                    f'<span class="swatch" style="background:{hex_val}" '
                    f'title="{hex_val}"></span>'
                )
        swatches_html += "</div></div>"

    # ---- per-asset table ---------------------------------------------------
    rows_html = ""
    for out in outputs:
        ctype    = str(out.get("creative_type", "—")).lower()
        icon     = ASSET_TYPE_META.get(ctype, {}).get("icon", "📄")
        fname    = out.get("filename", "—")
        model    = out.get("model_used", "—")
        score    = float(out.get("quality_score", 50))
        plats    = ", ".join(out.get("platforms", [])) or "—"
        badge    = _quality_badge(score)
        rows_html += (
            f"<tr>"
            f"<td>{icon} <code>{fname}</code></td>"
            f"<td>{ctype}</td>"
            f"<td>{badge}</td>"
            f"<td>{plats}</td>"
            f"<td><small>{model}</small></td>"
            f"</tr>"
        )

    assets_table = ""
    if rows_html:
        assets_table = f"""
        <div class="section">
          <h4>Assets</h4>
          <table class="assets">
            <thead>
              <tr>
                <th>File</th><th>Type</th><th>Quality</th>
                <th>Platforms</th><th>Model</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{campaign_name} — Campaign Bundle</title>
<style>{_CSS}</style>
</head>
<body>
<div class="card">
  <h2>📦 {campaign_name}</h2>
  <div class="ts">
    Bundle <code>{bundle_id}</code> &nbsp;·&nbsp; Generated {ts_str}
    {avg_html}
  </div>

  <div class="counters">{counters_html}</div>

  <div class="section">
    <h4>Platforms covered</h4>
    {platforms_html}
  </div>

  <div class="section">
    <h4>Models used</h4>
    {models_html}
  </div>

  {swatches_html}

  {assets_table}
</div>
</body>
</html>"""

    logger.debug("Rendered HTML summary for bundle %s (%d bytes).", bundle_id, len(html))
    return html


# ---------------------------------------------------------------------------
# Platform validation
# ---------------------------------------------------------------------------


def validate_platform_specs(outputs: list[dict]) -> list[dict]:
    """Validate each output against the platform technical constraints.

    Checks:
    - ``creative_type`` is allowed on the target platform.
    - Image dimensions meet the platform minimum (requires ``width``/``height``
      in the output dict; silently skips if absent).
    - Video duration is within the platform maximum.
    - Copy character count is within the platform maximum.

    Args:
        outputs: List of enriched output dicts (as stored inside a bundle).

    Returns:
        A list of validation issue dicts, each containing:

        - ``output_filename`` – basename of the offending file.
        - ``platform``        – target platform name.
        - ``issue``           – human-readable description of the problem.
        - ``severity``        – ``"error"`` or ``"warning"``.

        An empty list means all outputs pass validation.
    """
    issues: list[dict] = []

    for out in outputs:
        filename      = out.get("filename", "<unknown>")
        creative_type = str(out.get("creative_type", "")).lower()
        platforms     = out.get("platforms") or []

        # Also handle the legacy single-string 'platform' field
        if not platforms:
            raw = out.get("platform", "")
            if raw:
                platforms = [p.strip() for p in str(raw).split(",") if p.strip()]

        if not platforms:
            # No platform declared — nothing to validate against
            continue

        for platform in platforms:
            spec = PLATFORM_SPECS.get(platform.lower())
            if spec is None:
                issues.append({
                    "output_filename": filename,
                    "platform":        platform,
                    "issue":           f"Unknown platform '{platform}' — no spec available.",
                    "severity":        "warning",
                })
                continue

            allowed_types: list[str] = spec.get("allowed_types", [])
            if creative_type and allowed_types and creative_type not in allowed_types:
                issues.append({
                    "output_filename": filename,
                    "platform":        platform,
                    "issue": (
                        f"Creative type '{creative_type}' is not allowed on {platform}. "
                        f"Allowed: {allowed_types}."
                    ),
                    "severity": "error",
                })

            # Image dimension check
            if creative_type == "image":
                min_px = spec.get("image_min_px", 0)
                width  = out.get("width") or out.get("checks", {}).get("width", 0)
                height = out.get("height") or out.get("checks", {}).get("height", 0)
                if min_px and width and height:
                    if min(int(width), int(height)) < min_px:
                        issues.append({
                            "output_filename": filename,
                            "platform":        platform,
                            "issue": (
                                f"Image is {width}×{height}px; {platform} requires "
                                f"at least {min_px}px on the shortest side."
                            ),
                            "severity": "warning",
                        })

            # Video duration check
            if creative_type == "video":
                max_sec = spec.get("video_max_seconds", 0)
                duration = (
                    out.get("duration")
                    or out.get("checks", {}).get("duration_seconds", 0)
                )
                if max_sec and duration and float(duration) > max_sec:
                    issues.append({
                        "output_filename": filename,
                        "platform":        platform,
                        "issue": (
                            f"Video is {duration:.1f}s; {platform} maximum is {max_sec}s."
                        ),
                        "severity": "error",
                    })

            # Copy character count check
            if creative_type == "copy":
                max_chars = spec.get("copy_max_chars", 0)
                char_count = (
                    out.get("characters")
                    or out.get("checks", {}).get("characters", 0)
                )
                if max_chars and char_count and int(char_count) > max_chars:
                    issues.append({
                        "output_filename": filename,
                        "platform":        platform,
                        "issue": (
                            f"Copy is {char_count} chars; {platform} maximum is {max_chars} chars."
                        ),
                        "severity": "warning",
                    })

    if issues:
        logger.warning(
            "validate_platform_specs: %d issue(s) found across %d output(s).",
            len(issues),
            len(outputs),
        )
    else:
        logger.info("validate_platform_specs: all outputs passed platform checks.")

    return issues
