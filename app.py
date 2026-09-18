import time,uuid,os,tempfile,threading
from flask import Flask,jsonify,request,render_template,send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from core import config, scraper, prompt_engine, model_router, generator, learning, ai
from core.store import load_json, save_json
if config.ENVIRONMENT == 'production' and (config.FLASK_DEBUG or not config.SECRET_KEY):
    raise RuntimeError('Production requires FLASK_DEBUG=0 and a non-empty SECRET_KEY.')
app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=config.MAX_CONTENT_LENGTH, SECRET_KEY=config.SECRET_KEY or 'development-only-not-for-production')
PROJECTS = {}
GENERATION_LOCK = threading.RLock()

@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(error): return jsonify(error=f'Upload exceeds the {config.MAX_CONTENT_LENGTH // 1024 // 1024} MB limit.'),413

@app.errorhandler(500)
def internal_error(error): return jsonify(error='Internal server error. Check the application logs for details.'),500

def log(p, msg): p.setdefault('history', []).append({'timestamp': time.time(), 'message': msg})
def persist(): save_json(config.PROJECT_STORE, {'projects': list(PROJECTS.values())})
def load():
    for p in load_json(config.PROJECT_STORE, {'projects': []}).get('projects', []): PROJECTS[p['id']] = p
load()
def getp(pid): return PROJECTS.get(pid)

@app.route('/')
def index(): return render_template('index.html', app_name=config.APP_NAME)
@app.route('/outputs/<path:filename>')
def outputs(filename): return send_from_directory(config.OUTPUT_DIR, filename)
@app.route('/reference/<pid>/<path:filename>')
def reference(pid, filename): return send_from_directory(config.REFERENCE_DIR / pid, filename)

@app.route('/api/health')
def health():
    auth_info = ai.check_auth()
    return jsonify({
        'ok': True,
        'app': config.APP_NAME,
        'environment': config.ENVIRONMENT,
        'openrouter_configured': bool(config.OPENROUTER_API_KEY),
        'openrouter': auth_info,
        'learning': learning.summary()
    })

@app.route('/api/learning')
def learn(): return jsonify(learning.summary())

@app.route('/api/projects', methods=['POST'])
def create():
    d = request.get_json(silent=True) or {}
    brief = (d.get('brief') or '').strip()
    types = d.get('creative_types') or ['image']
    if not brief: return jsonify(error='Brief is required.'), 400
    if not isinstance(types, list) or not types or not all(x in ('image', 'video', 'copy') for x in types):
        return jsonify(error='Invalid creative types.'), 400
    pid = uuid.uuid4().hex[:10]
    p = {
        'id': pid,
        'created_at': time.time(),
        'url': (d.get('url') or '').strip(),
        'brief': brief,
        'creative_types': types,
        'stage': 'created',
        'scraped': None,
        'prompt_bundle': None,
        'reviewed_prompts': {},
        'review_feedback': {},
        'verified': False,
        'chosen_models': {},
        'outputs': {},
        'final_review': {},
        'history': []
    }
    PROJECTS[pid] = p
    log(p, 'Project initialized.')
    persist()
    return jsonify(p), 201

@app.route('/api/projects/<pid>')
def project(pid):
    p = getp(pid)
    return (jsonify(p), 200) if p else (jsonify(error='Project not found'), 404)

@app.route('/api/projects/<pid>/scrape', methods=['POST'])
def scrape(pid):
    p = getp(pid)
    if not p: return jsonify(error='Project not found'), 404
    p['scraped'] = scraper.scrape_url(p['url'], pid) if p['url'] else scraper.scrape_url('', pid)
    if p['scraped'].get('error'): return jsonify(error=p['scraped']['error']), 502
    p['stage'] = 'scraped'
    log(p, f"Reference ingestion complete: {len(p['scraped'].get('assets', []))} downloadable assets.")
    persist()
    return jsonify(p)

