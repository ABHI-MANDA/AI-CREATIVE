# AI Creative Studio -- Reference-Aware Agent v3

A local Flask application for reference-driven image, video and copy generation with persistent human-review memory.

## Features

1. **Real reference extraction**
   - Downloads HTML images, OpenGraph/Twitter images, video/source links.
   - Accepts direct local image/video uploads from the UI.
   - Downloads references into `data/reference_assets/<project_id>/`.
   - Extracts metadata from images and videos.
   - Extracts multiple frames from every downloadable video.
   - Computes basic visual signals (size, aspect ratio, brightness, RGB statistics).
   - Sends selected images/video frames to the configured vision model for visual analysis.

2. **Verification triggers generation**
   - `POST /api/projects/<id>/verify` verifies prompts and immediately runs the generation pipeline.
   - Generation errors are surfaced in the UI.

3. **Reference-aware generation**
   - OpenRouter image/video generation can use extracted reference images.
   - One OpenRouter API key powers copy, visual analysis, images, and video.
   - Agnes AI provides an alternative provider for all creative types.
   - Local fallbacks keep the UI testable without API keys.

4. **Persistent learning**
   - `data/reviews.json` stores every human review.
   - `data/creative_memory.json` stores reusable creative lessons.
   - Future prompt generation retrieves relevant lessons.
   - Rejected final outputs are refined and regenerated.

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
             +------+------+
             |             |
           Image         Video
             |             |
        OpenRouter      OpenRouter/local/Agnes
             |             |
             +------+------+
                    |
                    v
               Final review
                    |
             +------+------+
             |             |
          approved       rejected
             |             |
          memory       refine prompt
                           |
                        regenerate
```

## First-Run Checklist

### 1. Create virtual environment

Do not install this into the same Python environment as unrelated projects. Use a dedicated virtual environment.

```powershell
cd "C:\Users\ABHISHEK KUMAR\Desktop\M & A\AI CREATIVE g"
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

Expected: `No broken requirements found.`

### 2. Configure API keys

Copy `.env.example` to `.env` (or use the existing `.env`):

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set your actual API keys:

```env
OPENROUTER_API_KEY=sk-or-v1-your-actual-key
AGNES_API_KEY=sk-your-agnes-key
```

### 3. Configure model IDs

Set the model IDs for the services you want to use. The defaults work for text:

```env
OPENROUTER_TEXT_MODEL=openrouter/free
AGNES_TEXT_MODEL=agnes-2.5-flash
AGNES_IMAGE_MODEL=agnes-image-2.1-flash
AGNES_VIDEO_MODEL=agnes-video-v2.0
```

For image/video generation, set models enabled for your OpenRouter account:

```env
OPENROUTER_IMAGE_MODEL=openai/dall-e-3
OPENROUTER_VIDEO_MODEL=google/veo-2
```

> **Note:** `openrouter/free` provides free text and vision routing only. Image/video generation requires model IDs that support those endpoints and, in most cases, account credit.

### 4. Start the server

```powershell
python app.py
```

Open: `http://127.0.0.1:5000`

## Provider Configuration

| Provider | API Key Variable | Image Model | Video Model | Text Model |
|----------|-----------------|-------------|-------------|------------|
| OpenRouter | `OPENROUTER_API_KEY` | `OPENROUTER_IMAGE_MODEL` | `OPENROUTER_VIDEO_MODEL` | `OPENROUTER_TEXT_MODEL` |
| Agnes AI | `AGNES_API_KEY` | `AGNES_IMAGE_MODEL` | `AGNES_VIDEO_MODEL` | `AGNES_TEXT_MODEL` |

- If a model ID is blank, that option shows as unavailable in the UI with a specific message.
- If an API key is missing, all models for that provider show as unavailable.
- Local fallbacks are always available regardless of API configuration.

## Video Duration Constraints

| Provider | Supported Durations |
|----------|-------------------|
| OpenRouter (Veo/Seedance) | 4, 6, 8 seconds |
| Agnes AI | 5, 6, 8, 10 seconds |
| Local fallback | Any duration |

The app auto-snaps the requested duration to the nearest supported value for the selected model.

## Production Deployment

Set `ENVIRONMENT=production`, `FLASK_DEBUG=0`, and a long random `SECRET_KEY`. Run behind a TLS reverse proxy:

```powershell
waitress-serve --host=127.0.0.1 --port=5000 app:app
```

## Data Directories

```text
data/
├── projects.json
├── reviews.json
├── creative_memory.json
├── reference_assets/
│   └── <project_id>/
│       ├── image_01.jpg
│       ├── video_01.mp4
│       └── video_01_frames/
└── outputs/
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Provider health check |
| `/api/models` | GET | Available models per creative type |
| `/api/learning` | GET | Review memory stats |
| `/api/projects` | POST | Create a new project |
| `/api/projects/<id>` | GET | Get project state |
| `/api/projects/<id>/scrape` | POST | Extract reference intelligence |
| `/api/projects/<id>/upload-reference` | POST | Upload reference files |
| `/api/projects/<id>/prompts` | POST | Build production prompts |
| `/api/projects/<id>/review` | POST | Save and refine prompts |
| `/api/projects/<id>/verify` | POST | Verify and generate |
| `/api/projects/<id>/generate` | POST | Manual retry/regeneration |
| `/api/projects/<id>/auto-run` | POST | Full pipeline (extract + prompt + generate) |
| `/api/projects/<id>/final-review` | POST | Submit final review |

## Troubleshooting

### "Set OPENROUTER_IMAGE_MODEL in .env" in model dropdown

The OpenRouter API key is present but no image model ID is configured. Set `OPENROUTER_IMAGE_MODEL` in `.env` to a model enabled for your account (e.g., `openai/dall-e-3`).

### "Set OPENROUTER_API_KEY in .env" for all OpenRouter models

Your OpenRouter API key is not configured. Add it to `.env` and restart the Flask server.

### Agnes video fails with mode error

The app uses `keyframes` mode for reference-based video and `ti2vid` for text-to-video, matching the Agnes API specification.

### The reference page has no images

Some sites render images through JavaScript, CSS, login walls, or bot protection. Upload the reference image/video directly through the UI.

### Generation appears to stop after verification

Check the status message and `data/projects.json`. API/provider errors are stored in the generated output's `warning` field when the local fallback is used.

### API conflicts with another project

Use `.venv` for this project. Do not share the same environment with other Python applications.

## Important Note About "Learning"

The JSON memory is **prompt-level continual learning**, not automatic neural-network weight training. It retrieves previous human feedback and injects it into future prompt generation.
