"""
Brand DNA Manager.

Extracts, stores and retrieves a structured brand profile from reference
assets. The profile is used to lock visual/tonal consistency across all
generated creatives in a campaign.

Brand DNA covers:
  - Palette: dominant colors + hex approximations
  - Lighting: dark/bright/balanced
  - Composition: portrait/landscape/square
  - Tone of voice: adjectives inferred from text signals
  - Typography style: from reference visual analysis
  - Do/Don't rules: inferred from reference quality
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .creative_intelligence import build_dna, enrich_with_vision

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants – keyword → tone adjective mapping
# ---------------------------------------------------------------------------

#: Maps keywords (lower-cased) found in brief/scraped text to tone adjectives.
#: Add entries freely; evaluation is done as substring matching.
KEYWORD_TONE_MAP: dict[str, str] = {
    # Premium / aspiration
    "luxury":      "premium",
    "exclusive":   "exclusive",
    "prestige":    "prestigious",
    "high-end":    "sophisticated",
    "bespoke":     "bespoke",
    "artisan":     "artisanal",
    # Urgency / promotion
    "sale":        "urgent",
    "offer":       "promotional",
    "discount":    "value-driven",
    "limited":     "scarce",
    "flash":       "time-sensitive",
    "deal":        "deal-focused",
    # Trust / authority
    "award":       "authoritative",
    "certified":   "trustworthy",
    "guarantee":   "reliable",
    "secure":      "trustworthy",
    "proven":      "evidence-based",
    "expert":      "expert",
    # Innovation / forward-thinking
    "innovation":  "forward-thinking",
    "cutting-edge": "innovative",
    "smart":       "intelligent",
    "ai":          "tech-forward",
    "digital":     "digital-first",
    # Sustainability / values
    "sustainable": "eco-conscious",
    "green":       "environmentally-aware",
    "ethical":     "values-led",
    "organic":     "natural",
    # Community / inclusivity
    "community":   "inclusive",
    "together":    "collaborative",
    "diversity":   "diverse",
    # Energy / lifestyle
    "fast":        "energetic",
    "bold":        "bold",
    "powerful":    "powerful",
    "fun":         "playful",
    "adventure":   "adventurous",
    "lifestyle":   "lifestyle-oriented",
    # Warmth / personal
    "care":        "caring",
    "wellness":    "wellness-focused",
    "family":      "family-friendly",
    "personal":    "personable",
    # Minimalism / elegance
    "minimal":     "minimalist",
    "clean":       "clean",
    "simple":      "simple",
    "elegant":     "elegant",
}

#: Default do/don't rules when none can be inferred from references.
_DEFAULT_DO_RULES: list[str] = [
    "show product in a lifestyle or real-world context",
    "use negative space for visual breathing room",
    "keep typography legible at small sizes",
    "maintain consistent brand color palette",
]
_DEFAULT_DONT_RULES: list[str] = [
    "use busy or cluttered backgrounds",
    "mix more than two font families",
    "use low-resolution or pixelated assets",
    "place text over busy image areas without contrast",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_brand_dna(scraped: dict[str, Any], brief: str = "") -> dict[str, Any]:
    """
    Extract a structured brand DNA profile from reference assets and brief.

    Orchestration
    -------------
    1. Calls :func:`creative_intelligence.build_dna` to derive structural
       signals from scraped colour / typography / composition data.
    2. Calls :func:`creative_intelligence.enrich_with_vision` to layer AI
       vision analysis (object recognition, mood, contrast ratios) on top.
    3. Infers tone-of-voice adjectives from the brief + scraped text via
       :func:`infer_tone`.
    4. Assembles and returns a canonical brand DNA dict.

    Parameters
    ----------
    scraped : dict
        Scraped reference data typically structured as::

            {
                "images":  [list of image paths or URLs],
                "text":    "aggregated text from brand pages",
                "colors":  [list of hex strings or RGB tuples],
                "fonts":   [list of font names],
                "urls":    [list of source URLs],
            }

        Any keys may be absent; the function degrades gracefully.
    brief : str, optional
        Creative brief text used to enrich tone-of-voice inference.

    Returns
    -------
    dict
        Structured brand DNA with the following keys:

        ``palette`` (list[str])
            Human-readable colour descriptions, e.g.
            ``["rich jewel tones", "deep navy backgrounds", "gold accents"]``.
        ``lighting`` (str)
            One of ``"dark/dramatic"``, ``"bright/airy"``, ``"balanced/neutral"``,
            ``"cinematic"``, or ``"unknown"``.
        ``composition`` (str)
            One of ``"portrait-first"``, ``"landscape-first"``,
            ``"square-centered"``, or ``"mixed"``.
        ``tone`` (list[str])
            Tone-of-voice adjectives, e.g. ``["premium", "aspirational"]``.
        ``keywords`` (list[str])
            Distilled brand keywords extracted from text.
        ``typography`` (str)
            Typography style descriptor, e.g. ``"serif editorial"``.
        ``do_rules`` (list[str])
            Visual / copy dos for creatives.
        ``dont_rules`` (list[str])
            Visual / copy don'ts for creatives.
    """
    logger.info("extract_brand_dna: starting extraction | brief_len=%d | scraped_keys=%s",
                len(brief), list(scraped.keys()))

    # ── Step 1: Structural DNA (colors, fonts, composition) ───────────────
    structural: dict[str, Any] = {}
    try:
        structural = build_dna(scraped, brief=brief) or {}
        logger.info("extract_brand_dna: build_dna returned keys=%s", list(structural.keys()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_brand_dna: build_dna failed (%s) – using empty structural", exc)

    # ── Step 2: AI Vision enrichment (mood, objects, contrast) ───────────
    enriched: dict[str, Any] = {}
    images: list[Any] = scraped.get("images", [])
    if images:
        try:
            enriched = enrich_with_vision(dna=structural, scraped=scraped) or {}
            logger.info("extract_brand_dna: enrich_with_vision returned keys=%s", list(enriched.keys()))
        except Exception as exc:  # noqa: BLE001
            logger.warning("extract_brand_dna: enrich_with_vision failed (%s) – skipping enrichment", exc)
    else:
        logger.debug("extract_brand_dna: no images found in scraped data – skipping vision enrichment")

    # ── Step 3: Tone of voice ─────────────────────────────────────────────
    scraped_text: str = scraped.get("text", "")
    tone: list[str] = infer_tone(text=scraped_text, brief=brief)

    # Merge any tone from structural/enriched layers
    for source in (structural, enriched):
        extra_tone: Any = source.get("tone", [])
        if isinstance(extra_tone, list):
            for adj in extra_tone:
                if isinstance(adj, str) and adj not in tone:
                    tone.append(adj)
        elif isinstance(extra_tone, str) and extra_tone and extra_tone not in tone:
            tone.append(extra_tone)

    # ── Step 4: Assemble DNA dict ─────────────────────────────────────────
    palette: list[str] = (
        enriched.get("palette")
        or structural.get("palette")
        or _extract_palette_from_scraped(scraped)
    )

    visual_dna = structural.get("visual_dna", {})

    lighting: str = (
        enriched.get("lighting")
        or visual_dna.get("lighting")
        or structural.get("lighting")
        or "balanced/neutral"
    )

    composition: str = (
        enriched.get("composition")
        or visual_dna.get("composition")
        or structural.get("composition")
        or "mixed"
    )

    typography: str = (
        enriched.get("typography")
        or structural.get("typography")
        or "sans-serif modern"
    )

    keywords: list[str] = _extract_keywords(text=scraped_text, brief=brief)

    do_rules: list[str] = (
        enriched.get("do_rules")
        or structural.get("do_rules")
        or list(_DEFAULT_DO_RULES)
    )

    dont_rules: list[str] = (
        enriched.get("dont_rules")
        or structural.get("dont_rules")
        or list(_DEFAULT_DONT_RULES)
    )

    brand_signals = structural.get("brand_signals", {
        "title": scraped.get("title") or "",
        "keywords": keywords,
    })

    dna: dict[str, Any] = {
        "brand_signals": brand_signals,
        "visual_dna": visual_dna or {"lighting": lighting, "composition": composition},
        "palette":     palette,
        "lighting":    lighting,
        "composition": composition,
        "tone":        tone if tone else ["neutral"],
        "keywords":    keywords,
        "typography":  typography,
        "do_rules":    do_rules,
        "dont_rules":  dont_rules,
    }

    logger.info(
        "extract_brand_dna: complete | tone=%s | palette=%s | composition=%s | lighting=%s",
        tone,
        palette,
        composition,
        lighting,
    )
    return dna


def brand_dna_to_prompt_block(dna: dict[str, Any]) -> str:
    """
    Convert a brand DNA dict into a formatted text block for prompt injection.

    The output is designed to be appended to any generation prompt so that
    the model respects brand consistency guidelines.

    Parameters
    ----------
    dna : dict
        Brand DNA dict as returned by :func:`extract_brand_dna`.

    Returns
    -------
    str
        A multi-line prompt block, for example::

            BRAND DNA:
            - Palette: rich jewel tones, deep navy backgrounds, gold accents
            - Lighting: dramatic cinematic with strong rim-light
            - Composition: portrait-first, subject centered
            - Tone: premium, aspirational, confident
            - Typography: serif editorial
            - DO: show product in lifestyle context, use negative space
            - DON'T: busy backgrounds, amateur lighting, clipart-style graphics

    Notes
    -----
    Missing or empty keys are gracefully omitted from the output.

    Examples
    --------
    >>> dna = extract_brand_dna(scraped={}, brief="luxury watches")
    >>> block = brand_dna_to_prompt_block(dna)
    >>> print(block)
    BRAND DNA:
    - Palette: ...
    """
    if not dna:
        logger.debug("brand_dna_to_prompt_block: empty DNA – returning empty string")
        return ""

    lines: list[str] = ["BRAND DNA:"]

    # ── Palette ───────────────────────────────────────────────────────────
    palette = dna.get("palette", [])
    if palette:
        palette_str = ", ".join(palette) if isinstance(palette, list) else str(palette)
        lines.append(f"- Palette: {palette_str}")

    # ── Lighting ──────────────────────────────────────────────────────────
    lighting = dna.get("lighting") or dna.get("visual_dna", {}).get("lighting", "")
    if lighting:
        lines.append(f"- Lighting: {lighting}")

    # ── Composition ───────────────────────────────────────────────────────
    composition = dna.get("composition") or dna.get("visual_dna", {}).get("composition", "")
    if composition:
        lines.append(f"- Composition: {composition}")

    # ── Tone ──────────────────────────────────────────────────────────────
    tone = dna.get("tone", [])
    if tone:
        tone_str = ", ".join(tone) if isinstance(tone, list) else str(tone)
        lines.append(f"- Tone: {tone_str}")

    # ── Typography ────────────────────────────────────────────────────────
    typography = dna.get("typography", "")
    if typography:
        lines.append(f"- Typography: {typography}")

    # ── Do rules ─────────────────────────────────────────────────────────
    do_rules = dna.get("do_rules") or dna.get("guidelines", {}).get("do_rules", [])
    if do_rules:
        do_str = (
            "; ".join(do_rules) if isinstance(do_rules, list) else str(do_rules)
        )
        lines.append(f"- DO: {do_str}")

    # ── Don't rules ───────────────────────────────────────────────────────
    dont_rules = dna.get("dont_rules") or dna.get("guidelines", {}).get("dont_rules", [])
    if dont_rules:
        dont_str = (
            "; ".join(dont_rules) if isinstance(dont_rules, list) else str(dont_rules)
        )
        lines.append(f"- DON'T: {dont_str}")

    block = "\n".join(lines)
    logger.debug("brand_dna_to_prompt_block: generated block (%d chars)", len(block))
    return block


def infer_tone(text: str, brief: str = "") -> list[str]:
    """
    Infer a list of tone-of-voice adjectives from keyword analysis.

    Combines ``text`` (scraped brand copy) and ``brief`` (campaign brief)
    into a single corpus, lower-cases it, and matches against
    :data:`KEYWORD_TONE_MAP`.  Duplicate adjectives are deduplicated while
    preserving first-occurrence order.

    Parameters
    ----------
    text : str
        Aggregated text scraped from brand assets / landing pages.
    brief : str, optional
        Creative brief text.

    Returns
    -------
    list[str]
        Ordered, deduplicated list of tone adjectives, e.g.
        ``["premium", "exclusive", "eco-conscious"]``.
        Returns an empty list if no matches are found.

    Examples
    --------
    >>> infer_tone("Our luxury sustainable brand offers exclusive access", brief="")
    ['premium', 'eco-conscious', 'exclusive']
    """
    corpus = f"{text} {brief}".lower()
    seen: set[str] = set()
    adjectives: list[str] = []

    for keyword, adjective in KEYWORD_TONE_MAP.items():
        # Use word-boundary matching for short keywords to avoid false positives
        # e.g. "fast" shouldn't match "breakfast"
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, corpus) and adjective not in seen:
            adjectives.append(adjective)
            seen.add(adjective)
            logger.debug("infer_tone: matched keyword='%s' → adjective='%s'", keyword, adjective)

    logger.info("infer_tone: found %d adjectives: %s", len(adjectives), adjectives)
    return adjectives


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_palette_from_scraped(scraped: dict[str, Any]) -> list[str]:
    """
    Derive a human-readable palette description from raw scraped color data.

    Attempts to interpret ``scraped['colors']`` as a list of hex codes or
    RGB tuples and map them to descriptive names.  Falls back to a neutral
    default if no color data is present.

    Parameters
    ----------
    scraped : dict
        Raw scraped data; ``colors`` key is optional.

    Returns
    -------
    list[str]
        E.g. ``["deep navy", "warm gold", "off-white"]``.
        Returns ``["neutral tones"]`` when no usable color data exists.
    """
    colors: Any = scraped.get("colors", [])
    if not colors:
        return ["neutral tones"]

    descriptions: list[str] = []
    for color in colors[:5]:  # limit to top-5 dominant colours
        desc = _color_to_description(color)
        if desc and desc not in descriptions:
            descriptions.append(desc)

    return descriptions if descriptions else ["neutral tones"]


def _color_to_description(color: Any) -> str:
    """
    Map a single color value (hex string or RGB tuple) to a human-readable
    description using broad luminance/hue bucketing.

    Parameters
    ----------
    color : str or tuple
        Hex string like ``"#1a2b3c"`` or RGB tuple like ``(26, 43, 60)``.

    Returns
    -------
    str
        Descriptive name, e.g. ``"deep navy"``, ``"warm gold"``, ``"off-white"``.
        Returns ``""`` if the color value cannot be parsed.
    """
    r, g, b = 128, 128, 128  # neutral default

    try:
        if isinstance(color, str):
            hex_val = color.lstrip("#")
            if len(hex_val) == 6:
                r, g, b = int(hex_val[0:2], 16), int(hex_val[2:4], 16), int(hex_val[4:6], 16)
        elif isinstance(color, (tuple, list)) and len(color) >= 3:
            r, g, b = int(color[0]), int(color[1]), int(color[2])
        else:
            return ""
    except (ValueError, TypeError) as exc:
        logger.debug("_color_to_description: could not parse color %r (%s)", color, exc)
        return ""

    luminance = 0.299 * r + 0.587 * g + 0.114 * b

    # Hue-dominant bucket
    max_ch = max(r, g, b)
    if luminance > 220:
        return "crisp white"
    if luminance < 50:
        if b == max_ch:
            return "deep navy"
        return "rich black"
    if luminance > 180:
        return "light grey"
    if r == max_ch and r > 160 and g > 120:
        return "warm gold"
    if r == max_ch and r > 150:
        return "warm red"
    if g == max_ch:
        return "forest green"
    if b == max_ch:
        return "cool blue"
    return "mid-tone neutral"


def _extract_keywords(text: str, brief: str = "") -> list[str]:
    """
    Extract a short list of meaningful brand keywords from scraped text and brief.

    Uses a simple stop-word filtered frequency approach; returns up to 10
    high-frequency tokens that are likely brand-relevant.

    Parameters
    ----------
    text : str
        Aggregated scraped text.
    brief : str, optional
        Creative brief text.

    Returns
    -------
    list[str]
        Up to 10 lowercase keyword strings.
    """
    _stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "has", "have", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "our", "your", "their", "its", "we",
        "you", "they", "it", "this", "that", "these", "those", "not", "as",
        "more", "new", "get", "now", "all", "one", "also", "which",
    }

    corpus = f"{text} {brief}"
    tokens = re.findall(r"[a-zA-Z]{4,}", corpus.lower())  # 4+ char tokens only
    freq: dict[str, int] = {}
    for tok in tokens:
        if tok not in _stop_words:
            freq[tok] = freq.get(tok, 0) + 1

    sorted_tokens = sorted(freq, key=lambda k: freq[k], reverse=True)
    keywords = sorted_tokens[:10]
    logger.debug("_extract_keywords: top keywords=%s", keywords)
    return keywords
