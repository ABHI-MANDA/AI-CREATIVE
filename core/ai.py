"""Multi-provider AI client for OpenRouter and Agnes AI."""
import base64
import io
import mimetypes
import time
import urllib.parse
import logging
import requests
from PIL import Image
from . import config

logger = logging.getLogger(__name__)

class ProviderError(RuntimeError):
    pass

# Known supported video durations per provider (fallback when model metadata unavailable)
OPENROUTER_VIDEO_DURATIONS = [4, 6, 8]
AGNES_VIDEO_DURATIONS = [5, 6, 8, 10]

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds


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
    
    timeout = kwargs.pop("timeout", config.REQUEST_TIMEOUT)
    retries = kwargs.pop("retries", MAX_RETRIES)
    
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(
                method,
                _base(provider) + path,
                headers=_headers(provider),
                timeout=timeout,
                **kwargs
            )
            if response.ok:
                return response
            
            # Don't retry on client errors (4xx), only on server errors (5xx) and 429
            if 400 <= response.status_code < 500 and response.status_code != 429:
                raise _api_error(response, provider)
            
            last_error = _api_error(response, provider)
            logger.warning(
                f"{provider.title()} request failed (attempt {attempt + 1}/{retries + 1}): {last_error}"
            )
        except requests.RequestException as e:
            last_error = ProviderError(f"{provider.title()} request error: {e}")
            logger.warning(
                f"{provider.title()} request exception (attempt {attempt + 1}/{retries + 1}): {e}"
            )
        
        if attempt < retries:
            backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.info(f"Retrying in {backoff}s...")
            time.sleep(backoff)
    
    raise last_error or ProviderError(f"{provider.title()} request failed after {retries + 1} attempts")


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


def _chat_provider(provider, instruction, model, image_paths=(), timeout=None):
    content = [{"type": "text", "text": instruction}]
    for path in image_paths[:6]:
        content.append({"type": "image_url", "image_url": {"url": file_data_uri(path)}})
    data = _request(
        provider, "POST", "/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": content}]},
        timeout=timeout or config.REQUEST_TIMEOUT,
        retries=2,
    ).json()
    result = data["choices"][0]["message"]["content"]
    if isinstance(result, list):
        result = "".join(item.get("text", "") for item in result if isinstance(item, dict))
    return str(result or "").strip()


def chat(instruction, model=None, provider="openrouter", image_paths=(), timeout=None):
    if provider == "agnes":
        return _chat_provider("agnes", instruction, model or config.AGNES_TEXT_MODEL, image_paths, timeout=timeout)
    models = [model or config.OPENROUTER_TEXT_MODEL]
    if config.OPENROUTER_FALLBACK_TEXT_MODEL:
        models.append(config.OPENROUTER_FALLBACK_TEXT_MODEL)
    last = None
    for m in dict.fromkeys(models):
        try:
            return _chat_provider("openrouter", instruction, m, image_paths, timeout=timeout)
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
    
    logger.info(f"OpenRouter video job started: {job_id}, duration={duration}s, aspect={aspect}, resolution={resolution}")
    
    deadline = time.monotonic() + config.VIDEO_TIMEOUT
    last_state = None
    while time.monotonic() < deadline:
        try:
            status = _request("openrouter", "GET", f"/videos/{job_id}", timeout=60).json()
        except ProviderError as e:
            logger.warning(f"OpenRouter video status check failed: {e}")
            time.sleep(config.VIDEO_POLL_SECONDS)
            continue
            
        state = str(status.get("status", "")).lower()
        if state != last_state:
            logger.info(f"OpenRouter video job {job_id} state: {state}")
            last_state = state
        
        if state == "completed":
            # Try multiple content endpoints
            content = None
            for endpoint in [f"/videos/{job_id}/content", f"/videos/{job_id}/download"]:
                try:
                    content = _request("openrouter", "GET", endpoint, timeout=120).content
                    if content:
                        break
                except ProviderError:
                    continue
            
            if content:
                output_path.write_bytes(content)
                logger.info(f"OpenRouter video job {job_id} completed successfully")
                return model, duration
            raise ProviderError("OpenRouter video API returned no content.")
        
        if state in {"failed", "cancelled", "expired"}:
            error_msg = status.get('error') or status.get('message') or 'unknown error'
            raise ProviderError(f"OpenRouter video generation {state}: {error_msg}")
        
        if state in {"queued", "processing", "running", "pending"}:
            time.sleep(config.VIDEO_POLL_SECONDS)
            continue
            
        # Unknown state, keep polling
        logger.warning(f"OpenRouter video job {job_id} unknown state: {state}")
        time.sleep(config.VIDEO_POLL_SECONDS)
    
    raise ProviderError(f"OpenRouter video generation timed out after {config.VIDEO_TIMEOUT}s")


