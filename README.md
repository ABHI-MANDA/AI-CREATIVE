# AI Creative Studio — Reference-Aware Agent v0.0.3

> **Internal use at Mark-Anthony Ventures.** This project is in its initial
> testing phase and is not yet a multi-user production service.

A Flask application for reference-driven image, video, and copy generation with persistent human-review memory, real-time progress updates, and enterprise-grade security.

## Features

### 1. Real Reference Extraction & Intelligence
- **Web scraping**: Downloads HTML images, OpenGraph/Twitter meta images, video/source links, JSON-LD structured data
- **Direct uploads**: Accepts local image/video uploads from the UI with drag-and-drop
- **Asset organization**: Downloads references into `data/reference_assets/<project_id>/`
- **Visual analysis**: Computes metadata from images (dimensions, aspect ratio, brightness, RGB stats) and videos (FPS, duration, frame extraction)
- **Frame extraction**: Extracts multiple frames from every downloadable video for reference
- **Vision model analysis**: Sends selected images/video frames to configured vision model for visual analysis

### 2. Verification-Triggered Generation Pipeline
- `POST /api/projects/<id>/verify` verifies prompts and immediately runs generation
- Background thread execution with proper locking (no race conditions)
- Generation errors surfaced in UI with full traceback logging
- Auto-snaps video duration to nearest supported value per model

### 3. Multi-Provider Reference-Aware Generation
- **OpenRouter**: Unified API for copy, vision, image, and video generation
- **Agnes AI**: Alternative provider for all creative types
- **Local fallbacks**: Always available for testing without API keys
- Reference images passed to image/video generation endpoints
- Automatic model routing with usability checks

### 4. Persistent Learning & Memory
- `data/reviews.json` stores every human review with ratings and notes
- `data/creative_memory.json` stores reusable creative lessons
- Future prompt generation retrieves relevant lessons automatically
- Rejected final outputs are refined via LLM and regenerated with same model

### 5. Real-Time Progress Updates
- **WebSocket support**: Flask-SocketIO with the portable threading backend
- Live generation progress via Socket.IO (`progress` events)
- Project-specific rooms for isolated updates
- Automatic fallback to polling if WebSocket unavailable

### 6. Security & Production Hardening
- **Rate limiting**: IP-based (100 req/min default, configurable)
- **Security headers**: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy, Permissions-Policy, HSTS
- **SSRF protection**: Private IP blocking, URL validation, redirect limits
- **Input sanitization**: URL length limits, content-type validation, file size limits
- **Session security**: Secure, HttpOnly, SameSite cookies

### 7. Reliability & Observability
- **Retry logic**: Exponential backoff (3 retries) for all external API calls
- **Structured logging**: JSON-formatted logs with project context
- **Health endpoint**: Detailed provider status, uptime, project counts
- **Atomic writes**: Thread-safe JSON persistence with temp file + rename
- **Stale state cleanup**: Clears `generation_running` flags on server restart

---

## Architecture

```text
User brief / URL / upload
        |
        v
Reference ingestion
  |          |          |
 HTML      Images      Videos
              |          |
              |       Frame extraction
              \__________/
                    |
                    v
          Visual + metadata analysis
                    |
                    v
             Prompt engineering
                    |
                    v
               Human review
                    |
                    v
          Verify & Generate Now
                    |
           +--------+--------+
           |                 |
         Image             Video
           |                 |
      OpenRouter         OpenRouter/local/Agnes
           |                 |
           +--------+--------+
                    |
                    v
               Final review
                    |
           +--------+--------+
           |                 |
        approved           rejected
           |                 |
        memory            refine prompt
                             |
                          regenerate
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- OpenRouter API key (for hosted models) or Agnes AI API key

### Local Development

```bash
# Clone and enter directory
cd AI_CREATIVE

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip check

# Configure environment
cp .env.example .env
# Edit .env with provider keys, or enter them in the Settings card in the UI.

