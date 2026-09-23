import os, time, textwrap, math, logging, uuid
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import cv2, numpy as np
from . import config
from .ai import chat, generate_image, generate_video, ProviderError

logger = logging.getLogger(__name__)

def _font(size, bold=False):
    choices = [
        r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
        r"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ] if bold else [
        r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
        r"/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    for path in choices:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except Exception: pass
    return ImageFont.load_default()

def _extract_brief_headline(prompt):
    lines = [l.strip() for l in prompt.split("\n") if l.strip()]
    for line in lines:
        if line.lower().startswith("brief:") or "for this brief:" in line.lower():
            continue
        if 5 < len(line) < 70 and not any(k in line.lower() for k in ("reference", "quality", "creative", "prompt")):
            return line.strip(':"\'- ')
    return "Exclusive Campaign Creative"

def cinematic_campaign_image(prompt, job, reference_paths=()):
    width, height = 1024, 1024
    headline = _extract_brief_headline(prompt)
    if reference_paths and os.path.exists(reference_paths[0]):
        try:
            base = Image.open(reference_paths[0]).convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
            image = base
        except Exception:
            image = Image.new("RGB", (width, height), "#0b1329")
    else:
        image = Image.new("RGB", (width, height), "#0b1329")
        draw = ImageDraw.Draw(image)
        for y in range(height):
            ratio = y / height
            draw.line([(0, y), (width, y)], fill=(11 + int(25*ratio), 19 + int(45*ratio), 41 + int(65*ratio)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([(60,60),(330,108)], radius=14, fill="#0284c7")
    draw.text((82,72), "CAMPAIGN CREATIVE", font=_font(20, True), fill="#fff")
    y=155
    for line in textwrap.wrap(headline, 30)[:3]:
        draw.text((60,y),line,font=_font(46,True),fill="#fff"); y+=56
    draw.line([(60,y+10),(280,y+10)], fill="#38bdf8", width=4)
    y += 38
    for pt in ["• Premium visual direction", "• Conversion-focused messaging", "• Multi-channel campaign system"]:
        draw.text((60,y),pt,font=_font(22),fill="#cbd5e1"); y+=40
    draw.rounded_rectangle([(60,height-120),(390,height-65)], radius=10, fill="#38bdf8")
    draw.text((85,height-105),"EXPLORE CAMPAIGN",font=_font(22,True),fill="#081426")
    path=config.OUTPUT_DIR/f"{job}_image.png"; image.save(path); return path

def cinematic_campaign_video(prompt, job, base_image_path=None, settings=None):
    """Generate a professional local fallback video with cinematic effects."""
    settings = settings or {}
    aspect_ratio = settings.get("aspect_ratio", "16:9")
    dimensions = {
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "1:1": (720, 720),
    }
    width, height = dimensions.get(aspect_ratio, dimensions["16:9"])
    fps = config.VIDEO_FPS
    duration = int(settings.get("duration", config.VIDEO_SECONDS))
    total_frames = max(1, fps * duration)
    path = config.OUTPUT_DIR / f"{job}_video.mp4"
    headline = _extract_brief_headline(prompt)
    
    # Load or create base image
    if base_image_path and os.path.exists(base_image_path):
        src_img = Image.open(base_image_path).convert("RGB")
    else:
        # Generate a base image from prompt
        tmp_img_path = cinematic_campaign_image(prompt, f"{job}_tmp")
        src_img = Image.open(tmp_img_path).convert("RGB")
    
    # Crop to target aspect ratio (16:9)
    src_w, src_h = src_img.size
    target_aspect = width / height
    current = src_w / max(1, src_h)
    if current > target_aspect:
        new_w = int(src_h * target_aspect)
        left = (src_w - new_w) // 2
        src_img = src_img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / target_aspect)
        top = (src_h - new_h) // 2
        src_img = src_img.crop((0, top, src_w, top + new_h))
    
    # Resize slightly larger for ken burns effect
    src_img = src_img.resize((int(width * 1.3), int(height * 1.3)), Image.Resampling.LANCZOS)
    src_np = np.asarray(src_img)
    
    # mp4v is bundled with OpenCV's Windows builds. Trying avc1 first loads an
    # external OpenH264 DLL whose version often does not match the build.
    writer = None
    codec_name = None
    for candidate in ("mp4v", "MJPG"):
        if path.exists():
            path.unlink()
        candidate_writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*candidate), fps, (width, height)
        )
        if candidate_writer.isOpened():
            writer = candidate_writer
            codec_name = candidate
            break
        candidate_writer.release()

    if writer is None:
        raise RuntimeError(
            "No supported local video codec is available. "
            "OpenCV could not open mp4v or MJPG."
        )
    
    max_x, max_y = src_img.width - width, src_img.height - height
    
    # Color palette for professional look
    brand_color = (56, 189, 248)  # Cyan
    dark_overlay = (10, 15, 25)
    accent_color = (248, 189, 56)  # Gold
    
    for i in range(total_frames):
        t = i / max(1, total_frames - 1)
        
        # Smooth easing functions
        ease_in_out = 0.5 - 0.5 * math.cos(t * math.pi)  # Smooth step
        ease_out = 1 - (1 - t) ** 3  # Cubic ease out
        
        # Ken Burns: slow zoom + pan
        scale = 1.0 + 0.15 * ease_in_out
        crop_w, crop_h = int(width / scale), int(height / scale)
        
        # Pan trajectory: diagonal with slight curve
        pan_x = max_x * 0.6 * ease_out
        pan_y = max_y * 0.4 * (1 - math.cos(t * math.pi * 0.7)) * 0.5
        cx = min(src_img.width - crop_w, max(0, int(pan_x)))
        cy = min(src_img.height - crop_h, max(0, int(pan_y)))
        
        # Extract and resize frame
        frame = cv2.resize(
            src_np[cy:cy+crop_h, cx:cx+crop_w],
            (width, height),
            interpolation=cv2.INTER_LANCZOS4
        )
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        
        # Subtle color grading
        frame = cv2.convertScaleAbs(frame, alpha=0.95 + 0.1 * math.sin(t * math.pi), beta=2)
        
        # Add cinematic bars (letterbox) for ultra-wide feel on short videos
        if duration <= 6:
            bar_h = int(height * 0.08)
            frame[:bar_h, :] = frame[:bar_h, :] * 0.3
            frame[-bar_h:, :] = frame[-bar_h:, :] * 0.3
        
        # Fade in/out
        fade_in = min(1.0, t * 3) if t < 0.33 else 1.0
        fade_out = min(1.0, (1 - t) * 3) if t > 0.67 else 1.0
        fade = fade_in * fade_out
        
        # Lower third overlay
        if fade > 0.15:
            overlay = frame.copy()
            # Gradient background
            cv2.rectangle(overlay, (0, height - 140), (width, height), dark_overlay, -1)
            # Accent line
            cv2.rectangle(overlay, (40, height - 130), (48, height - 40), accent_color, -1)
            cv2.addWeighted(overlay, 0.8 * fade, frame, 1 - (0.8 * fade), 0, frame)
            
            # Headline text
            cv2.putText(frame, headline[:50], (65, height - 88),
                       cv2.FONT_HERSHEY_DUPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
            # Subtitle
            cv2.putText(frame, "AI CREATIVE STUDIO • LOCAL PREVIEW", (65, height - 55),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 220, 240), 1, cv2.LINE_AA)
            
            # Duration indicator
            cv2.putText(frame, f"{duration}s • {fps}fps • LOCAL FALLBACK", (width - 320, height - 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 170, 190), 1, cv2.LINE_AA)
        
        # Frame number indicator (subtle)
        cv2.putText(frame, f"frame {i+1}/{total_frames}", (10, 25),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.35, (100, 120, 140), 1, cv2.LINE_AA)
        
        writer.write(frame)
    
    writer.release()
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("Local video writer produced no output file.")
    logger.info(f"Local fallback video generated: {path.name} ({duration}s, {fps}fps, {codec_name})")
    return path

