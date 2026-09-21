"""Multi-provider AI client for OpenRouter and Agnes AI."""
import base64, io, mimetypes, time, urllib.parse
import requests
from PIL import Image
from . import config

class ProviderError(RuntimeError):
    pass

# Known supported video durations per provider (fallback when model metadata unavailable)
OPENROUTER_VIDEO_DURATIONS = [4, 6, 8]
AGNES_VIDEO_DURATIONS = [5, 6, 8, 10]


def _headers(provider):
    if provider == "agnes":
        return {"Authorization": f"Bearer {config.AGNES_API_KEY}", "Content-Type": "application/json"}
    h = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    if config.OPENROUTER_SITE_URL:
        h["HTTP-Referer"] = config.OPENROUTER_SITE_URL
    if config.OPENROUTER_APP_TITLE:
        h["X-OpenRouter-Title"] = config.OPENROUTER_APP_TITLE
    return h


def _base(provider):
    return config.AGNES_BASE_URL.rstrip("/") if provider == "agnes" else config.OPENROUTER_BASE_URL.rstrip("/")


def _api_error(response, provider):
    try:
        body = response.json()
        detail = body.get("error", body)
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("detail") or str(detail)
        else:
            message = str(detail)
    except Exception:
        message = response.text[:500] or response.reason
    return ProviderError(f"{provider.title()} request failed ({response.status_code}): {message}")


def _request(provider, method, path, **kwargs):
    key = config.AGNES_API_KEY if provider == "agnes" else config.OPENROUTER_API_KEY
    if not key:
        raise ProviderError(f"{provider.title()} API key is not configured.")
    response = requests.request(
        method,
        _base(provider) + path,
        headers=_headers(provider),
        timeout=kwargs.pop("timeout", config.REQUEST_TIMEOUT),
        **kwargs
    )
    if not response.ok:
        raise _api_error(response, provider)
    return response


def file_data_uri(path, max_dim=1280):
    try:
        im = Image.open(path)
        if max(im.size) > max_dim:
            im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _chat_provider(provider, instruction, model, image_paths=()):
    content = [{"type": "text", "text": instruction}]
    for path in image_paths[:6]:
        content.append({"type": "image_url", "image_url": {"url": file_data_uri(path)}})
    data = _request(
        provider, "POST", "/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": content}]},
        timeout=config.REQUEST_TIMEOUT
    ).json()
    result = data["choices"][0]["message"]["content"]
    if isinstance(result, list):
        result = "".join(item.get("text", "") for item in result if isinstance(item, dict))
    return str(result or "").strip()


def chat(instruction, model=None, provider="openrouter", image_paths=()):
    if provider == "agnes":
        return _chat_provider("agnes", instruction, model or config.AGNES_TEXT_MODEL, image_paths)
    models = [model or config.OPENROUTER_TEXT_MODEL]
    if config.OPENROUTER_FALLBACK_TEXT_MODEL:
        models.append(config.OPENROUTER_FALLBACK_TEXT_MODEL)
    last = None
    for m in dict.fromkeys(models):
        try:
            return _chat_provider("openrouter", instruction, m, image_paths)
        except Exception as e:
            last = e
    raise ProviderError(str(last) if last else "OpenRouter text generation failed.")


