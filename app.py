"""
app.py — Flask + Socket.IO backend for AI Creative Studio.

Implements the reference-aware creative pipeline as real API endpoints:

  brief/URL/upload -> reference ingestion (HTML/images/videos + frames)
      -> visual + metadata analysis -> prompt engineering
      -> human review -> verify & generate (best-model routing)
      -> final review -> approved: memory | rejected: refine + regenerate

State lives in PROJECTS (in-memory) and is persisted atomically to
data/projects.json. Generation runs in background threads with live
Socket.IO progress events (polling fallback in the frontend).
"""

import os
import sys
import time
import uuid
import threading
import logging
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, request, render_template, send_from_directory, abort
from flask_socketio import SocketIO, join_room, leave_room

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from core import config, scraper, prompt_engine, model_router, generator, learning, ai
from core.store import load_persistent_collection, save_persistent_collection
from core import database, storage, evaluator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY or uuid.uuid4().hex
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
app.config["SESSION_COOKIE_HTTPONLY"] = config.SESSION_COOKIE_HTTPONLY
app.config["SESSION_COOKIE_SAMESITE"] = config.SESSION_COOKIE_SAMESITE
if config.ENVIRONMENT == "production":
    app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

PROJECTS = {}
PROJECTS_LOCK = threading.RLock()
_STATE_LOCK = threading.RLock()
_START_TIME = time.time()

VALID_TYPES = ("copy", "image", "video")

STAGE_INDEX = {
    "created": 0,
    "scraping": 1,
    "scraped": 1,
    "analyzing": 2,
    "prompting": 2,
    "prompts_generated": 2,
    "reviewed": 3,
    "verified": 4,
    "generating": 4,
    "generated": 5,
    "final_reviewed": 6,
    "generation_failed": 4,
}

_rate_lock = threading.Lock()
_rate_hits = {}


# ---------------------------------------------------------------------------
# Persistence & helpers
# ---------------------------------------------------------------------------

def _load_projects():
    data = load_persistent_collection("projects", config.PROJECT_STORE, {"projects": []})
    for p in data.get("projects", []):
        if not isinstance(p, dict) or not p.get("id"):
            continue
        p["generation_running"] = False
        if p.get("stage") == "generating":
            if p.get("outputs"):
                p["stage"] = "generated"
            elif p.get("verified") and p.get("reviewed_prompts"):
                p["stage"] = "verified"
            elif p.get("reviewed_prompts"):
                p["stage"] = "reviewed"
            elif p.get("scraped"):
                p["stage"] = "scraped"
            else:
                p["stage"] = "created"
        p.setdefault("history", [])
        p.setdefault("outputs", {})
        p.setdefault("final_review", {})
        p.setdefault("chosen_models", {})
        p.setdefault("review_feedback", {})
        p.setdefault("creative_settings", {})
        PROJECTS[p["id"]] = p


def _persist():
    with PROJECTS_LOCK:
        save_persistent_collection("projects", config.PROJECT_STORE, {"projects": list(PROJECTS.values())})


def _log(p, message):
    p.setdefault("history", []).append({"timestamp": time.time(), "message": message})
    if len(p["history"]) > 500:
        del p["history"][:-500]


def _emit(pid, stage=None, message=None):
    payload = {"project_id": pid, "timestamp": time.time()}
    if stage:
        payload["stage"] = stage
    if message:
        payload["message"] = message
    try:
        socketio.emit("progress", payload, to=pid)
    except Exception:
        logger.debug("progress emit failed", exc_info=True)


def _set_stage(p, stage, message=None):
    with _STATE_LOCK:
        p["stage"] = stage
        if message:
            _log(p, message)
        _persist()
    _emit(p["id"], stage, message)


def _get(pid):
    return PROJECTS.get(pid)


def _empty_scraped(title="Uploaded references"):
    return {
        "url": "",
        "title": title,
        "text": "",
        "images": [],
        "videos": [],
        "assets": [],
        "visual_analysis": "",
        "error": None,
    }