def format_production_copy(raw_chat_output, prompt):
    headline=_extract_brief_headline(prompt)
    if "##" in raw_chat_output or "Headline:" in raw_chat_output:
        return raw_chat_output
    return f"""# 🚀 Production Campaign Suite: {headline}

## 🎯 Campaign Positioning
- Premium quality, international appeal, and authoritative positioning.
- Modern, sophisticated, confident, conversion-focused tone.

## 📢 Headlines & Taglines
- **Primary:** "{headline} — Built for Distinction."
- **Secondary:** "Experience extraordinary quality, tailored to your expectations."

## 📱 Instagram & Facebook
**Caption:** ✨ Discover the new standard in excellence. {headline} delivers a premium campaign experience designed for attention and action.
**CTA:** Learn More
**Hashtags:** #{headline.replace(" ","")} #Campaign #Luxury #Innovation

## 💼 LinkedIn
**Headline:** Elevating standards through design, performance, and craftsmanship.
**Body:** {headline} brings together design, performance and a polished customer experience.

## 🔍 Google Ads
- Headline 1: {headline[:30]}
- Headline 2: Official Campaign Showcase
- Headline 3: Premium Quality & Style
- Description 1: Discover {headline}. Modern design, premium craftsmanship, and exceptional value.
- Description 2: Explore the campaign and take the next step today.

---
### Full Prompt Directives
{raw_chat_output}
"""

def _reference_paths(assets):
    paths=[]
    for asset in assets:
        if asset.get("type")=="image" and asset.get("path"):
            full=config.ROOT/asset["path"]
            if full.exists(): paths.append(full)
        elif asset.get("type")=="video" and asset.get("frame_paths"):
            for fp in asset["frame_paths"]:
                full=config.ROOT/fp
                if full.exists(): paths.append(full); break
    return paths

