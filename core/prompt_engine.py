"""Prompt engineering: blends brief + reference intelligence + mood + memory
into production-ready prompts, with optional LLM refinement.

Enhanced capabilities:
- Multi-variant generation (4 variants per creative type)
- Platform-specific prompt tailoring with character-limit awareness
- Brand DNA injection via brand_manager integration
- Storyboard-style video prompts with shot-by-shot structure
- Advanced copy templates per platform (Instagram, LinkedIn, TikTok, etc.)
"""

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from .ai import chat
from .learning import get_context
from . import config
from .creative_intelligence import build_dna, enrich_with_vision

# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stopwords for keyword extraction
# ---------------------------------------------------------------------------
STOPWORDS = set(
    "the a an and or but of to in on for with at by from is are was were "
    "this that these those it its as be been being will would can could "
    "should may might do does did not no yes we you they he she i our your "
    "their his her them us".split()
)

# ---------------------------------------------------------------------------
# Platform copy format specifications
# ---------------------------------------------------------------------------
PLATFORM_COPY_FORMATS: Dict[str, dict] = {
    "instagram": {
        "hook": "Start with an attention-grabbing hook (emoji + bold statement, max 2 lines)",
        "body": "3-5 lines of benefit-driven copy, single idea per line",
        "cta": 'One clear CTA ("Shop now" / "DM us" / "Link in bio")',
        "hashtags": "20-30 niche + broad hashtags in first comment",
        "char_limit": 2200,
    },
    "facebook": {
        "hook": "Question or bold statement to stop the scroll",
        "body": "Short paragraphs (2-3 lines), white space heavy, bullet points welcome",
        "cta": "Button CTA + inline text CTA",
        "char_limit": 500,  # practical optimal, not technical max
    },
    "linkedin": {
        "hook": "Professional insight or thought-leadership opener (no emojis in first line)",
        "body": "Data-driven, value-packed 3-5 paragraphs, story arc",
        "cta": "Comment/share/connect CTA",
        "char_limit": 3000,
    },
    "tiktok": {
        "hook": "First 3 seconds hook (text overlay, not caption)",
        "body": "Ultra-short: 1-2 punchy lines max",
        "cta": "Comment or duet CTA",
        "char_limit": 150,
    },
    "twitter_x": {
        "hook": "Lead with the value immediately",
        "body": "1-2 sentences or a tight thread hook",
        "cta": "Reply/RT CTA",
        "char_limit": 280,
    },
    "google_ads": {
        "headline_chars": 30,
        "description_chars": 90,
        "headlines_count": 15,
        "descriptions_count": 4,
    },
}

# ---------------------------------------------------------------------------
# Image style variant descriptors (one per variant slot)
# ---------------------------------------------------------------------------
IMAGE_STYLE_VARIANTS: List[str] = [
    "Photorealistic, DSLR quality, natural lighting, lifestyle context",
    "Studio editorial, dramatic lighting, clean background, product hero shot",
    "Cinematic widescreen, golden hour light, aspirational mood",
    "Minimalist flat lay, overhead shot, pristine white or marble surface",
]

# ---------------------------------------------------------------------------
# Video shot structure templates (mapped to angle index)
# ---------------------------------------------------------------------------
VIDEO_SHOT_STRUCTURES: List[str] = [
    "Opening: tight product close-up (0-2s) → reveal lifestyle context (2-5s) → CTA screen (5-6s)",
    "Opening: person in context (0-2s) → product in use (2-4s) → brand closeup (4-6s)",
    "Opening: ambient scene (0-1s) → dramatic reveal (1-4s) → text overlay CTA (4-6s)",
]

# Copy variant angle frameworks – drive creative diversity across 4 variants
_COPY_ANGLE_FRAMEWORKS: List[str] = [
    "Emotional storytelling angle: lead with a relatable pain point, pivot to the product as the hero solution, close with an aspirational outcome.",
    "Social proof angle: open with a surprising statistic or customer voice, build credibility in the body, CTA anchored to trust.",
    "Feature-benefit angle: list three concrete product features, each immediately followed by the customer benefit it delivers.",
    "Urgency/scarcity angle: open with a time-bound or limited-availability hook, body builds FOMO, CTA demands immediate action.",
]