# Run development server
python app.py
# Open http://127.0.0.1:5000
```

### Running Without API Keys (Local Fallbacks)
Leave API keys empty in `.env` — the app runs with local fallback generators for testing the full workflow.

### Entering API keys in the UI

The **Connect AI providers** card in the left sidebar accepts OpenRouter and
Agnes keys without exposing them again in the response. Keys are held only in
the running Python process and are not written to `.env` or project data. This
is convenient for the internal initial-testing deployment; for a shared or
long-lived Render deployment, configure `OPENROUTER_API_KEY` and/or
`AGNES_API_KEY` as Render environment secrets instead.

## Render deployment

This repository includes [`render.yaml`](./render.yaml) for a single Render
web service. Create a Blueprint from the repository, then add provider keys in
Render's Environment settings (never commit them). Render runs:

```text
pip install -r requirements.txt
gunicorn --worker-class gthread --threads 4 --timeout 600 backend.app:app
```

The `/api/health` endpoint is used as the Render health check. The free plan
may sleep when idle, and the current JSON persistence is intended for initial
testing rather than durable multi-instance storage.

## Project boundaries

```text
backend/       Backend boundary and conventions
core/          Modular pipeline, provider, learning, and persistence services
frontend/      Frontend boundary and extraction notes
templates/     Flask-served UI markup
static/        Browser behavior and styles
app.py         Backwards-compatible Flask/Socket.IO entry point
```

---

## Configuration

### Environment Variables (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_NAME` | Application display name | `AI Creative Studio` |
| `HOST` | Bind address | `127.0.0.1` |
| `PORT` | Bind port | `5000` |
| `FLASK_DEBUG` | Enable debug mode | `0` |
| `ENVIRONMENT` | `development` or `production` | `development` |
| `SECRET_KEY` | Flask secret key | - |
| `MAX_UPLOAD_MB` | Max upload size in MB | `80` |

#### OpenRouter
| Variable | Description | Default |
|----------|-------------|---------|
| `OPENROUTER_API_KEY` | API key from openrouter.ai | - |
| `OPENROUTER_BASE_URL` | API base URL | `https://openrouter.ai/api/v1` |
| `OPENROUTER_SITE_URL` | Your site URL for attribution | - |
| `OPENROUTER_APP_TITLE` | App title for OpenRouter | `APP_NAME` |
| `OPENROUTER_TEXT_MODEL` | Text generation model | `openrouter/free` |
| `OPENROUTER_FALLBACK_TEXT_MODEL` | Fallback text model | - |
| `OPENROUTER_VISION_MODEL` | Vision analysis model | `OPENROUTER_TEXT_MODEL` |
| `OPENROUTER_IMAGE_MODEL` | Image generation model | - |
| `OPENROUTER_VIDEO_MODEL` | Video generation model | - |
| `OPENROUTER_VIDEO_RATIO` | Default aspect ratio | `16:9` |
| `OPENROUTER_VIDEO_RESOLUTION` | Default resolution | `720p` |

#### Agnes AI
| Variable | Description | Default |
|----------|-------------|---------|
| `AGNES_API_KEY` | API key from agnes-ai.com | - |
| `AGNES_BASE_URL` | API base URL | `https://apihub.agnes-ai.com/v1` |
| `AGNES_TEXT_MODEL` | Text generation model | `agnes-2.5-flash` |
| `AGNES_IMAGE_MODEL` | Image generation model | `agnes-image-2.1-flash` |
| `AGNES_VIDEO_MODEL` | Video generation model | `agnes-video-v2.0` |

#### Creative Defaults
| Variable | Description | Default |
|----------|-------------|---------|
| `DEFAULT_IMAGE_SIZE` | Default image dimensions | `1024x1024` |
| `VIDEO_SECONDS` | Default video duration | `6` |
| `VIDEO_FPS` | Video frame rate | `24` |

#### Timeouts & Limits
| Variable | Description | Default |
|----------|-------------|---------|
| `REQUEST_TIMEOUT` | API request timeout (s) | `180` |
| `VIDEO_POLL_SECONDS` | Video status poll interval (s) | `5` |
| `VIDEO_TIMEOUT` | Max video generation wait (s) | `600` |
| `SCRAPE_TIMEOUT` | Web scrape timeout (s) | `30` |
| `MAX_DOWNLOAD_MB` | Max reference download (MB) | `80` |

