"""Reference ingestion: HTML + image/video assets + frame extraction + visual signals."""
import hashlib
import json
import mimetypes
import re
import shutil
import socket
import ipaddress
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
import cv2
import numpy as np
from bs4 import BeautifulSoup
from PIL import Image
from . import config

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 AI-Creative-Studio/3.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}
VIDEO_EXT = ('.mp4', '.webm', '.mov', '.m4v', '.avi', '.mkv')
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')

def _validate_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('Use a valid public http(s) URL.')
    if config.ALLOW_PRIVATE_REFERENCE_URLS:
        return
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as error:
        raise ValueError('Reference host could not be resolved.') from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError('Private or non-public reference URLs are not allowed.')

def _clean_url_ext(url, default='.jpg'):
    clean_path = urlparse(url).path
    ext = Path(clean_path).suffix.lower()
    return ext if ext in IMAGE_EXT or ext in VIDEO_EXT else default

def _download(url, dest, max_mb=None):
    _validate_public_url(url)
    max_bytes = (max_mb or config.MAX_DOWNLOAD_MB) * 1024 * 1024
    
    # Use a session with limited redirects to prevent redirect loops
    session = requests.Session()
    session.max_redirects = 5
    r = session.get(url, headers=HEADERS, timeout=config.SCRAPE_TIMEOUT, stream=True)
    r.raise_for_status()
    
    # Validate content type
    content_type = r.headers.get('content-type', '').lower()
    allowed_types = [
        'image/', 'video/', 'application/octet-stream',
        'application/x-mpegurl', 'video/mp2t'  # HLS
    ]
    if not any(content_type.startswith(t) for t in allowed_types):
        logger.warning(f"Unexpected content type: {content_type} for {url}")
    
    cl = int(r.headers.get('content-length') or 0)
    if cl > max_bytes:
        raise RuntimeError('asset exceeds configured download limit')
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with dest.open('wb') as f:
        for chunk in r.iter_content(1024 * 256):
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError('asset exceeds configured download limit')
            f.write(chunk)
    return dest

def image_signals(path):
    try:
        im = Image.open(path).convert('RGB')
        a = np.asarray(im)
        return {
            'width': im.width,
            'height': im.height,
            'aspect_ratio': round(im.width / max(1, im.height), 3),
            'mean_brightness': round(float(a.mean()), 2),
            'mean_rgb': [round(float(x), 1) for x in a.reshape(-1, 3).mean(0)]
        }
    except Exception as e:
        return {'error': str(e)}

