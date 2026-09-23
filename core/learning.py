import time,re
from . import config
from .store import load_persistent_collection,save_persistent_collection

def clean(s,n=3000): return re.sub(r'\s+',' ',str(s or '')).strip()[:n]
def record_review(project_id,ctype,prompt,output_filename,approved,notes='',rating=None):
    d=load_persistent_collection("reviews", config.REVIEW_STORE,{'reviews':[]})
    r={'id':f'rev_{int(time.time()*1000)}','timestamp':time.time(),'project_id':project_id,'creative_type':ctype,'prompt':clean(prompt,6000),'output':output_filename,'approved':bool(approved),'rating':rating,'notes':clean(notes)}
    d.setdefault('reviews',[]).append(r); d['reviews']=d['reviews'][-config.MAX_REVIEW_MEMORY*3:]; save_persistent_collection("reviews", config.REVIEW_STORE,d); _memory(r); return r
def _memory(r):
    m=load_persistent_collection("creative_memory", config.MEMORY_STORE,{'lessons':[],'stats':{'reviews':0,'approved':0,'rejected':0}})
    s=m.setdefault('stats',{'reviews':0,'approved':0,'rejected':0}); s['reviews']+=1; s['approved' if r['approved'] else 'rejected']+=1
    if r['notes']:
        m.setdefault('lessons',[]).append({'timestamp':r['timestamp'],'creative_type':r['creative_type'],'approved':r['approved'],'rating':r['rating'],'lesson':r['notes']})
        m['lessons']=m['lessons'][-config.MAX_REVIEW_MEMORY:]
    save_persistent_collection("creative_memory", config.MEMORY_STORE,m)
def get_context(ctype,limit=8):
    m=load_persistent_collection("creative_memory", config.MEMORY_STORE,{'lessons':[]}); rows=[x for x in m.get('lessons',[]) if x.get('creative_type') in (ctype,'all')][-limit:]
    if not rows:return ''
    return 'LEARNED REVIEW LESSONS:\n'+'\n'.join(f"- [{'APPROVED' if x.get('approved') else 'CORRECTION'}] {x.get('lesson','')}" for x in rows)
def summary(): return load_persistent_collection("creative_memory", config.MEMORY_STORE,{'lessons':[],'stats':{'reviews':0,'approved':0,'rejected':0}})