#### Memory & Learning
| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_REVIEW_MEMORY` | Max lessons stored | `40` |
| `MAX_PROMPT_CHARS` | Max prompt length | `12000` |
| `MAX_REFERENCE_IMAGES` | Max images per project | `12` |
| `MAX_REFERENCE_VIDEOS` | Max videos per project | `5` |
| `MAX_VIDEO_FRAMES` | Max frames extracted per video | `6` |

#### Security
| Variable | Description | Default |
|----------|-------------|---------|
| `ALLOW_PRIVATE_REFERENCE_URLS` | Allow private IPs in references | `0` |
| `RATE_LIMIT_MAX_REQUESTS` | Requests per window | `100` |
| `RATE_LIMIT_WINDOW` | Rate limit window (s) | `60` |
| `SESSION_COOKIE_SECURE` | Secure cookies (HTTPS only) | `1` |
| `SESSION_COOKIE_HTTPONLY` | HttpOnly cookies | `1` |
| `SESSION_COOKIE_SAMESITE` | SameSite policy | `Lax` |

---

## Video Duration Constraints

| Provider | Supported Durations | Notes |
|----------|-------------------|-------|
| OpenRouter (Veo/Seedance) | 4, 6, 8 seconds | Auto-snaps to nearest |
| Agnes AI | 5, 6, 8, 10 seconds | Auto-snaps to nearest |
| Local fallback | Any duration | For testing only |

The app automatically snaps requested duration to the nearest supported value for the selected model.

---

## Project Structure

```text
AI_CREATIVE/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── .env.example          # Environment template
├── .gitignore            # Git ignore rules
├── pytest.ini           # Test configuration
├── core/
│   ├── __init__.py
│   ├── config.py         # Configuration management
│   ├── ai.py             # Multi-provider AI client (with retries)
│   ├── scraper.py        # Web scraping & asset download
│   ├── prompt_engine.py  # Prompt building & refinement
│   ├── model_router.py   # Model selection & duration snapping
│   ├── generator.py      # Local fallback generators
│   ├── learning.py       # Review memory & lessons
│   └── store.py          # Atomic JSON persistence
├── templates/
│   └── index.html        # Main UI
├── static/
│   ├── style.css         # Stylesheet
│   └── script.js         # Frontend logic + WebSocket
├── tests/
│   ├── conftest.py       # Test fixtures
│   ├── test_config.py
│   ├── test_store.py
│   ├── test_learning.py
│   └── test_api.py
└── data/                 # Created at runtime
    ├── projects.json
    ├── reviews.json
    ├── creative_memory.json
    ├── reference_assets/
    └── outputs/
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Provider health, uptime, project count |
| `/api/models` | GET | Available models per creative type |
| `/api/projects/<id>/models` | GET | Models for project's creative types |
| `/api/learning` | GET | Review memory stats |
| `/api/projects` | POST | Create new project |
| `/api/projects/<id>` | GET | Get project state |
| `/api/projects/<id>/scrape` | POST | Extract reference intelligence |
| `/api/projects/<id>/upload-reference` | POST | Upload reference files |
| `/api/projects/<id>/prompts` | POST | Build production prompts |
| `/api/projects/<id>/review` | POST | Save and refine prompts |
| `/api/projects/<id>/verify` | POST | Verify and start generation |
| `/api/projects/<id>/generate` | POST | Manual retry/regeneration |
| `/api/projects/<id>/auto-run` | POST | Full pipeline (extract + prompt + generate) |
| `/api/projects/<id>/final-review` | POST | Submit final review & learn |

### WebSocket Events

| Event | Direction | Payload |
|-------|-----------|---------|
| `connect` | Client→Server | - |
| `disconnect` | Client→Server | - |
| `join_project` | Client→Server | `{project_id}` |
| `leave_project` | Client→Server | `{project_id}` |
| `progress` | Server→Client | `{project_id, stage, message, timestamp}` |
| `joined` | Server→Client | `{project_id}` |

---

## Data Model

### Project Record (`data/projects.json`)
```json
{
  "id": "abc123",
  "created_at": 1699999999.0,
  "url": "https://example.com",
  "brief": "Campaign brief...",
  "creative_types": ["copy", "image", "video"],
  "creative_settings": {
    "copy": {"mood": "Premium & cinematic"},
    "image": {"image_size": "1024x1024", "mood": "..."},
    "video": {"duration": 6, "aspect_ratio": "16:9", "resolution": "720p", "mood": "..."}
  },
  "stage": "created|scraped|prompts_generated|reviewed|generating|generated|generation_failed|final_reviewed",
  "scraped": { "url": "...", "title": "...", "text": "...", "images": [], "videos": [], "assets": [], "visual_analysis": "" },
  "prompt_bundle": { "keywords": [], "visual_analysis": "", "prompts": {"copy": "...", "image": "..."}, "asset_count": 5 },
  "reviewed_prompts": {"copy": "...", "image": "..."},
  "review_feedback": {"copy": "...", "image": "..."},
  "verified": true,
  "chosen_models": {"copy": {...}, "image": {...}},
  "outputs": {"copy": {"filename": "...", "model_used": "...", "url": "/outputs/..."}, "image": {...}},
  "final_review": {"copy": {"approved": true, "notes": "", "rating": 5, "timestamp": ...}},
  "generation_running": false,
  "generation_error": null,
  "history": [{"timestamp": ..., "message": "..."}]
}
```