def _new_project(url, brief, creative_types, creative_settings=None):
    pid = uuid.uuid4().hex[:10]
    p = {
        "id": pid,
        "created_at": time.time(),
        "url": (url or "").strip(),
        "brief": brief,
        "creative_types": creative_types,
        "creative_settings": creative_settings or {},
        "stage": "created",
        "scraped": None,
        "prompt_bundle": None,
        "reviewed_prompts": {},
        "review_feedback": {},
        "verified": False,
        "chosen_models": {},
        "outputs": {},
        "final_review": {},
        "generation_running": False,
        "generation_error": None,
        "history": [],
    }
    PROJECTS[pid] = p
    return p


# ---------------------------------------------------------------------------
# Pipeline stage implementations
# ---------------------------------------------------------------------------

def ingest_project(p):
    """Stage: reference ingestion — scrape URL (HTML/images/videos + frames)
    while preserving any previously uploaded assets."""
    prior = p.get("scraped")
    prior_assets = list(prior.get("assets", [])) if isinstance(prior, dict) else []
    uploads = [a for a in prior_assets if not a.get("url")]

    if p.get("url"):
        scraped = scraper.scrape_url(p["url"], p["id"])
        if not isinstance(scraped, dict):
            scraped = _empty_scraped()
        scraped_assets = scraped.get("assets") or []
        scraped["assets"] = scraped_assets + uploads
        scraped["images"] = [
            a.get("url") for a in scraped["assets"]
            if a.get("type") == "image" and a.get("url")
        ]
        scraped["videos"] = [
            a.get("url") for a in scraped["assets"]
            if a.get("type") == "video" and a.get("url")
        ]
        p["scraped"] = scraped
        if scraped.get("error"):
            _log(p, f"Scrape warning: {scraped['error']}")
    else:
        if not isinstance(prior, dict):
            prior = _empty_scraped()
        p["scraped"] = prior

    with _STATE_LOCK:
        p["stage"] = "scraped"
        sc = p["scraped"] or {}
        n_img = sum(1 for a in sc.get("assets", []) if a.get("type") == "image" and not a.get("error"))
        n_vid = sum(1 for a in sc.get("assets", []) if a.get("type") == "video" and not a.get("error"))
        _log(p, f"Reference ingestion complete ({n_img} images, {n_vid} videos/frames).")
        _persist()
    return p


def synthesize_project_prompts(p, settings=None):
    """Stage: visual + metadata analysis and prompt engineering."""
    if not isinstance(p.get("scraped"), dict):
        ingest_project(p)

    merged = dict(p.get("creative_settings") or {})
    if settings:
        for k, v in settings.items():
            if isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
            else:
                merged[k] = v

    with _STATE_LOCK:
        p["creative_settings"] = merged

    bundle = prompt_engine.build_prompts(
        p["scraped"], p["brief"], p["creative_types"], merged
    )
    with _STATE_LOCK:
        p["prompt_bundle"] = bundle
        p["reviewed_prompts"] = dict(bundle.get("prompts") or {})
        p["verified"] = False
        p["stage"] = "prompts_generated"
        _log(p, "Visual analysis complete — production prompts synthesized.")
        _persist()
    return p


def _model_candidates(p, ctype, forced_models=None):
    mid = (forced_models or {}).get(ctype)
    cands = model_router.model_candidates(ctype, forced_id=mid)
    if not cands and mid:
        cands = model_router.model_candidates(ctype)
    return cands