def generate(creative_type, prompt, model, reference_assets=None, settings=None, allow_local_fallback=True):
    settings = settings or {}
    # Unique per-call ID so parallel type generation never collides.
    job = f"job_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    provider = model.get("provider", "local")
    model_id = model.get("model") or model.get("id", "")
    refs = _reference_paths(reference_assets or [])
    is_local = provider in ("local", "fallback") or not (model_id or "")

    if is_local and creative_type in ("image", "video", "copy"):
        # Skip the remote API entirely: render locally for minimal cost/latency.
        if creative_type == "copy":
            path = config.OUTPUT_DIR / f"{job}_copy.txt"
            path.write_text(format_production_copy(f"Fallback campaign engine:\n\n{prompt}", prompt), encoding="utf-8")
            return {"filename": path.name, "model_used": "local-copy-fallback", "url": "/outputs/" + path.name}
        if creative_type == "image":
            img = cinematic_campaign_image(prompt, job, refs)
            return {"filename": img.name, "model_used": "local-image-fallback", "url": "/outputs/" + img.name}
        vid = cinematic_campaign_video(prompt, job, refs[0] if refs else None, settings)
        dur = int(settings.get("duration", config.VIDEO_SECONDS))
        return {"filename": vid.name, "model_used": "local-video-fallback", "url": "/outputs/" + vid.name, "duration": dur}

    if creative_type == "copy":
        try:
            instruction = f"""You are an elite Creative Director at a global marketing agency.
Create a production-ready multi-channel campaign from this brief and reference intelligence.

{prompt}

Include campaign positioning, 3 headlines/taglines, Instagram/Facebook copy, LinkedIn copy,
Google Ads (3 headlines <=30 chars and 2 descriptions <=90 chars), CTA, and audience/tone guidance.
Use clean Markdown."""
            content = chat(instruction, model=model_id, provider=provider)
            path = config.OUTPUT_DIR / f"{job}_copy.txt"
            path.write_text(format_production_copy(content, prompt), encoding="utf-8")
            return {"filename": path.name, "model_used": f"{provider} • {model_id}", "url": "/outputs/" + path.name}
        except Exception as e:
            if not allow_local_fallback:
                raise
            path = config.OUTPUT_DIR / f"{job}_copy.txt"
            path.write_text(format_production_copy(f"Fallback campaign engine:\n\n{prompt}", prompt), encoding="utf-8")
            return {"filename": path.name, "model_used": "local-copy-fallback", "url": "/outputs/" + path.name, "warning": str(e)}

    if creative_type == "image":
        path = config.OUTPUT_DIR / f"{job}_image.png"
        try:
            used = generate_image(provider, model_id, prompt, path, refs, size=settings.get("image_size") or config.DEFAULT_IMAGE_SIZE)
            return {"filename": path.name, "model_used": f"{provider} • {used}", "url": "/outputs/" + path.name}
        except Exception as e:
            if not allow_local_fallback:
                raise
            img = cinematic_campaign_image(prompt, job, refs)
            return {"filename": img.name, "model_used": "local-image-fallback", "url": "/outputs/" + img.name, "warning": str(e)}

    if creative_type == "video":
        path = config.OUTPUT_DIR / f"{job}_video.mp4"

        # Normalize and validate settings
        settings = dict(settings or {})
        try:
            if "duration" in settings:
                settings["duration"] = int(settings["duration"])
        except (TypeError, ValueError):
            settings["duration"] = config.VIDEO_SECONDS

        # Get reference path (first image or video frame)
        ref_path = refs[0] if refs else None

        logger.info(f"Starting video generation: provider={provider}, model={model_id}, duration={settings.get('duration')}s, reference={'yes' if ref_path else 'no'}")

        try:
            used, duration = generate_video(provider, model_id, prompt, path, ref_path, settings)
            logger.info(f"Video generation completed: {path.name} ({duration}s)")
            return {
                "filename": path.name,
                "model_used": f"{provider} • {used} • {duration}s",
                "url": "/outputs/" + path.name,
                "duration": duration,
            }
        except Exception as e:
            logger.error(f"Video generation failed (provider={provider}, model={model_id}): {e}")
            if not allow_local_fallback:
                raise

            # Local fallback with properly snapped duration
            safe_settings = dict(settings or {})
            try:
                safe_settings["duration"] = int(safe_settings.get("duration", config.VIDEO_SECONDS))
            except (TypeError, ValueError):
                safe_settings["duration"] = config.VIDEO_SECONDS

            # Snap duration for local fallback too (wider range)
            local_durations = [4, 5, 6, 8, 10]
            req_dur = safe_settings["duration"]
            safe_settings["duration"] = min(local_durations, key=lambda x: abs(x - req_dur))

            vid = cinematic_campaign_video(prompt, job, ref_path, safe_settings)
            logger.info(f"Local fallback video generated: {vid.name} ({safe_settings['duration']}s)")

            return {
                "filename": vid.name,
                "model_used": "local-video-fallback",
                "url": "/outputs/" + vid.name,
                "duration": safe_settings["duration"],
                "warning": f"Provider error (using local fallback): {str(e)[:200]}",
            }

    path = config.OUTPUT_DIR / f"{job}_{creative_type}.txt"
    path.write_text(prompt, encoding="utf-8")
    return {"filename": path.name, "model_used": "local", "url": "/outputs/" + path.name}