def _agnes_video(prompt, model, output_path, reference_path, settings):
    allowed = list(AGNES_VIDEO_DURATIONS)
    requested = settings.get("duration", 5)
    seconds = _snap_duration(requested, allowed)
    aspect = settings.get("aspect_ratio", "16:9")
    size = str(settings.get("resolution", "720P")).upper().replace("P", "P")
    if not size.endswith("P"):
        size = size + "P" if size.isdigit() else "720P"
    mode = "keyframes" if reference_path else "ti2vid"
    payload = {
        "model": model,
        "prompt": prompt,
        "mode": mode,
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

    logger.info(f"Agnes video job started: {video_id}, model={model}, duration={seconds}s, size={size}")

    # Docs: poll GET https://apihub.agnes-ai.com/agnesapi?video_id=...&model_name=...
    # (domain root — NOT under the /v1 base URL).
    poll_root = config.AGNES_BASE_URL.rstrip("/").split("/v1")[0].rstrip("/")
    poll_url = f"{poll_root}/agnesapi"
    headers = _headers("agnes")

    deadline = time.monotonic() + config.VIDEO_TIMEOUT
    last_state = None
    while time.monotonic() < deadline:
        try:
            resp = requests.get(
                poll_url,
                headers=headers,
                params={"video_id": video_id, "model_name": model},
                timeout=60,
            )
        except requests.RequestException as e:
            logger.warning(f"Agnes video status check failed: {e}")
            time.sleep(config.VIDEO_POLL_SECONDS)
            continue

        if resp.status_code == 429:
            time.sleep(config.VIDEO_POLL_SECONDS * 2)
            continue
        if not resp.ok:
            raise _api_error(resp, "agnes")

        data = resp.json()
        state = str(data.get("status", "")).lower()
        if state != last_state:
            logger.info(f"Agnes video job {video_id} state: {state} progress={data.get('progress')}")
            last_state = state

        if state == "completed":
            meta = data.get("metadata") or {}
            video_url = (
                meta.get("url")
                or data.get("url")
                or data.get("video_url")
                or data.get("download_url")
            )
            if not video_url:
                raise ProviderError("Agnes completed the job but returned no video URL.")
            try:
                r = requests.get(video_url, timeout=180, headers={"User-Agent": "AI-Creative-Studio/3.0"})
                r.raise_for_status()
                if r.content:
                    output_path.write_bytes(r.content)
                    logger.info(f"Agnes video job {video_id} completed successfully")
                    return model, seconds
                raise ProviderError("Agnes video download returned empty content.")
            except requests.RequestException as e:
                raise ProviderError(f"Agnes video URL could not be downloaded: {e}")

        if state in {"failed", "cancelled", "expired"}:
            err = data.get("error") or {}
            if isinstance(err, dict):
                error_msg = err.get("message") or "unknown error"
            else:
                error_msg = str(err) or data.get("message") or "unknown error"
            raise ProviderError(f"Agnes video generation {state}: {error_msg}")

        if state in {"queued", "in_progress", "processing", "running", "pending", "generating"}:
            time.sleep(config.VIDEO_POLL_SECONDS)
            continue

        logger.warning(f"Agnes video job {video_id} unknown state: {state}")
        time.sleep(config.VIDEO_POLL_SECONDS)

    raise ProviderError(f"Agnes video generation timed out after {config.VIDEO_TIMEOUT}s")


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


def check_video_capability(provider=None):
    """Check which providers support video generation with current config."""
    capabilities = {}
    
    providers = [provider] if provider else ["openrouter", "agnes"]
    
    for prov in providers:
        caps = {
            "provider": prov,
            "configured": False,
            "model_configured": False,
            "model_id": "",
            "supported_durations": [],
            "supports_reference": False,
        }
        
        if prov == "openrouter":
            caps["configured"] = bool(config.OPENROUTER_API_KEY)
            caps["model_id"] = config.OPENROUTER_VIDEO_MODEL or ""
            caps["model_configured"] = bool(config.OPENROUTER_VIDEO_MODEL)
            caps["supported_durations"] = OPENROUTER_VIDEO_DURATIONS
            caps["supports_reference"] = True
        elif prov == "agnes":
            caps["configured"] = bool(config.AGNES_API_KEY)
            caps["model_id"] = config.AGNES_VIDEO_MODEL or ""
            caps["model_configured"] = bool(config.AGNES_VIDEO_MODEL)
            caps["supported_durations"] = AGNES_VIDEO_DURATIONS
            caps["supports_reference"] = True
        
        caps["ready"] = caps["configured"] and caps["model_configured"]
        capabilities[prov] = caps
    
    return capabilities