def video_signals(path, frame_dir):
    cap = cv2.VideoCapture(str(path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    dur = frames / fps if fps else 0
    frame_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    count = max(1, min(config.MAX_VIDEO_FRAMES, frames or 1))
    for i in range(count):
        idx = int((frames - 1) * i / max(1, count - 1)) if frames else 0
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            out = frame_dir / f'frame_{i+1:02d}.jpg'
            cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            saved.append(str(out))
    cap.release()
    return {
        'width': w,
        'height': h,
        'fps': round(fps, 2),
        'frames': frames,
        'duration_seconds': round(dur, 2),
        'frame_paths': saved
    }

def _clean_text(soup):
    for t in soup(['script', 'style', 'noscript', 'svg', 'nav', 'footer', 'form']):
        t.decompose()
    return re.sub(r'\s+', ' ', soup.get_text(' ', strip=True))[:12000]

def _parse_srcset(srcset_val):
    urls = []
    if not srcset_val: return urls
    for part in srcset_val.split(','):
        piece = part.strip().split(' ')[0].strip()
        if piece: urls.append(piece)
    return urls

def scrape_url(url, pid):
    # Sanitize and validate URL
    if url:
        url = url.strip()
        if not url:
            url = None
        elif urlparse(url).scheme not in ('http', 'https'):
            return {'error': 'Use a valid http(s) URL.', 'images': [], 'videos': [], 'assets': []}
        # Limit URL length
        if len(url) > 2048:
            return {'error': 'URL too long.', 'images': [], 'videos': [], 'assets': []}
    root = config.REFERENCE_DIR / pid
    root.mkdir(parents=True, exist_ok=True)
    if not url:
        return {'url': '', 'title': 'Brief-only project', 'text': '', 'images': [], 'videos': [], 'assets': [], 'visual_analysis': '', 'error': None}
    
    try:
        _validate_public_url(url)
        # Use session with limited redirects
        session = requests.Session()
        session.max_redirects = 5
        r = session.get(url, headers=HEADERS, timeout=config.SCRAPE_TIMEOUT)
        r.raise_for_status()
        
        # Validate response content type
        content_type = r.headers.get('content-type', '').lower()
        if not content_type.startswith('text/html'):
            return {'error': f'Expected HTML content, got {content_type}', 'images': [], 'videos': [], 'assets': []}
        
        final_url = r.url
        raw_html = r.text
        # Limit HTML size
        if len(raw_html) > 5 * 1024 * 1024:  # 5MB limit
            raw_html = raw_html[:5 * 1024 * 1024]
        
        soup = BeautifulSoup(raw_html, 'html.parser')
        
        # Extract title and rich marketing metadata
        title = soup.title.get_text(strip=True) if soup.title else final_url
        meta_desc = ''
        meta_keywords = ''
        
        for m in soup.find_all('meta'):
            name = (m.get('name') or m.get('property') or '').lower()
            content = m.get('content') or ''
            if name in ('description', 'og:description', 'twitter:description') and not meta_desc:
                meta_desc = content.strip()
            elif name in ('keywords',) and not meta_keywords:
                meta_keywords = content.strip()

        # Extract headings for structured context
        headings = []
        for h in soup.find_all(['h1', 'h2', 'h3'])[:10]:
            htext = h.get_text(strip=True)
            if htext and len(htext) > 3 and htext not in headings:
                headings.append(htext)

        body_text = _clean_text(soup)
        text_context = f"TITLE: {title}\nDESCRIPTION: {meta_desc}\nKEY HEADINGS: {' | '.join(headings)}\nCONTENT: {body_text}"[:12000]
        
        image_candidates = []
        video_candidates = []
        
        # 1. OpenGraph & Twitter image meta tags (Highest quality)
        for meta in soup.find_all('meta'):
            prop = (meta.get('property') or meta.get('name') or '').lower()
            content = meta.get('content') or ''
            if prop in ('og:image', 'og:image:secure_url', 'twitter:image', 'twitter:image:src') and content:
                image_candidates.append(urljoin(final_url, content))
            elif prop in ('og:video', 'og:video:url', 'og:video:secure_url', 'twitter:player:stream') and content:
                video_candidates.append(urljoin(final_url, content))

        # 2. JSON-LD structured data (Product, Organization, RealEstate)
        for s in soup.find_all('script', type='application/ld+json'):
            try:
                j = json.loads(s.string or '')
                items = j if isinstance(j, list) else [j]
                for item in items:
                    if isinstance(item, dict):
                        img_val = item.get('image') or item.get('logo') or item.get('thumbnailUrl')
                        if isinstance(img_val, str) and img_val:
                            image_candidates.append(urljoin(final_url, img_val))
                        elif isinstance(img_val, list):
                            for iv in img_val:
                                if isinstance(iv, str): image_candidates.append(urljoin(final_url, iv))
            except Exception:
                pass

        # 2b. Link tags (apple-touch-icon, icons, image preloads)
        for l in soup.find_all('link'):
            rel = ' '.join(l.get('rel', [])) if isinstance(l.get('rel'), list) else (l.get('rel') or '').lower()
            href = l.get('href')
            if href and any(k in rel for k in ('apple-touch-icon', 'icon', 'preload')):
                clean_ext = urlparse(href).path.lower()
                if any(clean_ext.endswith(ext) for ext in IMAGE_EXT):
                    image_candidates.append(urljoin(final_url, href))

        # 3. Standard img tags (src, data-src, srcset)
        for img in soup.find_all('img'):
            for attr in ('src', 'data-src', 'data-original', 'data-lazy-src', 'data-high-res-src'):
                val = img.get(attr)
                if val and not val.startswith('data:'):
                    image_candidates.append(urljoin(final_url, val))
            if img.get('srcset'):
                for u in _parse_srcset(img.get('srcset')):
                    image_candidates.append(urljoin(final_url, u))

        # 4. <picture> <source srcset="...">
        for src in soup.find_all('source'):
            if src.get('srcset'):
                for u in _parse_srcset(src.get('srcset')):
                    image_candidates.append(urljoin(final_url, u))
            if src.get('src'):
                u = urljoin(final_url, src.get('src'))
                if any(u.lower().endswith(ext) for ext in VIDEO_EXT): video_candidates.append(u)
                else: image_candidates.append(u)

        # 5. Inline CSS background-image
        for el in soup.find_all(style=True):
            style = el['style']
            bg_matches = re.findall(r'url\([\'"]?([^\'")]+)[\'"]?\)', style, re.IGNORECASE)
            for bg in bg_matches:
                if not bg.startswith('data:'):
                    image_candidates.append(urljoin(final_url, bg))

        # 6. Video tags and links
        for v in soup.find_all('video'):
            if v.get('src'): video_candidates.append(urljoin(final_url, v['src']))
        for a in soup.find_all('a', href=True):
            href = urljoin(final_url, a['href'])
            clean_href = href.lower().split('?')[0]
            if clean_href.endswith(VIDEO_EXT): video_candidates.append(href)

        # 7. Regex fallback for modern client-rendered SPAs (Next.js __NEXT_DATA__, Vite, Webflow)
        if not image_candidates:
            raw_img_matches = re.findall(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', raw_html, re.IGNORECASE)
            image_candidates.extend(raw_img_matches)

        # Deduplicate while preserving order, filter non-http
        seen = set()
        clean_images = []
        for u in image_candidates:
            u_clean = u.split('#')[0]
            if u_clean.startswith(('http://', 'https://')) and u_clean not in seen:
                # Exclude tiny spacer gifs or 1x1 tracker pixels
                if not any(x in u_clean.lower() for x in ('1x1', 'spacer', 'pixel', 'analytics', 'tracking')):
                    seen.add(u_clean)
                    clean_images.append(u_clean)

        clean_videos = []
        for u in video_candidates:
            u_clean = u.split('#')[0]
            if u_clean.startswith(('http://', 'https://')) and u_clean not in seen:
                seen.add(u_clean)
                clean_videos.append(u_clean)

        clean_images = clean_images[:config.MAX_REFERENCE_IMAGES]
        clean_videos = clean_videos[:config.MAX_REFERENCE_VIDEOS]

        def _download_image(args):
            i, u = args
            try:
                ext = _clean_url_ext(u, default='.jpg')
                p = root / f'image_{i+1:02d}{ext}'
                _download(u, p)
                sig = image_signals(p)
                # Filter out tiny corrupted or 1px downloads
                if sig.get('width', 0) >= 32 and sig.get('height', 0) >= 32:
                    return {
                        'type': 'image',
                        'url': u,
                        'path': str(p.relative_to(config.ROOT)),
                        'signals': sig,
                    }
                return {'type': 'image', 'url': u, 'error': 'image too small or invalid'}
            except Exception as e:
                return {'type': 'image', 'url': u, 'error': str(e)}

        def _download_video(args):
            i, u = args
            try:
                p = root / f'video_{i+1:02d}.mp4'
                _download(u, p)
                vs = video_signals(p, root / f'video_{i+1:02d}_frames')
                return {
                    'type': 'video',
                    'url': u,
                    'path': str(p.relative_to(config.ROOT)),
                    'signals': vs,
                    'frame_paths': [
                        str(Path(x).relative_to(config.ROOT))
                        for x in vs.get('frame_paths', [])
                    ],
                }
            except Exception as e:
                return {'type': 'video', 'url': u, 'error': str(e)}

        image_assets = []
        video_assets = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            if clean_images:
                image_assets = list(pool.map(_download_image, enumerate(clean_images)))
            if clean_videos:
                video_assets = list(pool.map(_download_video, enumerate(clean_videos)))
        assets = image_assets + video_assets

        return {
            'url': final_url,
            'title': title,
            'meta_description': meta_desc,
            'headings': headings,
            'text': text_context,
            'images': [a['url'] for a in assets if a.get('type') == 'image' and not a.get('error')],
            'videos': [a['url'] for a in assets if a.get('type') == 'video' and not a.get('error')],
            'assets': assets,
            'error': None
        }
    except Exception as e:
        return {'url': url, 'title': '', 'text': '', 'images': [], 'videos': [], 'assets': [], 'error': f'Fetch failed: {e}'}

def upload_asset(src, pid, filename):
    root = config.REFERENCE_DIR / pid
    root.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()
    safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    dest = root / safe
    shutil.copy2(src, dest)
    if ext in IMAGE_EXT:
        return {'type': 'image', 'path': str(dest.relative_to(config.ROOT)), 'signals': image_signals(dest), 'url': ''}
    if ext in VIDEO_EXT:
        vs = video_signals(dest, root / (Path(safe).stem + '_frames'))
        return {'type': 'video', 'path': str(dest.relative_to(config.ROOT)), 'signals': vs, 'frame_paths': [str(Path(x).relative_to(config.ROOT)) for x in vs['frame_paths']], 'url': ''}
    raise ValueError('Unsupported reference file. Use JPG, PNG, WEBP, MP4, MOV, WEBM or M4V.')
