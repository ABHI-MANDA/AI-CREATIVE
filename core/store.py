import json, os, tempfile, threading
from pathlib import Path
_LOCK=threading.RLock()
def load_json(path, default):
    path=Path(path)
    with _LOCK:
        try:
            if not path.exists(): return default
            with path.open('r',encoding='utf-8') as f: return json.load(f)
        except (OSError,json.JSONDecodeError): return default
def save_json(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with _LOCK:
        fd,tmp=tempfile.mkstemp(prefix='.tmp_',dir=str(path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
