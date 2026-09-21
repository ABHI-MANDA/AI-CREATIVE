import os, time, textwrap, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import cv2, numpy as np
from . import config
from .ai import chat, generate_image, generate_video, ProviderError

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
    settings=settings or {}
    width,height=1280,720
    fps=config.VIDEO_FPS
    duration=int(settings.get("duration", config.VIDEO_SECONDS))
    total_frames=max(1,fps*duration)
    path=config.OUTPUT_DIR/f"{job}_video.mp4"
    headline=_extract_brief_headline(prompt)
    if base_image_path and os.path.exists(base_image_path):
        src_img=Image.open(base_image_path).convert("RGB")
    else:
        src_img=Image.open(cinematic_campaign_image(prompt,f"{job}_tmp")).convert("RGB")
    src_w,src_h=src_img.size; target_aspect=width/height; current=src_w/max(1,src_h)
    if current>target_aspect:
        new_w=int(src_h*target_aspect); left=(src_w-new_w)//2; src_img=src_img.crop((left,0,left+new_w,src_h))
    else:
        new_h=int(src_w/target_aspect); top=(src_h-new_h)//2; src_img=src_img.crop((0,top,src_w,top+new_h))
    src_img=src_img.resize((int(width*1.25),int(height*1.25)),Image.Resampling.LANCZOS)
    src_np=np.asarray(src_img)
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*"mp4v"),fps,(width,height))
    max_x,max_y=src_img.width-width,src_img.height-height
    for i in range(total_frames):
        t=i/max(1,total_frames-1); scale=1.0+0.12*math.sin(t*math.pi*0.5)
        crop_w,crop_h=int(width/scale),int(height/scale)
        cx=min(src_img.width-crop_w,max(0,int((max_x*0.5)*t)))
        cy=min(src_img.height-crop_h,max(0,int((max_y*0.4)*t)))
        frame=cv2.resize(src_np[cy:cy+crop_h,cx:cx+crop_w],(width,height),interpolation=cv2.INTER_LINEAR)
        frame=cv2.cvtColor(frame,cv2.COLOR_RGB2BGR)
        frame=cv2.convertScaleAbs(frame,alpha=0.85+0.15*math.sin(t*math.pi),beta=3)
        fade=min(1.0,i/max(1,fps*0.8))
        if fade>0.1:
            overlay=frame.copy()
            cv2.rectangle(overlay,(40,height-130),(width-40,height-40),(10,15,25),-1)
            cv2.addWeighted(overlay,0.75*fade,frame,1-(0.75*fade),0,frame)
            cv2.rectangle(frame,(40,height-130),(48,height-40),(248,189,56),-1)
            cv2.putText(frame,headline[:45],(65,height-88),cv2.FONT_HERSHEY_DUPLEX,0.85,(255,255,255),2,cv2.LINE_AA)
            cv2.putText(frame,"CAMPAIGN VIDEO SPOTLIGHT • HIGH IMPACT",(65,height-58),cv2.FONT_HERSHEY_SIMPLEX,0.5,(200,220,240),1,cv2.LINE_AA)
        writer.write(frame)
    writer.release(); return path

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

def generate(creative_type,prompt,model,reference_assets=None,settings=None):
    settings=settings or {}
    job=f"job_{int(time.time()*1000)}"
    provider=model.get("provider","local")
    model_id=model.get("model") or model.get("id","")
    refs=_reference_paths(reference_assets or [])

    if creative_type=="copy":
        try:
            instruction=f"""You are an elite Creative Director at a global marketing agency.
Create a production-ready multi-channel campaign from this brief and reference intelligence.

{prompt}

Include campaign positioning, 3 headlines/taglines, Instagram/Facebook copy, LinkedIn copy,
Google Ads (3 headlines <=30 chars and 2 descriptions <=90 chars), CTA, and audience/tone guidance.
Use clean Markdown."""
            content=chat(instruction, model=model_id, provider=provider)
            path=config.OUTPUT_DIR/f"{job}_copy.txt"; path.write_text(format_production_copy(content,prompt),encoding="utf-8")
            return {"filename":path.name,"model_used":f"{provider} • {model_id}","url":"/outputs/"+path.name}
        except Exception as e:
            path=config.OUTPUT_DIR/f"{job}_copy.txt"; path.write_text(format_production_copy(f"Fallback campaign engine:\n\n{prompt}",prompt),encoding="utf-8")
            return {"filename":path.name,"model_used":"local-copy-fallback","url":"/outputs/"+path.name,"warning":str(e)}

    if creative_type=="image":
        path=config.OUTPUT_DIR/f"{job}_image.png"
        try:
            used=generate_image(provider,model_id,prompt,path,refs,size=settings.get("image_size") or config.DEFAULT_IMAGE_SIZE)
            return {"filename":path.name,"model_used":f"{provider} • {used}","url":"/outputs/"+path.name}
        except Exception as e:
            img=cinematic_campaign_image(prompt,job,refs)
            return {"filename":img.name,"model_used":"local-image-fallback","url":"/outputs/"+img.name,"warning":str(e)}

    if creative_type=="video":
        path=config.OUTPUT_DIR/f"{job}_video.mp4"
        # Normalize duration once more so local fallback and hosted paths stay in sync
        try:
            settings = dict(settings or {})
            if "duration" in settings:
                settings["duration"] = int(settings["duration"])
        except Exception:
            settings = settings or {}
        try:
            used,duration=generate_video(provider,model_id,prompt,path,refs[0] if refs else None,settings)
            return {"filename":path.name,"model_used":f"{provider} • {used} • {duration}s","url":"/outputs/"+path.name,"duration":duration}
        except Exception as e:
            # Local motion fallback always accepts the requested (or snapped) duration
            safe_settings = dict(settings or {})
            try:
                safe_settings["duration"] = int(safe_settings.get("duration", config.VIDEO_SECONDS))
            except Exception:
                safe_settings["duration"] = config.VIDEO_SECONDS
            vid=cinematic_campaign_video(prompt,job,refs[0] if refs else None,safe_settings)
            return {"filename":vid.name,"model_used":"local-video-fallback","url":"/outputs/"+vid.name,"duration":safe_settings["duration"],"warning":str(e)}

    path=config.OUTPUT_DIR/f"{job}_{creative_type}.txt"; path.write_text(prompt,encoding="utf-8")
    return {"filename":path.name,"model_used":"local","url":"/outputs/"+path.name}