# Number of variants to produce per creative type
NUM_VARIANTS = 4


# ---------------------------------------------------------------------------
# Utility helpers (original)
# ---------------------------------------------------------------------------

def top_keywords(text: str, n: int = 15) -> List[str]:
    """Extract the top-N meaningful keywords from a body of text.

    Args:
        text: Raw text to analyse.
        n: Maximum number of keywords to return.

    Returns:
        List of keyword strings ordered by frequency descending.
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", (text or "").lower())
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [w for w, _ in counts.most_common(n)]


def asset_summary(scraped: dict) -> str:
    """Produce a human-readable summary of downloaded reference assets.

    Args:
        scraped: Scraped reference data dict containing an 'assets' list.

    Returns:
        Multi-line string with up to 20 asset signal descriptions.
    """
    rows = []
    for a in (scraped or {}).get("assets", []):
        if a.get("error"):
            continue
        rows.append(f"{a.get('type', 'asset')}: {a.get('signals', {})}")
    return "\n".join(rows[:20])


def _mood_from_settings(settings: dict) -> str:
    """Extract mood string from nested or flat settings dict.

    Args:
        settings: Campaign settings dict (may nest mood under copy/image/video keys).

    Returns:
        Mood string, or empty string if not specified.
    """
    settings = settings or {}
    for key in ("copy", "image", "video"):
        mood = (settings.get(key) or {}).get("mood")
        if mood:
            return str(mood).strip()
    return str(settings.get("mood") or "").strip()


def _media_specs(settings: dict) -> str:
    """Serialise media specs (size, duration, aspect ratio, resolution) to a line.

    Args:
        settings: Campaign settings dict.

    Returns:
        Semicolon-separated spec string, or empty string.
    """
    settings = settings or {}
    parts = []
    image = settings.get("image") or {}
    video = settings.get("video") or {}
    size = image.get("image_size")
    if size:
        parts.append(f"image size {size}")
    if video.get("duration"):
        parts.append(f"duration {video['duration']}s")
    if video.get("aspect_ratio"):
        parts.append(f"aspect ratio {video['aspect_ratio']}")
    if video.get("resolution"):
        parts.append(f"resolution {video['resolution']}")
    return "; ".join(parts)


def analyze_visual_references(scraped: dict) -> str:
    """Run vision-LLM analysis on up to 3 reference images/frames.

    Args:
        scraped: Scraped reference data containing 'assets' with image paths.

    Returns:
        Text summary of palette, composition, lighting, and stylistic cues,
        or empty string if analysis is unavailable.
    """
    paths = []
    for a in (scraped or {}).get("assets", []):
        if a.get("type") == "image" and a.get("path"):
            paths.append(config.ROOT / a["path"])
        for fp in a.get("frame_paths", []) or []:
            paths.append(config.ROOT / fp)
    paths = [p for p in paths if p.exists()][:3]
    if not paths or not config.OPENROUTER_API_KEY:
        return ""
    try:
        return chat(
            """Analyze these key visual references for advertising production. Summarize:
