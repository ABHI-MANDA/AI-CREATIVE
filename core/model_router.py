from . import config

def _label(provider, model_id, kind):
    """Build a human-friendly label that never shows a blank model ID."""
    provider_label = "OpenRouter" if provider == "openrouter" else "Agnes" if provider == "agnes" else "Local"
    if kind == "local":
        return f"Local fallback"
    if model_id:
        return f"{provider_label} \u2022 {model_id}"
    return f"{provider_label} \u2022 (not configured)"


REGISTRY = {
    "copy": [
        {
            "id": "openrouter-text",
            "provider": "openrouter",
            "label": _label("openrouter", config.OPENROUTER_TEXT_MODEL, "hosted"),
            "kind": "hosted",
            "model": config.OPENROUTER_TEXT_MODEL,
        },
        {
            "id": "agnes-text",
            "provider": "agnes",
            "label": _label("agnes", config.AGNES_TEXT_MODEL, "hosted"),
            "kind": "hosted",
            "model": config.AGNES_TEXT_MODEL,
        },
        {
            "id": "local-copy",
            "provider": "local",
            "label": "Local fallback",
            "kind": "local",
            "model": "",
        },
    ],
    "image": [
        {
            "id": "openrouter-image",
            "provider": "openrouter",
            "label": _label("openrouter", config.OPENROUTER_IMAGE_MODEL, "hosted"),
            "kind": "hosted",
            "model": config.OPENROUTER_IMAGE_MODEL,
        },
        {
            "id": "agnes-image",
            "provider": "agnes",
            "label": _label("agnes", config.AGNES_IMAGE_MODEL, "hosted"),
            "kind": "hosted",
            "model": config.AGNES_IMAGE_MODEL,
        },
        {
            "id": "local-image",
            "provider": "local",
            "label": "Local fallback",
            "kind": "local",
            "model": "",
        },
    ],
    "video": [
        {
            "id": "openrouter-video",
            "provider": "openrouter",
            "label": _label("openrouter", config.OPENROUTER_VIDEO_MODEL, "hosted"),
            "kind": "hosted",
            "model": config.OPENROUTER_VIDEO_MODEL,
            "durations": [4, 6, 8],
            "note": "Veo / Seedance-style models accept 4, 6 or 8 seconds only",
        },
        {
            "id": "agnes-video",
            "provider": "agnes",
            "label": _label("agnes", config.AGNES_VIDEO_MODEL, "hosted"),
            "kind": "hosted",
            "model": config.AGNES_VIDEO_MODEL,
            "durations": [5, 6, 8, 10],
            "note": "Agnes video accepts 5, 6, 8 or 10 seconds",
        },
        {
            "id": "local-video",
            "provider": "local",
            "label": "Local fallback",
            "kind": "local",
            "model": "",
            "durations": [4, 5, 6, 8, 10],
            "note": "Local renderer accepts any listed duration",
        },
    ],
}


def available_models(creative_type):
    result = []
    for model in REGISTRY.get(creative_type, []):
        usable = model["kind"] == "local"
        reason = ""
        if model["provider"] == "openrouter":
            if not config.OPENROUTER_API_KEY:
                usable = False
                reason = "Set OPENROUTER_API_KEY in .env"
            elif not model["model"]:
                usable = False
                env_var = {
                    "openrouter-image": "OPENROUTER_IMAGE_MODEL",
                    "openrouter-video": "OPENROUTER_VIDEO_MODEL",
                }.get(model["id"], "OPENROUTER_TEXT_MODEL")
                reason = f"Set {env_var} in .env"
            else:
                usable = True
        elif model["provider"] == "agnes":
            if not config.AGNES_API_KEY:
                usable = False
                reason = "Set AGNES_API_KEY in .env"
            elif not model["model"]:
                usable = False
                reason = "Set model ID in .env"
            else:
                usable = True
        result.append({**model, "usable": usable, "unavailable_reason": reason})
    return result


def get_model(model_id, creative_type):
    return next(
        (m for m in available_models(creative_type) if m["id"] == model_id and m["usable"]),
        None,
    )


def select_model(creative_type, prompt=""):
    options = available_models(creative_type)
    return next(
        (m for m in options if m["usable"] and m["kind"] == "hosted"),
        next((m for m in options if m["usable"]), None),
    )


def snap_duration_for_model(model, requested):
    durations = model.get("durations") or [4, 6, 8]
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        requested = durations[0]
    if requested in durations:
        return requested
    return min(durations, key=lambda x: abs(x - requested))
