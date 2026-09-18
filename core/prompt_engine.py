import re
from collections import Counter
from .ai import chat
from .learning import get_context
from . import config
STOPWORDS=set('the a an and or but of to in on for with at by from is are was were this that these those it its as be been being will would can could should may might do does did not no yes we you they he she i our your their his her them us'.split())
def top_keywords(text,n=15):
    words=re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}',text.lower()); c=Counter(x for x in words if x not in STOPWORDS); return [w for w,_ in c.most_common(n)]
def asset_summary(scraped):
    rows=[]
    for a in scraped.get('assets',[]):
        if a.get('error'): continue
        s=a.get('signals',{}); rows.append(f"{a['type']}: {s}")
    return '\n'.join(rows[:20])
def analyze_visual_references(scraped):
    paths=[]
    for a in scraped.get('assets',[]):
        if a.get('type')=='image' and a.get('path'): paths.append(config.ROOT/a['path'])
        for fp in a.get('frame_paths',[]): paths.append(config.ROOT/fp)
    paths=[p for p in paths if p.exists()][:3]
    if not paths or not config.OPENROUTER_API_KEY: return ''
    try:
        return chat('''Analyze these key visual references for advertising production. Summarize:
1. Palette and color temperature
2. Composition, lighting, and mood
3. Texture, materials, and stylistic cues
Keep concise and actionable for creative synthesis.''', config.OPENROUTER_VISION_MODEL, paths)
    except Exception as e: return f'Visual reference summary: {e}'
def build_prompts(scraped,brief,creative_types):
    visual=analyze_visual_references(scraped); kws=top_keywords(scraped.get('text',''))
    common=f'''Create production-ready {', '.join(creative_types)} for this brief:\n{brief}\n\nREFERENCE TITLE: {scraped.get('title','Unknown')}\nREFERENCE TEXT SIGNALS: {', '.join(kws) or 'none'}\nASSET SUMMARY:\n{asset_summary(scraped) or 'No downloadable visual assets'}\nVISUAL REFERENCE ANALYSIS:\n{visual or 'Use structural signals from available reference assets.'}\n\nQUALITY CONTRACT:\n- professional, realistic, coherent and physically plausible\n- preserve important reference composition/style cues without copying protected logos/text unless explicitly requested\n- accurate perspective, scale, lighting, materials and anatomy\n- no random watermark or malformed text\n- satisfy every explicit constraint in the brief\n'''
    prompts={}
    for c in creative_types:
        learned=get_context(c); extra=''
        if c=='image': extra='Return one detailed image-generation prompt covering subject, composition, camera/lens, lighting, environment, materials, color, mood, negative constraints and reference-image guidance.'
        elif c=='video': extra='Return a production video prompt covering duration, aspect ratio, shot structure, camera movement, subject motion, temporal continuity, lighting, reference-frame usage, safety/frame containment and negative constraints.'
        else: extra='Return a professional copy prompt covering audience, tone, structure, CTA, claims and factual constraints.'
        prompts[c]=(common+'\nCREATIVE TYPE: '+c+'\n'+extra+'\n'+learned)[:config.MAX_PROMPT_CHARS]
    return {'keywords':kws,'visual_analysis':visual,'prompts':prompts,'asset_count':len(scraped.get('assets',[]))}
def refine_with_llm(prompt,feedback,ctype='image'):
    inst=f'''You are a senior creative director. Rewrite ONLY the production prompt. Preserve the original brief and all constraints. Apply reviewer corrections precisely, then improve realism, composition, continuity and model-specific specificity.\nTYPE: {ctype}\nPROMPT:\n{prompt}\nREVIEW:\n{feedback}\n{get_context(ctype)}'''
    try:
        x=chat(inst)
        if x:return x[:config.MAX_PROMPT_CHARS]
    except Exception: pass
    return (prompt+'\nMANDATORY REVIEW CORRECTIONS:\n'+feedback).strip()[:config.MAX_PROMPT_CHARS]
