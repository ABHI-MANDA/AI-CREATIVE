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

Extended API endpoints (v2):
  POST   /api/autopilot/launch              — fire-and-forget autopilot pipeline
  GET    /api/autopilot/status/<project_id> — poll autopilot job progress
  GET    /api/campaign/<project_id>/bundle  — retrieve full campaign bundle JSON
  GET    /api/campaign/<project_id>/quality — quality scores for all outputs
  POST   /api/generate/batch                — parallel multi-variant generation
  GET    /api/brand/<project_id>/dna        — retrieve / lazily extract brand DNA
  POST   /api/brand/<project_id>/dna        — manually override brand DNA
"""

import os
import sys
import time
import uuid
import threading
import logging
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import Flask, jsonify, request, render_template, send_from_directory, abort
from flask_socketio import SocketIO, join_room, leave_room

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from core import config, scraper, prompt_engine, model_router, generator, learning, ai
from core.store import load_persistent_collection, save_persistent_collection
from core import database, storage, evaluator

# ---------------------------------------------------------------------------
# Optional heavy-core imports — fail gracefully so the rest of the app stays up
# ---------------------------------------------------------------------------

try:
    from core.autopilot import run_autopilot  # type: ignore
    _AUTOPILOT_AVAILABLE = True
except ImportError:
    run_autopilot = None  # type: ignore
    _AUTOPILOT_AVAILABLE = False

try:
    from core.brand_manager import extract_brand_dna, brand_dna_to_prompt_block  # type: ignore
    _BRAND_MANAGER_AVAILABLE = True
except ImportError:
    extract_brand_dna = None  # type: ignore
    brand_dna_to_prompt_block = None  # type: ignore
    _BRAND_MANAGER_AVAILABLE = False

try:
    from core.campaign_packager import build_bundle  # type: ignore
    _PACKAGER_AVAILABLE = True
except ImportError:
    build_bundle = None  # type: ignore
    _PACKAGER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask / Socket.IO setup
# ---------------------------------------------------------------------------

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
# Constants
# ---------------------------------------------------------------------------

VALID_TYPES: tuple = ("copy", "image", "video")

STAGE_INDEX: Dict[str, int] = {
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

# Autopilot job status constants
AUTOPILOT_STATUS_LAUNCHED: str = "launched"
AUTOPILOT_STATUS_RUNNING: str = "running"
AUTOPILOT_STATUS_COMPLETE: str = "complete"
AUTOPILOT_STATUS_ERROR: str = "error"

# Max variants allowed per batch request
BATCH_MAX_COUNT: int = 4

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

PROJECTS: Dict[str, Dict[str, Any]] = {}
PROJECTS_LOCK = threading.RLock()
_STATE_LOCK = threading.RLock()
_START_TIME: float = time.time()

# Autopilot job registry: keyed by project_id
# Each entry: {job_id, status, progress, step, error, bundle}
AUTOPILOT_JOBS: Dict[str, Dict[str, Any]] = {}
AUTOPILOT_JOBS_LOCK = threading.RLock()

_rate_lock = threading.Lock()
_rate_hits: Dict[str, deque] = {}


# ---------------------------------------------------------------------------
# Persistence & helpers
# ---------------------------------------------------------------------------

def _load_projects() -> None:
    """Load persisted projects from disk/DB into the in-memory PROJECTS dict."""
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


def _persist() -> None:
    """Atomically persist all in-memory projects to the configured store."""
    with PROJECTS_LOCK:
        save_persistent_collection("projects", config.PROJECT_STORE, {"projects": list(PROJECTS.values())})


def _log(p: Dict[str, Any], message: str) -> None:
    """Append a timestamped history entry to a project, capped at 500 entries."""
    p.setdefault("history", []).append({"timestamp": time.time(), "message": message})
    if len(p["history"]) > 500:
        del p["history"][:-500]


def _emit(pid: str, stage: Optional[str] = None, message: Optional[str] = None) -> None:
    """Emit a Socket.IO ``progress`` event to all clients in the project room."""
    payload: Dict[str, Any] = {"project_id": pid, "timestamp": time.time()}
    if stage:
        payload["stage"] = stage
    if message:
        payload["message"] = message
    try:
        socketio.emit("progress", payload, to=pid)
    except Exception:
        logger.debug("progress emit failed", exc_info=True)


def _set_stage(p: Dict[str, Any], stage: str, message: Optional[str] = None) -> None:
    """Atomically update a project's stage, log the message, persist, and broadcast."""
    with _STATE_LOCK:
        p["stage"] = stage
        if message:
            _log(p, message)
        _persist()
    _emit(p["id"], stage, message)


