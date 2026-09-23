"""Prompt engineering: blends brief + reference intelligence + mood + memory
into production-ready prompts, with optional LLM refinement."""

import re
from collections import Counter

from .ai import chat
from .learning import get_context
from . import config
from .creative_intelligence import build_dna, enrich_with_vision

STOPWORDS = set(
    "the a an and or but of to in on for with at by from is are was were "
    "this that these those it its as be been being will would can could "
    "should may might do does did not no yes we you they he she i our your "
    "their his her them us".split()
)


def top_keywords(text, n=15):
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", (text or "").lower())
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [w for w, _ in counts.most_common(n)]


def asset_summary(scraped):
    rows = []
    for a in (scraped or {}).get("assets", []):
        if a.get("error"):
            continue
        rows.append(f"{a.get('type', 'asset')}: {a.get('signals', {})}")
    return "\n".join(rows[:20])


def _mood_from_settings(settings):
    settings = settings or {}
    for key in ("copy", "image", "video"):
        mood = (settings.get(key) or {}).get("mood")
        if mood:
            return str(mood).strip()
    return str(settings.get("mood") or "").strip()


def _media_specs(settings):
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


def analyze_visual_references(scraped):
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
    except Exception as e:
        return f"Visual reference summary unavailable: {e}"


def build_prompts(scraped, brief, creative_types, settings=None):
    settings = settings or {}
    scraped = scraped or {}
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

    prompts = {}
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

    return {
        "keywords": kws,
        "mood": mood,
        "visual_analysis": visual,
        "creative_dna": dna,
        "vision_creative_direction": vision_dna,
        "prompts": prompts,
        "asset_count": len(scraped.get("assets") or []),
    }


def refine_with_llm(prompt, feedback, ctype="image", timeout=45):
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
            return x[: config.MAX_PROMPT_CHARS]
    except Exception:
        pass
    return (prompt + "\nMANDATORY REVIEW CORRECTIONS:\n" + feedback).strip()[
        : config.MAX_PROMPT_CHARS
    ]


def refine_many(items, timeout=45):
    """Refine (prompt, feedback, ctype) triples in parallel. Returns list of refined prompts."""
    from concurrent.futures import ThreadPoolExecutor

    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
        return list(
            pool.map(
                lambda it: refine_with_llm(it[0], it[1], it[2], timeout=timeout),
                items,
            )
        )
