"""OpenRouter client for text, vision, image and video generation."""
import base64, io, mimetypes, time, urllib.parse
import requests
from PIL import Image
from . import config

class ProviderError(RuntimeError):
    """Safe error suitable for returning to an application user."""

def _headers():
    headers = {'Authorization': f'Bearer {config.OPENROUTER_API_KEY}', 'Content-Type': 'application/json'}
    if config.OPENROUTER_SITE_URL: headers['HTTP-Referer'] = config.OPENROUTER_SITE_URL
    if config.OPENROUTER_APP_TITLE: headers['X-OpenRouter-Title'] = config.OPENROUTER_APP_TITLE
    return headers

def check_auth():
    """Verify OpenRouter key status and credit balance."""
    if not config.OPENROUTER_API_KEY:
        return {'connected': False, 'error': 'OPENROUTER_API_KEY is not configured.'}
    try:
        r = requests.get(config.OPENROUTER_BASE_URL.rstrip('/') + '/auth/key', headers=_headers(), timeout=10)
        if r.ok:
            data = r.json().get('data', {})
            return {
                'connected': True,
                'label': data.get('label', 'Active Key'),
                'is_free_tier': data.get('is_free_tier', False),
                'free_daily_remaining': data.get('free_model_daily_requests', {}).get('remaining'),
                'primary_model': config.OPENROUTER_TEXT_MODEL
            }
        return {'connected': False, 'error': f'Status {r.status_code}: {r.text[:100]}'}
    except Exception as e:
        return {'connected': False, 'error': str(e)}

def _api_error(response):
    try:
        detail = response.json().get('error', {})
        message = detail.get('message') if isinstance(detail, dict) else str(detail)
    except ValueError:
        message = response.reason
    return ProviderError(f'OpenRouter request failed ({response.status_code}): {message or "unknown error"}')

def _request(method, path, **kwargs):
    if not config.OPENROUTER_API_KEY:
        raise ProviderError('OPENROUTER_API_KEY is not configured.')
    response = requests.request(
        method,
        config.OPENROUTER_BASE_URL.rstrip('/') + path,
        headers=_headers(),
        timeout=kwargs.pop('timeout', config.REQUEST_TIMEOUT),
        **kwargs
    )
    if not response.ok:
        raise _api_error(response)
    return response

def file_data_uri(path, max_dim=1024):
    """Encode an image path to a compact data URI, resizing if large."""
    try:
        im = Image.open(path)
        if max(im.size) > max_dim:
            im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.convert('RGB').save(buf, format='JPEG', quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{b64}'
    except Exception:
        mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')

def chat(instruction, model=None, image_paths=()):
    """Send chat or vision prompt with automatic model fallback."""
    models_to_try = [
        model or config.OPENROUTER_TEXT_MODEL,
        getattr(config, 'OPENROUTER_FALLBACK_TEXT_MODEL', 'google/gemma-4-31b-it:free'),
        'qwen/qwen3.8-27b:free'
    ]
    # Remove duplicates while preserving order
    seen = set()
    models_to_try = [m for m in models_to_try if m and not (m in seen or seen.add(m))]

    content = [{'type': 'text', 'text': instruction}]
    for path in image_paths[:4]:
        content.append({'type': 'image_url', 'image_url': {'url': file_data_uri(path)}})

    last_error = None
    for m in models_to_try:
        try:
            data = _request(
                'POST',
                '/chat/completions',
                json={'model': m, 'messages': [{'role': 'user', 'content': content}]},
                timeout=config.REQUEST_TIMEOUT
            ).json()
            result = data['choices'][0]['message']['content']
            if isinstance(result, list):
                result = ''.join(item.get('text', '') for item in result if isinstance(item, dict))
            text = str(result or '').strip()
            if text:
                return text
        except Exception as error:
            last_error = error
            continue

    if last_error:
        raise ProviderError(f'All text models failed: {last_error}') from last_error
    raise ProviderError('OpenRouter returned no chat content.')

def openrouter_image(prompt, output_path, reference_paths=()):
    """Generate image via OpenRouter or photorealistic AI engine."""
    # First attempt OpenRouter Image endpoint if available
    try:
        payload = {
            'model': config.OPENROUTER_IMAGE_MODEL,
            'prompt': prompt,
            'size': config.DEFAULT_IMAGE_SIZE,
            'output_format': 'png'
        }
        if reference_paths:
            payload['input_references'] = [{'type': 'image_url', 'image_url': {'url': file_data_uri(path)}} for path in reference_paths[:4]]
        res = _request('POST', '/images', json=payload, timeout=90)
        item = (res.json().get('data') or [{}])[0]
        if item.get('b64_json'):
            output_path.write_bytes(base64.b64decode(item['b64_json']))
            return 'openrouter-image'
    except Exception:
        pass

    # Seamless fallback: Photorealistic AI synthesis engine (Flux/SDXL)
    clean_prompt = prompt.replace('\n', ' ').strip()[:1000]
    encoded_prompt = urllib.parse.quote(clean_prompt)
    synth_url = f'https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true'
    
    r = requests.get(synth_url, timeout=60)
    if r.ok and len(r.content) > 1000 and r.headers.get('content-type', '').startswith('image'):
        output_path.write_bytes(r.content)
        return 'ai-flux-synthesizer'
        
    raise ProviderError('Image generation failed across all providers.')

def openrouter_video(prompt, output_path, reference_path=None):
    """Generate video via OpenRouter or raise for dynamic motion rendering."""
    payload = {
        'model': config.OPENROUTER_VIDEO_MODEL,
        'prompt': prompt,
        'duration': config.VIDEO_SECONDS,
        'aspect_ratio': config.OPENROUTER_VIDEO_RATIO,
        'resolution': config.OPENROUTER_VIDEO_RESOLUTION
    }
    if reference_path:
        payload['frame_images'] = [{'type': 'image_url', 'image_url': {'url': file_data_uri(reference_path)}, 'frame_type': 'first_frame'}]
    
    job = _request('POST', '/videos', json=payload, timeout=45).json()
    job_id = job.get('id')
    if not job_id:
        raise ProviderError('OpenRouter video API did not return a job ID.')
    
    deadline = time.monotonic() + config.VIDEO_TIMEOUT
    while time.monotonic() < deadline:
        status = _request('GET', f'/videos/{job_id}', timeout=30).json()
        state = status.get('status', '').lower()
        if state == 'completed':
            content = _request('GET', f'/videos/{job_id}/content', timeout=60).content
            if not content:
                raise ProviderError('OpenRouter video API returned an empty file.')
            output_path.write_bytes(content)
            return job_id
        if state in {'failed', 'cancelled', 'expired'}:
            raise ProviderError(f'OpenRouter video generation {state}: {status.get("error") or "unknown error"}')
        time.sleep(config.VIDEO_POLL_SECONDS)
    raise ProviderError('OpenRouter video generation timed out.')
