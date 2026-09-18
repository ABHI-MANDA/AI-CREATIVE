# AI Creative Studio — Reference-Aware Agent v3

A local Flask application for reference-driven image, video and copy generation with persistent human-review memory.

## What is fixed

1. **Real reference extraction**
   - Downloads HTML images and OpenGraph/Twitter images.
   - Finds HTML video/source links and common video files.
   - Accepts direct local image/video uploads from the UI.
   - Downloads references into `data/reference_assets/<project_id>/`.
   - Extracts metadata from images and videos.
   - Extracts multiple frames from every downloadable video.
   - Computes basic visual signals (size, aspect ratio, brightness, RGB statistics).
   - Sends selected images/video frames to the configured vision-capable text model for visual analysis.

2. **Verification actually triggers generation**
   - `POST /api/projects/<id>/verify` verifies the prompts and immediately calls the generation pipeline.
   - The UI button is now **Verify & Generate Now**.
   - Generation errors are surfaced in the UI instead of silently stopping the workflow.

3. **Reference-aware generation**
   - OpenRouter image generation can use extracted reference images.
   - OpenRouter video generation can use the first extracted image or a representative video frame as its opening frame.
   - One OpenRouter API key powers copy, visual analysis, images, and video.
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
        OpenRouter      OpenRouter/local
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

## Installation — Windows

Do not install this into the same Python environment as unrelated LangChain/LiveKit/rembg projects. Use a dedicated virtual environment.

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

Expected:

```text
No broken requirements found.
```

## Configure `.env`

Copy `.env.example` to `.env` and fill the keys you actually use.

```env
OPENROUTER_API_KEY=your-key-here
OPENROUTER_TEXT_MODEL=openai/gpt-4o-mini
OPENROUTER_VISION_MODEL=openai/gpt-4o-mini
OPENROUTER_IMAGE_MODEL=bytedance-seed/seedream-4.5
OPENROUTER_VIDEO_MODEL=google/veo-3.1
OPENROUTER_VIDEO_RATIO=16:9
OPENROUTER_VIDEO_RESOLUTION=720p
```

Never commit `.env`.

## Start

```powershell
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Production deployment

Set `ENVIRONMENT=production`, `FLASK_DEBUG=0`, and a long random `SECRET_KEY`. Run behind a TLS reverse proxy and start with a production WSGI server:

```powershell
waitress-serve --host=127.0.0.1 --port=5000 app:app
```

`OPENROUTER_SITE_URL` should be your public HTTPS URL. Keep `.env` out of source control, rotate a key that has been exposed, and place authentication in front of this application before exposing it publicly. Reference fetching blocks private/loopback addresses by default; set `ALLOW_PRIVATE_REFERENCE_URLS=1` only in a trusted internal deployment.

## Recommended production workflow

### Image + video reference

1. Enter a reference URL or upload files.
2. Click **Start reference extraction**.
3. Confirm extracted image/video counts and video-frame count.
4. Click **Build prompts from references**.
5. Inspect the visual-reference analysis included in the prompts.
6. Edit prompts and add reviewer corrections.
7. Click **Save review & refine**.
8. Tick the verification checkbox.
9. Click **Verify & Generate Now**.
10. The backend automatically generates all requested outputs.
11. Review outputs.
12. Reject with precise corrections if necessary.
13. The agent writes the review to JSON, refines the prompt and regenerates.

## Provider behavior

- `OPENROUTER_API_KEY` is the only hosted-provider credential.
- OpenRouter's chat-completions API powers copy and visual analysis; its image and asynchronous video APIs power media generation.
- Image/video model availability and pricing are controlled by your OpenRouter account. Set the model IDs in `.env` to options available to that account.
- If hosted generation fails, the UI receives a clearly marked local fallback so the review workflow remains testable.

## Important note about "learning"

The JSON memory is **prompt-level continual learning**, not automatic neural-network weight training. It retrieves previous human feedback and injects it into future prompt generation. This avoids falsely claiming that a foundation model has been retrained just because feedback was saved to JSON.

## Data directories

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

## Current API endpoints

- `POST /api/projects`
- `POST /api/projects/<id>/scrape`
- `POST /api/projects/<id>/upload-reference`
- `POST /api/projects/<id>/prompts`
- `POST /api/projects/<id>/review`
- `POST /api/projects/<id>/verify` — verifies and immediately generates
- `POST /api/projects/<id>/generate` — manual retry/regeneration
- `GET /api/projects/<id>/models`
- `POST /api/projects/<id>/final-review`
- `GET /api/learning`
- `GET /api/health`

## Troubleshooting

### The reference page has no images
Some sites render images through JavaScript, CSS, login walls, or bot protection. In that case upload the reference image/video directly through the UI.

### Video URL is a streaming playlist
Some pages expose HLS/DASH manifests rather than a simple MP4. The application currently handles common downloadable video URLs and embedded video links. For protected streaming, download the permitted source yourself and upload the video.

### Generation appears to stop after verification
This version removes the previous UI dependency: verification directly invokes the generation pipeline. Check the status message and `data/projects.json`. API/provider errors are stored in the generated output's `warning` field when the local fallback is used.

### API conflicts with another project
Use `.venv` for this project. Do not share the same environment with LiveKit/LangChain/rembg applications.