def generate_project_outputs(p, forced_models=None, settings=None, only_types=None):
    """Stage: best-model selection + generation (parallel across creative types)."""
    forced_models = forced_models or {}
    settings = settings or p.get("creative_settings") or {}

    prompts = p.get("reviewed_prompts") or {}
    types = [c for c in p["creative_types"] if prompts.get(c)]
    if only_types:
        types = [c for c in types if c in only_types]
    if not types:
        raise RuntimeError("No prompts available for generation.")

    with _STATE_LOCK:
        p["generation_running"] = True
        p["generation_error"] = None
        p["verified"] = True
        p["stage"] = "generating"
        _log(p, f"Generating deliverables: {', '.join(types)}")
        _persist()
    _emit(p["id"], "generating", f"Rendering {', '.join(types)} with the selected engines…")

    results = {}
    errors = {}

    def work(ctype):
        candidates = _model_candidates(p, ctype, forced_models)
        if not candidates:
            raise RuntimeError(f"No usable model configured for {ctype}.")
        refs = (p.get("scraped") or {}).get("assets") or []
        warnings = []
        last_err = None
        # Try hosted models in order; local is always last in the candidate list.
        for i, chosen in enumerate(candidates):
            is_last = i == len(candidates) - 1
            try:
                result = generator.generate(
                    ctype,
                    prompts[ctype],
                    chosen,
                    refs,
                    settings.get(ctype) or {},
                    allow_local_fallback=is_last,
                )
                if result.get("warning"):
                    warnings.append(f"{chosen.get('id')}: {result['warning']}")
                    result["warning"] = "; ".join(warnings)
                return ctype, chosen, result
            except Exception as exc:
                last_err = str(exc)
                warnings.append(f"{chosen.get('id')}: {exc}")
                continue
        raise RuntimeError(
            f"All engines failed for {ctype}: {last_err or 'unknown error'}"
        )

    workers = max(1, min(3, len(types)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, c): c for c in types}
        for fut in list(futures):
            ctype = futures[fut]
            try:
                ctype, chosen, result = fut.result()
                if result.get("filename"):
                    local_path = config.OUTPUT_DIR / result["filename"]
                    remote_url = storage.persist_output(local_path, p["id"], ctype)
                    if remote_url:
                        result["storage_url"] = remote_url
                        result["local_url"] = result.get("url")
                        result["url"] = remote_url
                if result.get("filename"):
                    local_path = config.OUTPUT_DIR / result["filename"]
                    result["evaluation"] = evaluator.evaluate_output(local_path, ctype, prompts.get(ctype, ""))
                with _STATE_LOCK:
                    p.setdefault("chosen_models", {})[ctype] = chosen
                    p.setdefault("outputs", {})[ctype] = result
                    _log(
                        p,
                        f"Generated {ctype} via {result.get('model_used', chosen.get('id'))}"
                        f" → {result.get('filename')}",
                    )
                    _persist()
                results[ctype] = result
                _emit(p["id"], None, f"{ctype.title()} deliverable ready: {result.get('filename')}")
            except Exception as exc:
                logger.exception("Generation failed for %s", ctype)
                errors[ctype] = str(exc)
                with _STATE_LOCK:
                    _log(p, f"{ctype} generation failed: {exc}")
                    _persist()
                _emit(p["id"], None, f"{ctype.title()} failed: {exc}")

    with _STATE_LOCK:
        p["generation_running"] = False
        if errors and not results:
            p["stage"] = "generation_failed"
            p["generation_error"] = "; ".join(f"{k}: {v}" for k, v in errors.items())
            _log(p, f"Generation failed: {p['generation_error']}")
        elif errors:
            p["stage"] = "generated"
            p["generation_error"] = "; ".join(f"{k}: {v}" for k, v in errors.items())
            _log(p, f"Generation finished with partial errors: {p['generation_error']}")
        else:
            p["stage"] = "generated"
            p["generation_error"] = None
            _log(p, "All deliverables generated successfully.")
        _persist()

    if p["stage"] == "generated":
        _emit(p["id"], "generated", "Generation complete — review your deliverables.")
    else:
        _emit(p["id"], p["stage"], p.get("generation_error") or "Generation failed.")
    return p