---

## Running Tests

```bash
# Install test dependencies
pip install pytest

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_api.py -v

# Run with coverage
pytest tests/ --cov=core --cov=app
```

---

## Troubleshooting

### Model Configuration Issues

| Error | Solution |
|-------|----------|
| `"Set OPENROUTER_IMAGE_MODEL in .env"` | Set an image model ID if using OpenRouter credits; leave blank to route images to Agnes |
| `"Set OPENROUTER_API_KEY in .env"` | Add OpenRouter API key and restart |
| `"Set AGNES_API_KEY in .env"` | Add Agnes API key and restart |
| Agnes video mode error | App uses `keyframes` (reference) or `ti2vid` (text-to-video) per Agnes spec |

### Reference Extraction

- **No images found**: Site uses JS rendering, login wall, or bot protection → Upload directly
- **Private URL blocked**: Set `ALLOW_PRIVATE_REFERENCE_URLS=1` (dev only) or upload
- **HLS/DASH streams**: Not auto-downloaded → Upload video file directly

### Generation Issues

- **Stuck on "generating"**: Check `/api/health` and server logs; WebSocket may have disconnected (polling fallback active)
- **Local fallback used**: Output has `warning` field with provider error; check API keys and model IDs
- **Video duration changed**: Auto-snapped to model's supported values (see table above)

### Common Issues

- **Port already in use**: Change `PORT` in `.env` or stop conflicting service
- **Permission errors**: Ensure `data/` directory is writable

---

## Important Notes

### About "Learning"
The JSON memory is **prompt-level continual learning**, not automatic neural-network weight training. It retrieves previous human feedback and injects it into future prompt generation via `learning.get_context()`.

### Local Fallbacks
When no API keys are configured, the app uses local generators that produce placeholder outputs marked with `"model_used": "local-*-fallback"` and a `warning` field. These are for workflow testing only.

### Security Considerations
- Never commit `.env` or `data/` to version control
- Use strong `SECRET_KEY` (32+ random chars) for production
- Rate limits are in-memory; use Redis for multi-instance deployments

---

## Roadmap (from Improvement Report)

### P0 — This Week (Safety)
- [x] Fix generation flag race conditions (F-01, F-06)
- [x] Clear stale flags on startup (F-02)
- [x] Null-safe reference handling (F-03)
- [x] Reference authorization (F-04)
- [x] Rate limiting & security headers

### P1 — Next 2-4 Weeks (Dependability)
- [ ] Redis-backed job queue with persistence
- [ ] Structured error codes & retry policies
- [ ] Prometheus metrics endpoint
- [ ] Request/response logging middleware

### P2 — This Quarter+ (Excellence)
- [ ] Authentication (OAuth2/OIDC)
- [ ] Multi-user projects with RBAC
- [ ] LoRA fine-tuning pipeline for domain adaptation
- [ ] Advanced prompt templates & versioning
- [ ] CDN integration for outputs

---

## License

MIT License — See LICENSE file for details.

---

## Credits

Built with:
- **Flask** + **Flask-SocketIO** for web framework & real-time
- **OpenRouter** & **Agnes AI** for hosted model APIs
- **BeautifulSoup4** + **lxml** for web scraping
- **OpenCV** + **Pillow** for image/video processing
- **Gunicorn** for production WSGI

## Production upgrade (2026-09)

This upgraded build adds a production persistence boundary without breaking the
existing JSON development mode:

- **PostgreSQL/Supabase support** through `DATABASE_URL`.
- **Durable Supabase Storage** for generated/reference media through the
  `SUPABASE_*` environment variables.
- `/api/health` now reports database and object-storage state.
- One-time migration utility: `python scripts_migrate_json_to_db.py`.
- Docker image with FFmpeg/OpenCV runtime libraries.
- Production dependency set with SQLAlchemy + psycopg.
- Render deployment Blueprint under `deploy/render.yaml`.
- Production architecture and deployment instructions under `docs/`.

### Persistence policy

JSON remains the local fallback. In production, use PostgreSQL as the system of
record and object storage for media. Never rely on the container filesystem for
assets that must survive deploys or restarts.

### Recommended hosting

- **Frontend:** Vercel + Next.js after the UI is split from Flask.
- **Backend/API:** Render Web Service + Gunicorn for the current Flask backend.
- **Database:** Supabase PostgreSQL.
- **Media:** Supabase Storage initially; S3/R2 can be introduced later for very
  large media volumes.

See `docs/PRODUCTION_ARCHITECTURE.md` for the deployment model and migration plan.