def _get(pid: str) -> Optional[Dict[str, Any]]:
    """Return a project by ID or None."""
    return PROJECTS.get(pid)


def _empty_scraped(title: str = "Uploaded references") -> Dict[str, Any]:
    """Return a blank scraped-data skeleton."""
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


def _new_project(
    url: str,
    brief: str,
    creative_types: List[str],
    creative_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new project dict, register it in PROJECTS, and return it."""
    pid = uuid.uuid4().hex[:10]
    p: Dict[str, Any] = {
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

def ingest_project(p: Dict[str, Any]) -> Dict[str, Any]:
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


def synthesize_project_prompts(
    p: Dict[str, Any],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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


def _model_candidates(
    p: Dict[str, Any],
    ctype: str,
    forced_models: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Return ordered model candidate list for a given creative type."""
    mid = (forced_models or {}).get(ctype)
    cands = model_router.model_candidates(ctype, forced_id=mid)
    if not cands and mid:
        cands = model_router.model_candidates(ctype)
    return cands


def generate_project_outputs(
    p: Dict[str, Any],
    forced_models: Optional[Dict[str, str]] = None,
    settings: Optional[Dict[str, Any]] = None,
    only_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
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

    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    def work(ctype: str):
        candidates = _model_candidates(p, ctype, forced_models)
        if not candidates:
            raise RuntimeError(f"No usable model configured for {ctype}.")
        refs = (p.get("scraped") or {}).get("assets") or []
        warnings: List[str] = []
        last_err: Optional[str] = None
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


def _generate_worker(pid: str, forced_models: Dict[str, str], settings: Dict[str, Any]) -> None:
    """Background worker: generate outputs for an existing project."""
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


def _pipeline_worker(pid: str, forced_models: Dict[str, str], settings: Dict[str, Any]) -> None:
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


def _regen_worker(pid: str, rejected: List[tuple]) -> None:
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
# Autopilot background worker
# ---------------------------------------------------------------------------

def _run_autopilot_bg(project_id: str, job_id: str, params: Dict[str, Any]) -> None:
    """Background thread target for the full autopilot pipeline.

    Updates AUTOPILOT_JOBS[project_id] throughout, emits Socket.IO events,
    and calls ``core.autopilot.run_autopilot`` if available.

    Args:
        project_id: The project identifier this job belongs to.
        job_id:     Unique job identifier returned to the caller on launch.
        params:     Dict containing creative_types, platforms, models, etc.
    """
    def _update_job(**kwargs: Any) -> None:
        with AUTOPILOT_JOBS_LOCK:
            AUTOPILOT_JOBS[project_id].update(kwargs)

    def emit_fn(event: str, data: Any) -> None:
        """Closure forwarding autopilot events to all SocketIO subscribers."""
        try:
            socketio.emit(event, data)
        except Exception:
            logger.debug("autopilot emit_fn failed for event=%s", event, exc_info=True)

    logger.info("Autopilot background thread started: project=%s job=%s", project_id, job_id)
    _update_job(status=AUTOPILOT_STATUS_RUNNING, step="initialising", progress=0)
    emit_fn("autopilot_progress", {
        "project_id": project_id,
        "job_id": job_id,
        "status": AUTOPILOT_STATUS_RUNNING,
        "step": "initialising",
        "progress": 0,
    })

    try:
        if not _AUTOPILOT_AVAILABLE or run_autopilot is None:
            raise RuntimeError(
                "core.autopilot module is not installed. "
                "Install it or implement core/autopilot.py."
            )

        bundle = run_autopilot(
            project_id=project_id,
            params=params,
            emit_fn=emit_fn,
        )

        _update_job(
            status=AUTOPILOT_STATUS_COMPLETE,
            step="complete",
            progress=100,
            bundle=bundle,
            error=None,
        )
        logger.info("Autopilot complete: project=%s job=%s", project_id, job_id)
        emit_fn("autopilot_complete", {
            "project_id": project_id,
            "job_id": job_id,
            "bundle": bundle,
        })

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Autopilot failed: project=%s job=%s", project_id, job_id)
        _update_job(
            status=AUTOPILOT_STATUS_ERROR,
            step="error",
            error=error_msg,
        )
        emit_fn("autopilot_error", {
            "project_id": project_id,
            "job_id": job_id,
            "error": error_msg,
        })


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
        "extensions": {
            "autopilot": _AUTOPILOT_AVAILABLE,
            "brand_manager": _BRAND_MANAGER_AVAILABLE,
            "campaign_packager": _PACKAGER_AVAILABLE,
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

    approved_types: List[str] = []
    rejected: List[tuple] = []
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
# API v2: Autopilot
# ---------------------------------------------------------------------------

@app.route("/api/autopilot/launch", methods=["POST"])
def autopilot_launch():
    """Launch the full autopilot pipeline for a project in a background thread.

    Request body (JSON):
        project_id     (str, required) — existing project to run autopilot on.
        creative_types (list[str])     — override creative types (optional).
        platforms      (list[str])     — target ad platforms (optional).
        models         (dict)          — forced model overrides per type (optional).

    Returns (202):
        {status, project_id, job_id}
    """
    if not _AUTOPILOT_AVAILABLE:
        return jsonify({
            "error": "Autopilot module is not available. "
                     "Implement core/autopilot.py with a run_autopilot() function."
        }), 503

    data = request.get_json(force=True, silent=True) or {}
    project_id: Optional[str] = data.get("project_id")
    if not project_id:
        return jsonify({"error": "'project_id' is required."}), 400

    p = _get(project_id)
    if not p:
        return jsonify({"error": f"Project '{project_id}' not found."}), 404

    # Check for an already-running job
    with AUTOPILOT_JOBS_LOCK:
        existing = AUTOPILOT_JOBS.get(project_id, {})
        if existing.get("status") == AUTOPILOT_STATUS_RUNNING:
            return jsonify({
                "error": "An autopilot job is already running for this project.",
                "job_id": existing.get("job_id"),
            }), 409

    job_id = uuid.uuid4().hex[:12]
    params: Dict[str, Any] = {
        "creative_types": data.get("creative_types") or p.get("creative_types", []),
        "platforms": data.get("platforms") or [],
        "models": data.get("models") or {},
    }

    with AUTOPILOT_JOBS_LOCK:
        AUTOPILOT_JOBS[project_id] = {
            "job_id": job_id,
            "status": AUTOPILOT_STATUS_LAUNCHED,
            "progress": 0,
            "step": "queued",
            "error": None,
            "bundle": None,
            "launched_at": time.time(),
        }

    logger.info("Launching autopilot: project=%s job=%s params=%s", project_id, job_id, params)
    t = threading.Thread(
        target=_run_autopilot_bg,
        args=(project_id, job_id, params),
        daemon=True,
    )
    t.start()

    return jsonify({
        "status": AUTOPILOT_STATUS_LAUNCHED,
        "project_id": project_id,
        "job_id": job_id,
    }), 202


@app.route("/api/autopilot/status/<project_id>", methods=["GET"])
def autopilot_status(project_id: str):
    """Return the current autopilot job status for a project.

    Returns (200):
        {project_id, job_id, status, progress, step, error}
    Returns (404) if no autopilot job has been launched for this project.
    """
    with AUTOPILOT_JOBS_LOCK:
        job = AUTOPILOT_JOBS.get(project_id)

    if not job:
        return jsonify({"error": f"No autopilot job found for project '{project_id}'."}), 404

    return jsonify({
        "project_id": project_id,
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "step": job.get("step"),
        "error": job.get("error"),
        "launched_at": job.get("launched_at"),
    })


# ---------------------------------------------------------------------------
# API v2: Campaign bundle & quality
# ---------------------------------------------------------------------------

@app.route("/api/campaign/<project_id>/bundle", methods=["GET"])
def campaign_bundle(project_id: str):
    """Return the full campaign bundle JSON for a completed autopilot run.

    Returns (200) with bundle data if available.
    Returns (404) if the bundle has not been generated yet.
    Returns (503) if the campaign_packager module is unavailable.
    """
    p = _get(project_id)
    if not p:
        return jsonify({"error": f"Project '{project_id}' not found."}), 404

    # First check if the autopilot job produced a bundle
    with AUTOPILOT_JOBS_LOCK:
        job = AUTOPILOT_JOBS.get(project_id, {})
        bundle = job.get("bundle")

    if bundle:
        return jsonify({"project_id": project_id, "bundle": bundle})

    # Fallback: build the bundle on-demand from project outputs
    if not p.get("outputs"):
        return jsonify({"error": "Campaign bundle not yet generated. Run autopilot or generate outputs first."}), 404

    if not _PACKAGER_AVAILABLE or build_bundle is None:
        return jsonify({
            "error": "Campaign packager module is unavailable. "
                     "Implement core/campaign_packager.py with a build_bundle() function."
        }), 503

    try:
        logger.info("Building on-demand campaign bundle for project=%s", project_id)
        bundle = build_bundle(p)
        return jsonify({"project_id": project_id, "bundle": bundle})
    except Exception as exc:
        logger.exception("Failed to build campaign bundle for project=%s", project_id)
        return jsonify({"error": f"Bundle generation failed: {exc}"}), 500


@app.route("/api/campaign/<project_id>/quality", methods=["GET"])
def campaign_quality(project_id: str):
    """Return quality scores for all outputs in a project.

    Aggregates the ``evaluation`` dict stored on each output by
    ``evaluator.evaluate_output`` at generation time.

    Returns (200) with per-type quality scores.
    Returns (404) if the project has no outputs yet.
    """
    p = _get(project_id)
    if not p:
        return jsonify({"error": f"Project '{project_id}' not found."}), 404

    outputs = p.get("outputs") or {}
    if not outputs:
        return jsonify({"error": "No outputs found. Generate content first."}), 404

    scores: Dict[str, Any] = {}
    for ctype, output in outputs.items():
        evaluation = output.get("evaluation") or {}
        scores[ctype] = {
            "score": evaluation.get("score"),
            "grade": evaluation.get("grade"),
            "details": evaluation.get("details"),
            "model_used": output.get("model_used"),
            "filename": output.get("filename"),
            "warning": output.get("warning"),
        }

    overall = None
    numeric = [v["score"] for v in scores.values() if isinstance(v.get("score"), (int, float))]
    if numeric:
        overall = round(sum(numeric) / len(numeric), 3)

    return jsonify({
        "project_id": project_id,
        "overall_score": overall,
        "scores": scores,
    })


# ---------------------------------------------------------------------------
# API v2: Batch generation
# ---------------------------------------------------------------------------

@app.route("/api/generate/batch", methods=["POST"])
def generate_batch():
    """Generate multiple variants of each creative type in parallel.

    Request body (JSON):
        project_id     (str, required)           — target project.
        creative_types (list[str], optional)      — types to generate (defaults to project types).
        models         (dict, optional)           — forced model overrides.
        settings       (dict, optional)           — creative settings overrides.
        count          (int, optional, 1–4)       — number of variants per type (default 1).

    Streams per-variant progress via SocketIO ``batch_progress`` events.

    Returns (200):
        {project_id, outputs: [{ctype, variant, result}]}
    """
    data = request.get_json(force=True, silent=True) or {}
    project_id: Optional[str] = data.get("project_id")
    if not project_id:
        return jsonify({"error": "'project_id' is required."}), 400

    p = _get(project_id)
    if not p:
        return jsonify({"error": f"Project '{project_id}' not found."}), 404

    prompts = p.get("reviewed_prompts") or {}
    if not prompts:
        return jsonify({"error": "No reviewed prompts available. Run the prompt stage first."}), 400

    # Resolve parameters
    raw_count = data.get("count", 1)
    try:
        count = max(1, min(BATCH_MAX_COUNT, int(raw_count)))
    except (TypeError, ValueError):
        return jsonify({"error": f"'count' must be an integer between 1 and {BATCH_MAX_COUNT}."}), 400

    creative_types: List[str] = data.get("creative_types") or p.get("creative_types") or []
    creative_types = [t for t in creative_types if t in prompts]
    if not creative_types:
        return jsonify({"error": "No valid creative types with prompts to generate."}), 400

    forced_models: Dict[str, str] = data.get("models") or {}
    settings: Dict[str, Any] = data.get("settings") or p.get("creative_settings") or {}

    logger.info(
        "Batch generation: project=%s types=%s count=%d",
        project_id, creative_types, count,
    )

    all_outputs: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    def _generate_variant(ctype: str, variant_idx: int) -> Dict[str, Any]:
        """Generate a single variant for one creative type."""
        candidates = _model_candidates(p, ctype, forced_models)
        if not candidates:
            raise RuntimeError(f"No usable model configured for {ctype}.")
        refs = (p.get("scraped") or {}).get("assets") or []
        last_err: Optional[str] = None
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
                return {
                    "ctype": ctype,
                    "variant": variant_idx,
                    "result": result,
                    "model_used": result.get("model_used", chosen.get("id")),
                }
            except Exception as exc:
                last_err = str(exc)
                continue
        raise RuntimeError(f"All engines failed for {ctype} variant {variant_idx}: {last_err}")

    total_tasks = len(creative_types) * count
    completed = 0

    workers = max(1, min(6, total_tasks))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_generate_variant, ctype, v): (ctype, v)
            for ctype in creative_types
            for v in range(1, count + 1)
        }
        for fut in as_completed(future_map):
            ctype, variant_idx = future_map[fut]
            try:
                output = fut.result()
                all_outputs.append(output)
                logger.info(
                    "Batch variant done: project=%s ctype=%s variant=%d model=%s",
                    project_id, ctype, variant_idx, output.get("model_used"),
                )
            except Exception as exc:
                logger.exception(
                    "Batch variant failed: project=%s ctype=%s variant=%d",
                    project_id, ctype, variant_idx,
                )
                errors.append({"ctype": ctype, "variant": variant_idx, "error": str(exc)})

            completed += 1
            progress_pct = round((completed / total_tasks) * 100)
            try:
                socketio.emit("batch_progress", {
                    "project_id": project_id,
                    "completed": completed,
                    "total": total_tasks,
                    "progress": progress_pct,
                    "ctype": ctype,
                    "variant": variant_idx,
                })
            except Exception:
                logger.debug("batch_progress emit failed", exc_info=True)

    response: Dict[str, Any] = {
        "project_id": project_id,
        "outputs": all_outputs,
    }
    if errors:
        response["errors"] = errors
        response["partial"] = True

    return jsonify(response)


# ---------------------------------------------------------------------------
# API v2: Brand DNA
# ---------------------------------------------------------------------------

@app.route("/api/brand/<project_id>/dna", methods=["GET"])
def get_brand_dna(project_id: str):
    """Return the extracted brand DNA for a project.

    If brand DNA has already been extracted and cached on the project, return it.
    Otherwise, lazily extract it from the project's scraped data and cache it.

    Returns (200) with brand DNA dict.
    Returns (400) if the project has no scraped data to extract from.
    Returns (404) if the project does not exist.
    Returns (503) if the brand_manager module is unavailable.
    """
    p = _get(project_id)
    if not p:
        return jsonify({"error": f"Project '{project_id}' not found."}), 404

    # Return cached brand DNA if available
    cached_dna = p.get("brand_dna")
    if cached_dna:
        return jsonify({
            "project_id": project_id,
            "brand_dna": cached_dna,
            "source": "cache",
        })

    if not _BRAND_MANAGER_AVAILABLE or extract_brand_dna is None:
        return jsonify({
            "error": "Brand manager module is unavailable. "
                     "Implement core/brand_manager.py with extract_brand_dna()."
        }), 503

    scraped = p.get("scraped")
    if not isinstance(scraped, dict) or not scraped:
        scraped = {
            "title": (p.get("brief") or "")[:50],
            "text": p.get("brief", ""),
            "assets": [],
        }

    try:
        logger.info("Extracting brand DNA for project=%s", project_id)
        dna = extract_brand_dna(scraped, p.get("brief", ""))
        with _STATE_LOCK:
            p["brand_dna"] = dna
            _log(p, "Brand DNA extracted and cached.")
            _persist()
        return jsonify({
            "project_id": project_id,
            "brand_dna": dna,
            "source": "extracted",
        })
    except Exception as exc:
        logger.exception("Brand DNA extraction failed for project=%s", project_id)
        return jsonify({"error": f"Brand DNA extraction failed: {exc}"}), 500


@app.route("/api/brand/<project_id>/dna", methods=["POST"])
def set_brand_dna(project_id: str):
    """Manually override or update the brand DNA for a project.

    Request body (JSON):
        brand_dna (dict, required) — the new brand DNA object.

    Returns (200) with the updated brand DNA.
    Returns (400) for validation errors.
    Returns (404) if the project does not exist.
    """
    p = _get(project_id)
    if not p:
        return jsonify({"error": f"Project '{project_id}' not found."}), 404

    data = request.get_json(force=True, silent=True) or {}
    brand_dna = data.get("brand_dna")
    if not isinstance(brand_dna, dict) or not brand_dna:
        return jsonify({"error": "'brand_dna' must be a non-empty object."}), 400

    with _STATE_LOCK:
        p["brand_dna"] = brand_dna
        _log(p, "Brand DNA manually overridden via API.")
        _persist()

    logger.info("Brand DNA updated for project=%s (%d keys)", project_id, len(brand_dna))

    # Optionally derive a prompt block for logging/debugging
    prompt_block: Optional[str] = None
    if _BRAND_MANAGER_AVAILABLE and brand_dna_to_prompt_block is not None:
        try:
            prompt_block = brand_dna_to_prompt_block(brand_dna)
        except Exception:
            logger.debug("brand_dna_to_prompt_block failed", exc_info=True)

    return jsonify({
        "project_id": project_id,
        "brand_dna": brand_dna,
        "prompt_block": prompt_block,
    })


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