def _generate_worker(pid, forced_models, settings):
    p = _get(pid)
    if not p:
        return
    try:
        generate_project_outputs(p, forced_models, settings)
    except Exception as exc:
        logger.exception("Generation worker failed for %s", pid)
        with _STATE_LOCK:
            p["stage"] = "generation_failed"
            p["generation_error"] = str(exc)
            p["generation_running"] = False
            _log(p, f"Generation failed: {exc}")
            _persist()
        _emit(pid, "generation_failed", f"Generation failed: {exc}")


def _pipeline_worker(pid, forced_models, settings):
    """Full auto pipeline: ingest -> analyze/prompt -> verify -> generate."""
    p = _get(pid)
    if not p:
        return
    try:
        with _STATE_LOCK:
            p["generation_running"] = True
            p["generation_error"] = None
            p["stage"] = "scraping"
            _log(p, "Full campaign pipeline started.")
            _persist()
        _emit(pid, "scraping", "Step 1/4 — Ingesting references (HTML, images, videos, frames)…")

        ingest_project(p)
        n_assets = len((p.get("scraped") or {}).get("assets") or [])
        _emit(pid, "scraped", f"References ingested ({n_assets} assets). Analyzing visuals…")

        with _STATE_LOCK:
            p["stage"] = "analyzing"
            _persist()
        _emit(pid, "analyzing", "Step 2/4 — Visual + metadata analysis, prompt engineering…")

        synthesize_project_prompts(p, settings)
        _emit(pid, "prompts_generated", "Step 3/4 — Prompts verified. Generating deliverables…")

        with _STATE_LOCK:
            p["verified"] = True
            p["stage"] = "generating"
            _persist()

        generate_project_outputs(p, forced_models, settings)
    except Exception as exc:
        logger.exception("Pipeline failed for %s", pid)
        with _STATE_LOCK:
            p["stage"] = "generation_failed"
            p["generation_error"] = str(exc)
            p["generation_running"] = False
            _log(p, f"Pipeline failed: {exc}")
            _persist()
        _emit(pid, "generation_failed", f"Pipeline failed: {exc}")


def _regen_worker(pid, rejected):
    """Refine rejected prompts from correction notes and regenerate those types."""
    p = _get(pid)
    if not p:
        return
    try:
        pending = [
            (ctype, notes)
            for ctype, notes in rejected
            if notes and ctype in (p.get("reviewed_prompts") or {})
        ]
        if pending:
            results = prompt_engine.refine_many(
                [(p["reviewed_prompts"][c], notes, c) for c, notes in pending],
                timeout=45,
            )
            with _STATE_LOCK:
                for (ctype, _), new_prompt in zip(pending, results):
                    p["reviewed_prompts"][ctype] = new_prompt
                    _log(p, f"Refined {ctype} prompt from correction notes.")
                _persist()
        settings = p.get("creative_settings") or {}
        generate_project_outputs(
            p, None, settings, only_types=[t for t, _ in rejected]
        )
        if p.get("stage") == "generated":
            with _STATE_LOCK:
                _log(p, "Regenerated deliverables ready for re-review.")
                _persist()
            _emit(pid, "generated", "Corrections applied — review the updated deliverables.")
    except Exception as exc:
        logger.exception("Regeneration failed for %s", pid)
        with _STATE_LOCK:
            p["stage"] = "generation_failed"
            p["generation_error"] = str(exc)
            p["generation_running"] = False
            _log(p, f"Regeneration failed: {exc}")
            _persist()
        _emit(pid, "generation_failed", f"Regeneration failed: {exc}")


# ---------------------------------------------------------------------------
# Security middleware
# ---------------------------------------------------------------------------

@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-XSS-Protection", "1; mode=block")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    return resp