def _write_remote_image_response(res, output_path):
    item = (res.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        output_path.write_bytes(base64.b64decode(item["b64_json"]))
        return True
    url = item.get("url")
    if url:
        r = requests.get(url, timeout=90)
        if r.ok and r.content:
            output_path.write_bytes(r.content)
            return True
    return False


def generate_image(provider, model, prompt, output_path, reference_paths=(), size=None):
    size = size or config.DEFAULT_IMAGE_SIZE
    if provider == "agnes":
        payload = {"model": model, "prompt": prompt, "n": 1, "size": size,
                   "extra_body": {"response_format": "b64_json"}}
        if reference_paths:
            payload["extra_body"]["image"] = [file_data_uri(p) for p in reference_paths[:4]]
        res = _request("agnes", "POST", "/images/generations", json=payload, timeout=120)
        if _write_remote_image_response(res, output_path):
            return model
        raise ProviderError("Agnes returned no image data.")
    payload = {"model": model, "prompt": prompt, "size": size, "output_format": "png"}
    if reference_paths:
        payload["input_references"] = [
            {"type": "image_url", "image_url": {"url": file_data_uri(p)}} for p in reference_paths[:4]
        ]
    res = _request("openrouter", "POST", "/images", json=payload, timeout=120)
    if _write_remote_image_response(res, output_path):
        return model
    raise ProviderError("OpenRouter returned no image data.")


def _snap_duration(requested, allowed):
    """Always return a duration that the provider accepts."""
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        requested = allowed[0] if allowed else 6
    if requested in allowed:
        return requested
    return min(allowed, key=lambda x: abs(x - requested))


def _openrouter_video(prompt, model, output_path, reference_path, settings):
    allowed = list(OPENROUTER_VIDEO_DURATIONS)
    requested = settings.get("duration", config.VIDEO_SECONDS)
    duration = _snap_duration(requested, allowed)
    aspect = settings.get("aspect_ratio") or config.OPENROUTER_VIDEO_RATIO
    resolution = settings.get("resolution") or config.OPENROUTER_VIDEO_RESOLUTION
    payload = {
        "model": model,
        "prompt": prompt,
        "duration": int(duration),
        "aspect_ratio": aspect,
        "resolution": resolution,
    }
    if reference_path:
        payload["frame_images"] = [{
            "type": "image_url",
            "image_url": {"url": file_data_uri(reference_path)},
            "frame_type": "first_frame",
        }]
    job = _request("openrouter", "POST", "/videos", json=payload, timeout=60).json()
    job_id = job.get("id")
    if not job_id:
        raise ProviderError("OpenRouter video API did not return a job ID.")
    deadline = time.monotonic() + config.VIDEO_TIMEOUT
    while time.monotonic() < deadline:
        status = _request("openrouter", "GET", f"/videos/{job_id}", timeout=60).json()
        state = str(status.get("status", "")).lower()
        if state == "completed":
            content = _request("openrouter", "GET", f"/videos/{job_id}/content", timeout=120).content
            if content:
                output_path.write_bytes(content)
                return model, duration
            raise ProviderError("OpenRouter video API returned an empty file.")
        if state in {"failed", "cancelled", "expired"}:
            raise ProviderError(f"OpenRouter video generation {state}: {status.get('error') or 'unknown error'}")
        time.sleep(config.VIDEO_POLL_SECONDS)
    raise ProviderError("OpenRouter video generation timed out.")


def _agnes_video(prompt, model, output_path, reference_path, settings):
    allowed = list(AGNES_VIDEO_DURATIONS)
    requested = settings.get("duration", 5)
    seconds = _snap_duration(requested, allowed)
    aspect = settings.get("aspect_ratio", "16:9")
    size = str(settings.get("resolution", "720P")).upper().replace("P", "P")
    if not size.endswith("P"):
        size = size + "P" if size.isdigit() else "720P"
    payload = {
        "model": model,
        "prompt": prompt,
        "mode": "keyframes" if reference_path else "ti2vid",
        "seconds": str(int(seconds)),
        "size": size,
        "aspect_ratio": aspect,
        "n": 1,
    }
    if reference_path:
        payload["images"] = [file_data_uri(reference_path)]
    job = _request("agnes", "POST", "/videos", json=payload, timeout=60).json()
    video_id = job.get("video_id") or job.get("id")
    if not video_id:
        raise ProviderError("Agnes video API did not return a video_id.")
    deadline = time.monotonic() + config.VIDEO_TIMEOUT
    while time.monotonic() < deadline:
        url = (
            f"https://apihub.agnes-ai.com/agnesapi"
            f"?video_id={urllib.parse.quote(str(video_id))}"
            f"&model_name={urllib.parse.quote(model)}"
        )
        status = requests.get(url, headers=_headers("agnes"), timeout=60)
        if not status.ok:
            raise _api_error(status, "agnes")
        data = status.json()
        state = str(data.get("status", "")).lower()
        if state == "completed":
            video_url = data.get("url")
            if not video_url:
                raise ProviderError("Agnes completed the job but returned no video URL.")
            r = requests.get(video_url, timeout=180)
            if r.ok and r.content:
                output_path.write_bytes(r.content)
                return model, seconds
            raise ProviderError("Agnes video URL could not be downloaded.")
        if state in {"failed", "cancelled", "expired"}:
            raise ProviderError(f"Agnes video generation {state}: {data.get('error') or 'unknown error'}")
        time.sleep(config.VIDEO_POLL_SECONDS)
    raise ProviderError("Agnes video generation timed out.")


def generate_video(provider, model, prompt, output_path, reference_path=None, settings=None):
    settings = settings or {}
    if provider == "agnes":
        return _agnes_video(prompt, model, output_path, reference_path, settings)
    return _openrouter_video(prompt, model, output_path, reference_path, settings)


def provider_status():
    status = {}
    for provider in ("openrouter", "agnes"):
        key = config.OPENROUTER_API_KEY if provider == "openrouter" else config.AGNES_API_KEY
        status[provider] = {"configured": bool(key)}
    return status


def check_auth():
    """Lightweight auth / readiness probe used by /api/health."""
    result = {
        "openrouter": {"configured": bool(config.OPENROUTER_API_KEY), "ok": False, "detail": ""},
        "agnes": {"configured": bool(config.AGNES_API_KEY), "ok": False, "detail": ""},
    }
    if config.OPENROUTER_API_KEY:
        try:
            # Soft probe — models list is cheap and confirms the key
            r = requests.get(
                config.OPENROUTER_BASE_URL.rstrip("/") + "/key",
                headers=_headers("openrouter"),
                timeout=12,
            )
            if r.ok:
                key_info = (r.json().get("data") or {})
                if key_info.get("disabled"):
                    result["openrouter"]["detail"] = "API key is disabled"
                else:
                    result["openrouter"]["ok"] = True
                    result["openrouter"]["detail"] = "ready"
            else:
                result["openrouter"]["detail"] = f"HTTP {r.status_code}"
        except Exception as e:
            result["openrouter"]["detail"] = str(e)[:120]
    else:
        result["openrouter"]["detail"] = "API key missing"
    if config.AGNES_API_KEY:
        result["agnes"]["ok"] = True
        result["agnes"]["detail"] = "key present"
    else:
        result["agnes"]["detail"] = "API key missing"
    return result