1. Palette and color temperature
2. Composition, lighting, and mood
3. Texture, materials, and stylistic cues
Keep concise and actionable for creative synthesis.""",
            config.OPENROUTER_VISION_MODEL,
            paths,
        )
    except Exception as exc:
        logger.warning("Visual reference analysis failed: %s", exc)
        return f"Visual reference summary unavailable: {exc}"


# ---------------------------------------------------------------------------
# Brand DNA helper (safe import – brand_manager may not exist yet)
# ---------------------------------------------------------------------------

def _brand_dna_block(brand_dna: Optional[dict]) -> str:
    """Convert brand_dna dict to a formatted prompt block.

    Attempts to use brand_manager.brand_dna_to_prompt_block when available;
    falls back to a minimal inline serialisation if the module is absent.

    Args:
        brand_dna: Brand DNA dictionary or None.

    Returns:
        Formatted brand DNA string for injection into prompts.
    """
    if not brand_dna:
        return ""
    try:
        from .brand_manager import brand_dna_to_prompt_block  # type: ignore
        return brand_dna_to_prompt_block(brand_dna)
    except ImportError:
        logger.debug("brand_manager not available; using inline brand DNA serialisation.")
        lines = ["BRAND DNA:"]
        for key, value in brand_dna.items():
            if value:
                lines.append(f"  {key.replace('_', ' ').title()}: {value}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# NEW: Platform copy prompt builder
# ---------------------------------------------------------------------------

def build_platform_copy_prompt(
    base_context: str,
    platform: str,
    brand_dna: Optional[dict] = None,
) -> str:
    """Build a complete, platform-optimised copy-generation prompt.

    Uses PLATFORM_COPY_FORMATS to inject platform-specific structural
    rules, character limits, hook strategy, and CTA conventions.

    Args:
        base_context: The shared creative context block (brief, DNA, signals).
        platform: Target platform key matching PLATFORM_COPY_FORMATS.
        brand_dna: Optional brand DNA dict; injected as a formatting constraint.

    Returns:
        Complete LLM instruction string ready for chat() consumption.
    """
    fmt = PLATFORM_COPY_FORMATS.get(platform.lower())
    if not fmt:
        logger.warning("Unknown platform '%s'; returning generic copy prompt.", platform)
        return (
            f"{base_context}\n\nWrite professional advertising copy for {platform}. "
            "Include a hook, benefit-driven body, and a clear CTA."
        )

    brand_block = _brand_dna_block(brand_dna)
    brand_section = f"\n{brand_block}\n" if brand_block else ""

    # Google Ads has a unique spec-driven format
    if platform.lower() == "google_ads":
        return (
            f"{base_context}{brand_section}\n"
            "PLATFORM: Google Ads (Responsive Search Ad)\n"
            f"Write {fmt['headlines_count']} unique headlines "
            f"(≤{fmt['headline_chars']} chars each) and "
            f"{fmt['descriptions_count']} descriptions "
            f"(≤{fmt['description_chars']} chars each).\n"
            "Rules:\n"
            "- Headlines: keyword-rich, benefit-first, avoid repetition across slots\n"
            "- Descriptions: expand benefits, include social proof or urgency, end with CTA\n"
            "- No punctuation at end of headlines\n"
            "- Do NOT include placeholder text or angle brackets\n"
            "Return ONLY the headlines (labelled H1–H15) then descriptions (D1–D4)."
        )

    char_limit = fmt.get("char_limit", 2200)
    hook_rule = fmt.get("hook", "")
    body_rule = fmt.get("body", "")
    cta_rule = fmt.get("cta", "")
    hashtag_rule = fmt.get("hashtags", "")

    hashtag_section = (
        f"\nHASHTAG STRATEGY: {hashtag_rule}" if hashtag_rule else ""
    )

    return (
        f"{base_context}{brand_section}\n"
        f"PLATFORM: {platform.upper()}\n"
        f"CHARACTER LIMIT: ≤{char_limit} characters\n\n"
        "STRUCTURAL RULES:\n"
        f"  HOOK    → {hook_rule}\n"
        f"  BODY    → {body_rule}\n"
        f"  CTA     → {cta_rule}"
        f"{hashtag_section}\n\n"
        "COPY QUALITY STANDARDS:\n"
        "- Every sentence must earn its place; cut filler words ruthlessly\n"
        "- Active voice, present tense wherever possible\n"
        "- Brand voice must remain consistent throughout\n"
        "- Never make unsubstantiated superlative claims (best, #1) without proof\n\n"
        f"Write one complete, publish-ready {platform.upper()} caption/ad copy "
        "that strictly follows the structural rules above. "
        "Output ONLY the copy text — no labels, no explanations."
    )


# ---------------------------------------------------------------------------
# NEW: Storyboard video prompt builder
# ---------------------------------------------------------------------------

def build_storyboard_video_prompt(
    scraped: dict,
    brief: str,
    settings: dict,
    brand_dna: Optional[dict] = None,
) -> str:
    """Build a shot-by-shot storyboard video prompt for production pipelines.

    Selects a VIDEO_SHOT_STRUCTURE template based on settings and enriches it
    with motion type, subject action, brand moment, and CTA frame specifics.

    Args:
        scraped: Scraped reference data dict.
        brief: Campaign creative brief.
        settings: Campaign settings dict (duration, aspect_ratio, resolution, mood).
        brand_dna: Optional brand DNA for voice/style constraints.

    Returns:
        Detailed storyboard video prompt string.
    """
    settings = settings or {}
    video_cfg = settings.get("video") or {}
    mood = _mood_from_settings(settings)
    specs = _media_specs(settings)
    brand_block = _brand_dna_block(brand_dna)
    kws = top_keywords(scraped.get("text", ""))

    duration = video_cfg.get("duration", config.VIDEO_SECONDS)
    aspect = video_cfg.get("aspect_ratio", config.OPENROUTER_VIDEO_RATIO)
    resolution = video_cfg.get("resolution", config.OPENROUTER_VIDEO_RESOLUTION)

    # Select shot structure: use settings-based index or default to first
    structure_idx = int(settings.get("storyboard_variant", 0)) % len(VIDEO_SHOT_STRUCTURES)
    shot_structure = VIDEO_SHOT_STRUCTURES[structure_idx]

    motion_options = ["smooth dolly-in", "slow pan right", "static locked-off"]
    motion_type = motion_options[structure_idx % len(motion_options)]

    brand_moment = (
        f"Brand identity moment: {brand_dna.get('brand_name', 'brand')} "
        f"logo/watermark appears at {max(1, duration - 2)}s, "
        f"colour palette anchored to {brand_dna.get('primary_color', 'brand colours')}."
        if brand_dna
        else "Brand identity moment: subtle logo reveal in final 2 seconds."
    )

    prompt = (
        f"PRODUCTION VIDEO PROMPT — STORYBOARD STRUCTURE\n"
        f"{'=' * 60}\n\n"
        f"BRIEF: {brief}\n\n"
        f"TECHNICAL SPECS:\n"
        f"  Duration     : {duration}s\n"
        f"  Aspect Ratio : {aspect}\n"
        f"  Resolution   : {resolution}\n"
        f"  Mood/Tone    : {mood or 'professional and aspirational'}\n\n"
        f"REFERENCE SIGNALS: {', '.join(kws) or 'none'}\n"
        f"REFERENCE TITLE  : {scraped.get('title') or 'Unknown'}\n\n"
        f"SHOT STRUCTURE:\n  {shot_structure}\n\n"
        f"MOTION DIRECTION:\n"
        f"  Primary camera movement: {motion_type}\n"
        f"  Transition style       : smooth dissolve or jump-cut (match energy to brief)\n\n"
        f"SHOT-BY-SHOT DETAIL:\n"
        f"  Frame 1 — Opening ({0}s–{min(2, duration)}s):\n"
        f"    • Subject action  : [hero product or person in brand-relevant action]\n"
        f"    • Camera position : tight close-up or establishing wide, {motion_type}\n"
        f"    • Lighting        : {mood or 'natural'} — ensure subject is hero-lit\n\n"
        f"  Frame 2 — Core ({min(2, duration)}s–{min(4, duration)}s):\n"
        f"    • Subject action  : demonstrate product benefit or lifestyle context\n"
        f"    • Camera position : medium shot, subtle push-in\n"
        f"    • Text overlay    : optional benefit phrase (≤5 words, high contrast)\n\n"
        f"  Frame 3 — Brand Moment ({min(4, duration)}s–{duration}s):\n"
        f"    • {brand_moment}\n"
        f"    • CTA frame       : clean background, brand colour dominant,\n"
        f"                        CTA text centred (e.g. 'Shop Now' / 'Discover More')\n\n"
        f"NEGATIVE CONSTRAINTS:\n"
        f"  - No jump cuts in first 1s (must hook immediately)\n"
        f"  - No blurry frames, motion artifacts, or temporal flickering\n"
        f"  - Preserve brand colour palette throughout\n"
        f"  - Anatomy and physics must remain plausible at all frames\n"
        f"  - No unintended watermarks or malformed text\n"
        f"  - Keep subject fully within safe-frame margins\n\n"
        f"SPECS SUMMARY: {specs or 'use defaults above'}\n"
    )

    if brand_block:
        prompt += f"\n{brand_block}\n"

    return prompt[: config.MAX_PROMPT_CHARS]


# ---------------------------------------------------------------------------
# NEW: Image variant builder
# ---------------------------------------------------------------------------

def build_image_variants(
    base_context: str,
    brief: str,
    refs_summary: str,
    brand_dna: Optional[dict] = None,
) -> List[str]:
    """Generate 4 image prompts, each using a distinct IMAGE_STYLE_VARIANTS entry.

    Each prompt specifies: subject, composition, lighting, color palette,
    mood, brand constraints, and negative constraints.

    Args:
        base_context: Shared creative context block.
        brief: Campaign creative brief.
        refs_summary: Visual reference analysis summary.
        brand_dna: Optional brand DNA dict for colour/style anchoring.

    Returns:
        List of 4 complete image-generation prompt strings.
    """
    brand_block = _brand_dna_block(brand_dna)
    color_palette = (
        brand_dna.get("primary_color", "brand-consistent palette")
        if brand_dna
        else "brand-consistent palette"
    )
    brand_name = (brand_dna or {}).get("brand_name", "the brand")

    variants: List[str] = []
    for idx, style in enumerate(IMAGE_STYLE_VARIANTS):
        variant_prompt = (
            f"IMAGE VARIANT {idx + 1} OF {len(IMAGE_STYLE_VARIANTS)}\n"
            f"STYLE FRAMEWORK: {style}\n\n"
            f"BRIEF: {brief}\n\n"
            f"SUBJECT & COMPOSITION:\n"
            f"  - Hero subject prominently placed using rule-of-thirds or centred composition\n"
            f"  - Depth of field appropriate to style: "
            f"{'shallow bokeh' if idx in (0, 1) else 'deep field'}\n"
            f"  - Negative space intentional; no cluttered backgrounds\n\n"
            f"LIGHTING:\n"
            f"  - {style.split(',')[1].strip() if ',' in style else 'Natural, flattering light'}\n"
            f"  - Shadows soft and directional; no harsh overexposure\n\n"
            f"COLOR PALETTE:\n"
            f"  - Anchor: {color_palette}\n"
            f"  - Complementary tones that reinforce the brand identity of {brand_name}\n\n"
            f"MOOD & ATMOSPHERE:\n"
            f"  - {style.split(',')[-1].strip()}\n"
            f"  - Emotional register: aspirational yet authentic\n\n"
            f"REFERENCE CONTEXT:\n{refs_summary or 'No visual references; infer from brief.'}\n\n"
            f"TECHNICAL QUALITY:\n"
            f"  - 4K-equivalent sharpness, accurate perspective and scale\n"
            f"  - No lens distortion unless stylistically intentional\n"
            f"  - Physically plausible materials and anatomy\n\n"
            f"NEGATIVE CONSTRAINTS:\n"
            f"  - No watermarks, logos (unless brief specifies), or malformed text\n"
            f"  - No oversaturated or neon-washed colour grading\n"
            f"  - No AI artefacts (extra limbs, floating objects, texture repeats)\n"
            f"  - No copyright-protected brand identifiers from third parties\n\n"
            f"SHARED CONTEXT:\n{base_context}"
        )
        if brand_block:
            variant_prompt += f"\n\n{brand_block}"
        variants.append(variant_prompt[: config.MAX_PROMPT_CHARS])
        logger.debug("Built image variant %d/%d.", idx + 1, len(IMAGE_STYLE_VARIANTS))

    return variants


# ---------------------------------------------------------------------------
# NEW: Multi-variant prompt builder
# ---------------------------------------------------------------------------

def build_multi_variant_prompts(
    scraped: dict,
    brief: str,
    creative_types: List[str],
    settings: Optional[dict] = None,
    brand_dna: Optional[dict] = None,
    platforms: Optional[List[str]] = None,
) -> Dict[str, list]:
    """Generate 4 creative variants per requested type plus platform-specific copy.

    Variants are produced by rotating through different angle/style frameworks
    so each output has a genuinely different creative strategy.

    Args:
        scraped: Scraped reference data dict.
        brief: Campaign creative brief.
        creative_types: List of creative types, e.g. ['copy', 'image', 'video'].
        settings: Optional campaign settings dict.
        brand_dna: Optional brand DNA dict for consistent brand injection.
        platforms: Optional list of platforms for platform_copy variants
            (defaults to all PLATFORM_COPY_FORMATS keys).

    Returns:
        Dict with keys matching each creative_type (list of N prompt strings),
        plus 'platform_copy' (dict mapping platform → prompt string).

    Example return shape::

        {
            'copy':  [prompt_v1, prompt_v2, prompt_v3, prompt_v4],
            'image': [prompt_v1, prompt_v2, prompt_v3, prompt_v4],
            'video': [prompt_v1, prompt_v2, prompt_v3, prompt_v4],
            'platform_copy': {
                'instagram': '...',
                'linkedin': '...',
                ...
            },
        }
    """
    settings = settings or {}
    scraped = scraped or {}
    platforms = platforms or list(PLATFORM_COPY_FORMATS.keys())

    logger.info(
        "Building multi-variant prompts: types=%s, platforms=%s",
        creative_types,
        platforms,
    )

    # --- Shared context assembly ---
    visual = analyze_visual_references(scraped)
    dna = build_dna(scraped, brief)
    vision_dna = enrich_with_vision(dna, scraped)
    kws = top_keywords(scraped.get("text", ""))
    mood = _mood_from_settings(settings)
    specs = _media_specs(settings)
    brand_block = _brand_dna_block(brand_dna)

    mood_line = f"\nVISUAL MOOD: {mood}" if mood else ""
    specs_line = f"\nMEDIA SPECS: {specs}" if specs else ""
    brand_section = f"\n{brand_block}" if brand_block else ""

    base_context = (
        f"BRIEF: {brief}{mood_line}{specs_line}\n\n"
        f"REFERENCE TITLE: {scraped.get('title') or 'Unknown'}\n"
        f"REFERENCE TEXT SIGNALS: {', '.join(kws) or 'none'}\n"
        f"ASSET SUMMARY:\n{asset_summary(scraped) or 'No downloadable visual assets'}\n"
        f"VISUAL REFERENCE ANALYSIS:\n"
        f"{visual or 'Use structural signals from available reference assets.'}\n"
        f"CREATIVE DNA:\n{dna}\n"
        f"VISION CREATIVE DIRECTION:\n{vision_dna or 'No additional vision enrichment available.'}"
        f"{brand_section}"
    )

    result: Dict[str, list] = {}

    for ctype in creative_types:
        learned = get_context(ctype)
        variants: List[str] = []

        if ctype == "image":
            variants = build_image_variants(base_context, brief, visual or "", brand_dna)

        elif ctype == "video":
            # Generate NUM_VARIANTS video prompts by rotating storyboard index
            for idx in range(NUM_VARIANTS):
                patched_settings = dict(settings)
                patched_settings["storyboard_variant"] = idx
                vp = build_storyboard_video_prompt(scraped, brief, patched_settings, brand_dna)
                variants.append(vp)

        else:
            # Copy or any custom type — rotate through angle frameworks
            for idx, angle_framework in enumerate(_COPY_ANGLE_FRAMEWORKS):
                variant_prompt = (
                    f"COPY VARIANT {idx + 1} OF {NUM_VARIANTS}\n"
                    f"ANGLE FRAMEWORK: {angle_framework}\n\n"
                    f"{base_context}\n\n"
                    "CREATIVE TYPE: copy\n"
                    "Return a professional copy prompt covering audience, tone, "
                    "structure, CTA, claims and factual constraints.\n"
                    f"{learned}"
                )
                if mood:
                    variant_prompt += f"\nMatch this visual/tonal mood: {mood}."
                variants.append(variant_prompt[: config.MAX_PROMPT_CHARS])

        result[ctype] = variants
        logger.info("Generated %d variants for creative type '%s'.", len(variants), ctype)

    # --- Platform copy variants ---
    platform_copy: Dict[str, str] = {}
    for platform in platforms:
        try:
            platform_copy[platform] = build_platform_copy_prompt(
                base_context, platform, brand_dna
            )
            logger.debug("Built platform copy prompt for '%s'.", platform)
        except Exception as exc:
            logger.error("Failed to build platform copy prompt for '%s': %s", platform, exc)
            platform_copy[platform] = f"Platform copy generation failed: {exc}"

    result["platform_copy"] = [platform_copy]  # wrap in list for uniform shape

    return result


# ---------------------------------------------------------------------------
# ORIGINAL (enhanced): build_prompts
# ---------------------------------------------------------------------------

def build_prompts(
    scraped: dict,
    brief: str,
    creative_types: List[str],
    settings: Optional[dict] = None,
) -> dict:
    """Build production-ready prompts for each requested creative type.

    Preserves all original behaviour while additionally calling
    build_multi_variant_prompts and embedding the result under a
    'variants' key in the returned dict.

    Args:
        scraped: Scraped reference data dict.
        brief: Campaign creative brief.
        creative_types: List of creative types, e.g. ['copy', 'image', 'video'].
        settings: Optional campaign settings dict.

    Returns:
        Dict containing:
            keywords, mood, visual_analysis, creative_dna,
            vision_creative_direction, prompts, asset_count, variants.
    """
    settings = settings or {}
    scraped = scraped or {}

    logger.info("build_prompts called for types: %s", creative_types)

    visual = analyze_visual_references(scraped)
    dna = build_dna(scraped, brief)
    vision_dna = enrich_with_vision(dna, scraped)
    kws = top_keywords(scraped.get("text", ""))
    mood = _mood_from_settings(settings)
    specs = _media_specs(settings)

    mood_line = f"\nVISUAL MOOD: {mood}" if mood else ""
    specs_line = f"\nMEDIA SPECS: {specs}" if specs else ""

    common = (
        f"Create production-ready {', '.join(creative_types)} for this brief:\n"
        f"{brief}{mood_line}{specs_line}\n\n"
        f"REFERENCE TITLE: {scraped.get('title') or 'Unknown'}\n"
        f"REFERENCE TEXT SIGNALS: {', '.join(kws) or 'none'}\n"
        f"ASSET SUMMARY:\n{asset_summary(scraped) or 'No downloadable visual assets'}\n"
        f"VISUAL REFERENCE ANALYSIS:\n"
        f"{visual or 'Use structural signals from available reference assets.'}\n"
        f"CREATIVE DNA:\n{dna}\n"
        f"VISION CREATIVE DIRECTION:\n{vision_dna or 'No additional vision enrichment available.'}\n\n"
        "QUALITY CONTRACT:\n"
        "- professional, realistic, coherent and physically plausible\n"
        "- preserve important reference composition/style cues without copying "
        "protected logos/text unless explicitly requested\n"
        "- accurate perspective, scale, lighting, materials and anatomy\n"
        "- no random watermark or malformed text\n"
        "- satisfy every explicit constraint in the brief\n"
    )

    prompts: Dict[str, str] = {}
    for c in creative_types:
        learned = get_context(c)
        if c == "image":
            extra = (
                "Return one detailed image-generation prompt covering subject, "
                "composition, camera/lens, lighting, environment, materials, color, "
                "mood, negative constraints and reference-image guidance."
            )
            if specs:
                extra += f" Honor these media specs: {specs}."
        elif c == "video":
            extra = (
                "Return a production video prompt covering duration, aspect ratio, "
                "shot structure, camera movement, subject motion, temporal continuity, "
                "lighting, reference-frame usage, safety/frame containment and "
                "negative constraints."
            )
            if specs:
                extra += f" Honor these media specs: {specs}."
        else:
            extra = (
                "Return a professional copy prompt covering audience, tone, "
                "structure, CTA, claims and factual constraints."
            )
            if mood:
                extra += f" Match this visual/tonal mood: {mood}."
        prompts[c] = (common + "\nCREATIVE TYPE: " + c + "\n" + extra + "\n" + learned)[
            : config.MAX_PROMPT_CHARS
        ]

    # --- Multi-variant generation ---
    try:
        variants = build_multi_variant_prompts(
            scraped=scraped,
            brief=brief,
            creative_types=creative_types,
            settings=settings,
        )
        logger.info("Multi-variant prompts generated successfully.")
    except Exception as exc:
        logger.error("Multi-variant prompt generation failed: %s", exc, exc_info=True)
        variants = {}

    return {
        "keywords": kws,
        "mood": mood,
        "visual_analysis": visual,
        "creative_dna": dna,
        "vision_creative_direction": vision_dna,
        "prompts": prompts,
        "asset_count": len(scraped.get("assets") or []),
        "variants": variants,
    }


# ---------------------------------------------------------------------------
# ORIGINAL: refine_with_llm
# ---------------------------------------------------------------------------

def refine_with_llm(
    prompt: str,
    feedback: str,
    ctype: str = "image",
    timeout: int = 45,
) -> str:
    """Refine an existing prompt using reviewer feedback via LLM.

    Preserves the original brief and all constraints while precisely
    applying reviewer corrections and improving model-specific specificity.

    Args:
        prompt: The original production prompt to be refined.
        feedback: Reviewer corrections or notes.
        ctype: Creative type ('image', 'video', or 'copy').
        timeout: LLM request timeout in seconds.

    Returns:
        Refined prompt string, truncated to MAX_PROMPT_CHARS.
        Falls back to appending feedback inline if LLM call fails.
    """
    if not (feedback or "").strip():
        return prompt
    inst = (
        "You are a senior creative director. Rewrite ONLY the production prompt. "
        "Preserve the original brief and all constraints. Apply reviewer corrections "
        "precisely, then improve realism, composition, continuity and model-specific "
        f"specificity.\nTYPE: {ctype}\nPROMPT:\n{prompt}\nREVIEW:\n{feedback}\n"
        f"{get_context(ctype)}"
    )
    try:
        x = chat(inst, timeout=timeout)
        if x:
            logger.info("LLM refinement succeeded for ctype='%s'.", ctype)
            return x[: config.MAX_PROMPT_CHARS]
    except Exception as exc:
        logger.warning("LLM refinement failed for ctype='%s': %s", ctype, exc)
    return (prompt + "\nMANDATORY REVIEW CORRECTIONS:\n" + feedback).strip()[
        : config.MAX_PROMPT_CHARS
    ]


# ---------------------------------------------------------------------------
# ORIGINAL: refine_many
# ---------------------------------------------------------------------------

def refine_many(items: list, timeout: int = 45) -> List[str]:
    """Refine (prompt, feedback, ctype) triples in parallel.

    Uses a ThreadPoolExecutor to concurrently refine multiple prompts,
    respecting a maximum of 6 parallel workers.

    Args:
        items: List of (prompt, feedback, ctype) tuples.
        timeout: Per-request LLM timeout in seconds.

    Returns:
        List of refined prompt strings in input order.
    """
    if not items:
        return []
    logger.info("refine_many called with %d items.", len(items))
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
        return list(
            pool.map(
                lambda it: refine_with_llm(it[0], it[1], it[2], timeout=timeout),
                items,
            )
        )