@app.before_request
def _rate_limit():
    if not request.path.startswith("/api/"):
        return None
    ip = request.remote_addr or "unknown"
    now = time.time()
    with _rate_lock:
        hits = _rate_hits.setdefault(ip, deque())
        window = config.RATE_LIMIT_WINDOW
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= config.RATE_LIMIT_MAX_REQUESTS:
            return jsonify({"error": "Rate limit exceeded."}), 429
        hits.append(now)
    return None


# ---------------------------------------------------------------------------
# Frontend routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", app_name=config.APP_NAME)


@app.route("/outputs/<path:filename>")
def serve_output(filename):
    return send_from_directory(config.OUTPUT_DIR, filename)


@app.route("/reference/<pid>/<path:filename>")
def serve_reference(pid, filename):
    root = config.REFERENCE_DIR.resolve()
    try:
        base = (config.REFERENCE_DIR / pid).resolve()
    except OSError:
        abort(404)
    if not str(base).startswith(str(root)) or not base.is_dir():
        abort(404)

    direct = base / filename
    try:
        resolved = direct.resolve()
        if resolved.is_file() and str(resolved).startswith(str(base)):
            return send_from_directory(base, filename)
    except OSError:
        pass

    name = Path(filename).name
    if name not in ("", ".", ".."):
        for match in base.rglob(name):
            try:
                if match.is_file() and str(match.resolve()).startswith(str(base)):
                    return send_from_directory(match.parent, match.name)
            except OSError:
                continue
    abort(404)


# ---------------------------------------------------------------------------
# API: system
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "app": config.APP_NAME,
        "environment": config.ENVIRONMENT,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "projects": len(PROJECTS),
        "generating": sum(1 for p in PROJECTS.values() if p.get("generation_running")),
        "providers": ai.provider_status(),
        "persistence": {
            "backend": "postgresql" if database.enabled() else "json",
            "healthy": database.ping() if database.enabled() else True,
        },
        "object_storage": {
            "enabled": storage.enabled(),
            "bucket": config.SUPABASE_STORAGE_BUCKET if storage.enabled() else None,
        },
    })


@app.route("/api/models")
def all_models():
    return jsonify({
        ctype: model_router.available_models(ctype) for ctype in VALID_TYPES
    })


@app.route("/api/settings/providers", methods=["GET", "POST"])
def provider_settings():
    """Read or update in-memory provider keys entered from the UI.

    Keys are deliberately not persisted and are never returned to the client.
    This endpoint is suitable for the initial internal Render deployment; use
    Render environment secrets for a multi-user production deployment.
    """
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        updates = data.get("providers")
        if not isinstance(updates, dict):
            return jsonify({"error": "'providers' must be an object."}), 400

        invalid = [name for name in updates if name not in ("openrouter", "agnes")]
        if invalid:
            return jsonify({"error": f"Unsupported provider(s): {', '.join(invalid)}."}), 400

        for provider, value in updates.items():
            if not isinstance(value, str):
                return jsonify({"error": f"{provider} API key must be text."}), 400
            if value.strip() and len(value.strip()) < 8:
                return jsonify({"error": f"{provider} API key is too short."}), 400
            config.set_runtime_api_key(provider, value)

    return jsonify({"providers": config.provider_key_status()})


@app.route("/api/learning")
def learning_summary():
    return jsonify(learning.summary())


# ---------------------------------------------------------------------------
# API: project lifecycle
# ---------------------------------------------------------------------------

@app.route("/api/projects", methods=["GET"])
def list_projects():
    rows = [
        {
            "id": p["id"],
            "url": p.get("url", ""),
            "brief": p.get("brief", ""),
            "stage": p.get("stage", "created"),
            "created_at": p.get("created_at"),
        }
        for p in PROJECTS.values()
    ]
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return jsonify(rows)