@app.route('/api/projects/<pid>/upload-reference', methods=['POST'])
def upload_ref(pid):
    p = getp(pid)
    if not p: return jsonify(error='Project not found'), 404
    if 'file' not in request.files: return jsonify(error='No file uploaded.'), 400
    f = request.files['file']
    if not f.filename: return jsonify(error='Filename missing.'), 400
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    try:
        f.save(tmp.name)
        asset = scraper.upload_asset(tmp.name, pid, f.filename)
        p.setdefault('scraped', {'url': '', 'title': 'Uploaded references', 'text': '', 'images': [], 'videos': [], 'assets': [], 'error': None, 'visual_analysis': ''})
        p['scraped']['assets'].append(asset)
        p['scraped']['images'] = [a.get('url', '') for a in p['scraped']['assets'] if a.get('type') == 'image']
        p['scraped']['videos'] = [a.get('url', '') for a in p['scraped']['assets'] if a.get('type') == 'video']
        p['stage'] = 'scraped'
        log(p, f"Uploaded reference asset: {f.filename}")
        persist()
        return jsonify(p)
    except Exception as e:
        return jsonify(error=str(e)), 400
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass

@app.route('/api/projects/<pid>/prompts', methods=['POST'])
def prompts(pid):
    p = getp(pid)
    if not p or not p.get('scraped'): return jsonify(error='Analyze or upload references first.'), 400
    p['prompt_bundle'] = prompt_engine.build_prompts(p['scraped'], p['brief'], p['creative_types'])
    p['reviewed_prompts'] = dict(p['prompt_bundle']['prompts'])
    p['stage'] = 'prompts_generated'
    log(p, 'Prompts built from text + visual reference analysis + creative memory.')
    persist()
    return jsonify(p)

@app.route('/api/projects/<pid>/review', methods=['POST'])
def review(pid):
    p = getp(pid)
    if not p: return jsonify(error='Project not found'), 404
    d = request.get_json(silent=True) or {}
    edits = d.get('edited_prompts', {})
    feedback = d.get('feedback', {})
    for c in p['creative_types']:
        if isinstance(edits.get(c), str) and edits[c].strip():
            p['reviewed_prompts'][c] = edits[c].strip()
        if feedback.get(c):
            p['review_feedback'][c] = feedback[c]
            p['reviewed_prompts'][c] = prompt_engine.refine_with_llm(p['reviewed_prompts'][c], feedback[c], c)
    p['verified'] = True
    p['stage'] = 'reviewed'
    log(p, 'Prompt review saved and ready for generation.')
    persist()
    return jsonify(p)

def _generate_all(p, forced=None):
    forced = forced or {}
    results = {}
    for c in p['creative_types']:
        opts = {m['id']: m for m in model_router.available_models(c)}
        chosen = opts.get(forced.get(c)) if forced.get(c) in opts and opts[forced[c]]['usable'] else model_router.select_model(c, p['reviewed_prompts'].get(c, ''))
        p['chosen_models'][c] = chosen
        r = generator.generate(c, p['reviewed_prompts'].get(c, p['brief']), chosen, p['scraped'].get('assets', []) if p.get('scraped') else [])
        p['outputs'][c] = r
        results[c] = r
        log(p, f"Generated {c} with {r.get('model_used', 'engine')}.")
    p['stage'] = 'generated'
    persist()
    return results

def _run_generation(p, forced):
    try:
        _generate_all(p, forced)
        log(p, 'Campaign generation completed successfully.')
    except Exception as error:
        p['stage'] = 'generation_failed'
        p['generation_error'] = str(error)
        log(p, f'Generation failed: {type(error).__name__} - {error}')
        app.logger.exception('Generation failed for project %s', p['id'])
    finally:
        p['generation_running'] = False
        persist()

def _start_generation(p, forced):
    with GENERATION_LOCK:
        if p.get('generation_running'): return False
        p['generation_running'] = True
        p['generation_error'] = None
        p['stage'] = 'generating'
        log(p, 'Generation started in background.')
        persist()
        threading.Thread(target=_run_generation, args=(p, forced), daemon=True, name=f"generation-{p['id']}").start()
        return True