@app.route("/api/projects", methods=["POST"])
def create_project():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    brief = (data.get("brief") or "").strip()
    creative_types = data.get("creative_types")
    if creative_types is None:
        creative_types = ["image"]
    if not isinstance(creative_types, list) or not creative_types:
        return jsonify({"error": "'creative_types' must be a non-empty list."}), 400
    creative_types = [str(t).strip().lower() for t in creative_types]
    invalid = [t for t in creative_types if t not in VALID_TYPES]
    if invalid:
        return jsonify({
            "error": f"Invalid creative type(s): {', '.join(invalid)}. "
                     f"Allowed: {', '.join(VALID_TYPES)}."
        }), 400

    settings = data.get("creative_settings")
    if settings is not None and not isinstance(settings, dict):
        return jsonify({"error": "'creative_settings' must be an object."}), 400

    if not brief:
        return jsonify({"error": "A campaign brief is required."}), 400

    p = _new_project(url, brief, creative_types, settings)
    with _STATE_LOCK:
        _log(p, f"Project created for {len(creative_types)} creative type(s).")
        _persist()
    return jsonify(p), 201


@app.route("/api/projects/<pid>", methods=["GET"])
def get_project(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    return jsonify(p)


@app.route("/api/projects/<pid>/models", methods=["GET"])
def project_models(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    return jsonify({
        ctype: model_router.available_models(ctype)
        for ctype in p.get("creative_types", [])
    })


# ---------------------------------------------------------------------------
# API: pipeline stages
# ---------------------------------------------------------------------------

@app.route("/api/projects/<pid>/scrape", methods=["POST"])
def scrape_stage(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    if p.get("generation_running"):
        return jsonify({"error": "A pipeline run is already in progress."}), 409

    _set_stage(p, "scraping", "Ingesting references from URL…")
    ingest_project(p)
    _emit(pid, "scraped", "Reference ingestion complete.")
    return jsonify(p)


@app.route("/api/projects/<pid>/upload-reference", methods=["POST"])
def upload_reference(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "Empty file."}), 400

    suffix = Path(f.filename).suffix.lower()
    allowed = scraper.IMAGE_EXT + scraper.VIDEO_EXT
    if suffix not in allowed:
        return jsonify({
            "error": "Unsupported file type. Use JPG, PNG, WEBP, GIF, BMP, "
                     "MP4, MOV, WEBM, M4V, AVI or MKV."
        }), 400

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name
        asset = scraper.upload_asset(tmp_path, p["id"], f.filename)
        stored_path = Path(config.ROOT / asset.get("path", "")) if asset.get("path") else None
        if stored_path and stored_path.exists():
            remote_url = storage.persist_reference(stored_path, p["id"], f.filename)
            if remote_url:
                asset["storage_url"] = remote_url
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Reference upload failed")
        return jsonify({"error": f"Upload failed: {exc}"}), 500
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    with _STATE_LOCK:
        if not isinstance(p.get("scraped"), dict):
            p["scraped"] = _empty_scraped()
        p["scraped"].setdefault("assets", []).append(asset)
        _log(p, f"Uploaded reference: {f.filename}")
        _persist()
    return jsonify(p)


@app.route("/api/projects/<pid>/prompts", methods=["POST"])
def prompts_stage(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    if p.get("generation_running"):
        return jsonify({"error": "A pipeline run is already in progress."}), 409
    if not isinstance(p.get("scraped"), dict):
        return jsonify({"error": "Run the scrape/ingest stage first."}), 400

    data = request.get_json(force=True, silent=True) or {}
    settings = data.get("creative_settings")

    _set_stage(p, "analyzing", "Visual + metadata analysis, prompt engineering…")
    synthesize_project_prompts(p, settings if isinstance(settings, dict) else None)
    _emit(pid, "prompts_generated", "Production prompts ready for human review.")
    return jsonify(p)


@app.route("/api/projects/<pid>/review", methods=["POST"])
def review_stage(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    if not p.get("prompt_bundle") or not p.get("reviewed_prompts"):
        return jsonify({"error": "Generate prompts first."}), 400

    data = request.get_json(force=True, silent=True) or {}
    edits = data.get("edited_prompts") or {}
    feedback = data.get("feedback") or {}
    if not isinstance(edits, dict) or not isinstance(feedback, dict):
        return jsonify({"error": "'edited_prompts' and 'feedback' must be objects."}), 400

    refined = []
    with _STATE_LOCK:
        for ctype, text in edits.items():
            if ctype in p["reviewed_prompts"] and isinstance(text, str):
                p["reviewed_prompts"][ctype] = text

        pending = [
            (ctype, fb.strip())
            for ctype, fb in feedback.items()
            if ctype in p["reviewed_prompts"] and isinstance(fb, str) and fb.strip()
        ]
        if pending:
            results = prompt_engine.refine_many(
                [(p["reviewed_prompts"][c], fb, c) for c, fb in pending],
                timeout=45,
            )
            for (ctype, fb), new_prompt in zip(pending, results):
                p["reviewed_prompts"][ctype] = new_prompt
                p["review_feedback"][ctype] = fb
                refined.append(ctype)

        p["verified"] = False
        p["stage"] = "reviewed"
        if refined:
            _log(p, f"Creative review applied with AI-refined feedback: {', '.join(refined)}")
        else:
            _log(p, "Creative review saved.")
        _persist()

    _emit(pid, "reviewed", "Prompts reviewed and refined.")
    return jsonify(p)


@app.route("/api/projects/<pid>/verify", methods=["POST"])
def verify_stage(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    if p.get("generation_running"):
        return jsonify({"error": "Generation is already running."}), 409
    if not p.get("reviewed_prompts"):
        return jsonify({"error": "Review prompts before verifying."}), 400

    data = request.get_json(force=True, silent=True) or {}
    forced = data.get("models") if isinstance(data.get("models"), dict) else {}
    settings = data.get("creative_settings")
    if not isinstance(settings, dict):
        settings = p.get("creative_settings") or {}

    with _STATE_LOCK:
        p["creative_settings"] = settings
        p["verified"] = True
        p["generation_running"] = True
        p["generation_error"] = None
        p["stage"] = "generating"
        _log(p, "Prompts verified — generation started with selected models.")
        _persist()
    _emit(pid, "generating", "Prompts verified — generating deliverables…")

    socketio.start_background_task(_generate_worker, pid, forced, settings)
    return jsonify(p)


@app.route("/api/projects/<pid>/generate", methods=["POST"])
def generate_stage(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    if p.get("generation_running"):
        return jsonify({"error": "Generation is already running."}), 409
    if not p.get("reviewed_prompts"):
        return jsonify({"error": "No prompts to generate from."}), 400

    data = request.get_json(force=True, silent=True) or {}
    forced = data.get("models") if isinstance(data.get("models"), dict) else {}
    settings = data.get("creative_settings")
    if not isinstance(settings, dict):
        settings = p.get("creative_settings") or {}

    with _STATE_LOCK:
        p["creative_settings"] = settings
        p["verified"] = True
        p["generation_running"] = True
        p["generation_error"] = None
        p["stage"] = "generating"
        _log(p, "Manual generation started.")
        _persist()
    _emit(pid, "generating", "Generation started with selected models.")

    socketio.start_background_task(_generate_worker, pid, forced, settings)
    return jsonify(p)


@app.route("/api/projects/<pid>/auto-run", methods=["POST"])
def auto_run(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    if p.get("generation_running"):
        return jsonify({"error": "A pipeline run is already in progress."}), 409

    data = request.get_json(force=True, silent=True) or {}
    forced = data.get("models") if isinstance(data.get("models"), dict) else {}
    settings = data.get("creative_settings")
    if not isinstance(settings, dict):
        settings = p.get("creative_settings") or {}
    else:
        with _STATE_LOCK:
            p["creative_settings"] = settings

    with _STATE_LOCK:
        p["generation_running"] = True
        p["generation_error"] = None
        p["verified"] = False
        p["stage"] = "scraping"
        _log(p, "Full campaign launched (auto-run).")
        _persist()
    _emit(pid, "scraping", "Launching full campaign pipeline…")

    socketio.start_background_task(_pipeline_worker, pid, forced, settings)
    return jsonify(p)


@app.route("/api/projects/<pid>/final-review", methods=["POST"])
def final_review_stage(pid):
    p = _get(pid)
    if not p:
        return jsonify({"error": "Project not found."}), 404
    if not p.get("outputs"):
        return jsonify({"error": "Nothing generated yet."}), 400
    if p.get("generation_running"):
        return jsonify({"error": "A pipeline run is already in progress."}), 409

    data = request.get_json(force=True, silent=True) or {}
    decisions = data.get("decisions")
    if not isinstance(decisions, dict) or not decisions:
        return jsonify({"error": "'decisions' must be a non-empty object."}), 400

    approved_types = []
    rejected = []
    with _STATE_LOCK:
        for ctype, d in decisions.items():
            if ctype not in (p.get("outputs") or {}):
                continue
            d = d if isinstance(d, dict) else {}
            approved = bool(d.get("approved", True))
            notes = str(d.get("notes") or "").strip()
            rating = d.get("rating")
            try:
                rating = int(rating) if rating is not None else None
            except (TypeError, ValueError):
                rating = None

            p.setdefault("final_review", {})[ctype] = {
                "approved": approved,
                "notes": notes,
                "rating": rating,
                "timestamp": time.time(),
            }
            out = p["outputs"][ctype]
            learning.record_review(
                project_id=p["id"],
                ctype=ctype,
                prompt=(p.get("reviewed_prompts") or {}).get(ctype, ""),
                output_filename=out.get("filename", ""),
                approved=approved,
                notes=notes,
                rating=rating,
            )
            if approved:
                approved_types.append(ctype)
            else:
                rejected.append((ctype, notes))
        _persist()

    if not rejected:
        with _STATE_LOCK:
            p["stage"] = "final_reviewed"
            _log(
                p,
                f"Final review: {len(approved_types)} deliverable(s) approved — "
                f"saved to creative memory.",
            )
            _persist()
        _emit(pid, "final_reviewed", "All deliverables approved — agent has learned from your review.")
        return jsonify(p)

    rejected_names = ", ".join(t for t, _ in rejected)
    with _STATE_LOCK:
        p["stage"] = "generating"
        p["generation_running"] = True
        p["generation_error"] = None
        _log(
            p,
            f"Corrections requested for {rejected_names} — refining prompts and regenerating…",
        )
        _persist()
    _emit(pid, "generating", f"Refining prompts and regenerating: {rejected_names}…")

    socketio.start_background_task(_regen_worker, pid, rejected)
    return jsonify(p)


# ---------------------------------------------------------------------------
# Socket.IO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def _sio_connect():
    logger.debug("Socket.IO client connected")


@socketio.on("join_project")
def _sio_join(data):
    pid = (data or {}).get("project_id")
    if not pid:
        return
    join_room(pid)
    from flask import request as flask_request
    sid = getattr(flask_request, "sid", None)
    if sid:
        socketio.emit("joined", {"project_id": pid}, to=sid)


@socketio.on("leave_project")
def _sio_leave(data):
    pid = (data or {}).get("project_id")
    if pid:
        leave_room(pid)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if database.enabled():
    try:
        database.init_db()
    except Exception:
        logger.exception("Database initialization failed; service will expose the failure in /api/health")
_load_projects()
if not PROJECTS:
    _persist()

if __name__ == "__main__":
    logger.info(
        "Starting %s on http://%s:%s (debug=%s)",
        config.APP_NAME, config.HOST, config.PORT, config.FLASK_DEBUG,
    )
    socketio.run(
        app,
        host=config.HOST,
        port=config.PORT,
        debug=config.FLASK_DEBUG,
        allow_unsafe_werkzeug=True,
    )