@app.route('/api/projects/<pid>/auto-run', methods=['POST'])
def auto_run(pid):
    """1-Click End-to-End Pipeline: Ingest -> Prompt -> Generate."""
    p = getp(pid)
    if not p: return jsonify(error='Project not found'), 404

    def _pipeline(proj):
        with GENERATION_LOCK:
            proj['generation_running'] = True
            proj['generation_error'] = None
            proj['stage'] = 'generating'
            log(proj, 'Step 1/3: Extracting reference intelligence...')
            persist()
        try:
            # 1. Ingest
            if not proj.get('scraped'):
                proj['scraped'] = scraper.scrape_url(proj['url'], proj['id']) if proj['url'] else scraper.scrape_url('', proj['id'])
                log(proj, f"Reference intelligence extracted ({len(proj['scraped'].get('assets', []))} assets).")
                persist()

            # 2. Prompts
            log(proj, 'Step 2/3: Synthesizing creative campaign prompts...')
            persist()
            proj['prompt_bundle'] = prompt_engine.build_prompts(proj['scraped'], proj['brief'], proj['creative_types'])
            proj['reviewed_prompts'] = dict(proj['prompt_bundle']['prompts'])
            proj['verified'] = True
            persist()

            # 3. Generate
            log(proj, 'Step 3/3: Synthesizing campaign creatives (Copy / Image / Video)...')
            persist()
            _generate_all(proj)
            log(proj, 'Full campaign launch complete! Ready for review.')
        except Exception as err:
            proj['stage'] = 'generation_failed'
            proj['generation_error'] = str(err)
            log(proj, f'Pipeline error: {err}')
            app.logger.exception('Auto-run failed for %s', proj['id'])
        finally:
            proj['generation_running'] = False
            persist()

    threading.Thread(target=_pipeline, args=(p,), daemon=True, name=f"autorun-{p['id']}").start()
    return jsonify(p), 202

@app.route('/api/projects/<pid>/verify', methods=['POST'])
def verify(pid):
    p = getp(pid)
    if not p: return jsonify(error='Project not found'), 404
    if not p.get('reviewed_prompts'):
        if p.get('scraped'):
            p['prompt_bundle'] = prompt_engine.build_prompts(p['scraped'], p['brief'], p['creative_types'])
            p['reviewed_prompts'] = dict(p['prompt_bundle']['prompts'])
        else:
            p['reviewed_prompts'] = {c: p['brief'] for c in p['creative_types']}
    d = request.get_json(silent=True) or {}
    p['verified'] = True
    log(p, 'Prompt verified. Starting generation.')
    _start_generation(p, d.get('models', {}))
    return jsonify(p), 202

@app.route('/api/projects/<pid>/generate', methods=['POST'])
def generate(pid):
    p = getp(pid)
    if not p: return jsonify(error='Project not found'), 404
    p['verified'] = True
    if not _start_generation(p, (request.get_json(silent=True) or {}).get('models', {})):
        return jsonify(error='Generation is already running.', project=p), 409
    return jsonify(p), 202
@app.route('/api/projects/<pid>/models')
def models(pid):
    p=getp(pid)
    if not p:return jsonify(error='Project not found'),404
    return jsonify({c:model_router.available_models(c) for c in p['creative_types']})
@app.route('/api/projects/<pid>/final-review',methods=['POST'])
def final_review(pid):
    p=getp(pid)
    if not p:return jsonify(error='Project not found'),404
    d=request.get_json(silent=True) or {}; decisions=d.get('decisions',{})
    for c,dec in decisions.items():
        if c not in p['outputs']:continue
        approved=bool(dec.get('approved')); notes=(dec.get('notes') or '').strip(); rating=dec.get('rating'); p['final_review'][c]={'approved':approved,'notes':notes,'rating':rating,'timestamp':time.time()}
        learning.record_review(p['id'],c,p['reviewed_prompts'].get(c,''),p['outputs'][c]['filename'],approved,notes,rating)
        if not approved and notes:
            p['reviewed_prompts'][c]=prompt_engine.refine_with_llm(p['reviewed_prompts'][c],notes,c); chosen=p['chosen_models'].get(c) or model_router.select_model(c); p['outputs'][c]=generator.generate(c,p['reviewed_prompts'][c],chosen,p['scraped'].get('assets',[])); log(p,f'Regenerated {c} using final-review corrections.')
    if p['outputs'] and set(p['outputs'])==set(p['final_review']) and all(x.get('approved') for x in p['final_review'].values()):p['stage']='final_reviewed'
    else:p['stage']='generated'
    persist();return jsonify(p)
if __name__=='__main__':
    app.run(host=config.HOST,port=config.PORT,debug=config.FLASK_DEBUG)
