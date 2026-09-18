"""Application configuration. Secrets are read only from the environment/.env."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

def env(name, default=''):
    return os.getenv(name, default).strip()

def env_int(name, default, minimum=1):
    try: value = int(env(name, str(default)))
    except ValueError: value = default
    return max(minimum, value)

APP_NAME = env('APP_NAME', 'AI Creative Studio')
HOST = env('HOST', '127.0.0.1')
PORT = env_int('PORT', 5000)
FLASK_DEBUG = env('FLASK_DEBUG', '0') == '1'
ENVIRONMENT = env('ENVIRONMENT', 'development').lower()
SECRET_KEY = env('SECRET_KEY')
MAX_CONTENT_LENGTH = env_int('MAX_UPLOAD_MB', 80) * 1024 * 1024

# One credential powers all hosted generation paths.
OPENROUTER_API_KEY = env('OPENROUTER_API_KEY')
OPENROUTER_BASE_URL = env('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
OPENROUTER_SITE_URL = env('OPENROUTER_SITE_URL')
OPENROUTER_APP_TITLE = env('OPENROUTER_APP_TITLE', APP_NAME)
OPENROUTER_TEXT_MODEL = env('OPENROUTER_TEXT_MODEL', 'openai/gpt-4o-mini')
OPENROUTER_FALLBACK_TEXT_MODEL = env('OPENROUTER_FALLBACK_TEXT_MODEL', 'google/gemma-4-31b-it:free')
OPENROUTER_VISION_MODEL = env('OPENROUTER_VISION_MODEL', OPENROUTER_TEXT_MODEL)
OPENROUTER_IMAGE_MODEL = env('OPENROUTER_IMAGE_MODEL', 'bytedance-seed/seedream-4.5')
OPENROUTER_VIDEO_MODEL = env('OPENROUTER_VIDEO_MODEL', 'google/veo-3.1')
OPENROUTER_VIDEO_RATIO = env('OPENROUTER_VIDEO_RATIO', '16:9')
OPENROUTER_VIDEO_RESOLUTION = env('OPENROUTER_VIDEO_RESOLUTION', '720p')
DEFAULT_IMAGE_SIZE = env('DEFAULT_IMAGE_SIZE', '1024x1024')
VIDEO_SECONDS = env_int('VIDEO_SECONDS', 5)
VIDEO_FPS = env_int('VIDEO_FPS', 24)
REQUEST_TIMEOUT = env_int('REQUEST_TIMEOUT', 60)
VIDEO_POLL_SECONDS = env_int('VIDEO_POLL_SECONDS', 5)
VIDEO_TIMEOUT = env_int('VIDEO_TIMEOUT', 90)
MAX_REVIEW_MEMORY = env_int('MAX_REVIEW_MEMORY', 40)
MAX_PROMPT_CHARS = env_int('MAX_PROMPT_CHARS', 12000)
SCRAPE_TIMEOUT = env_int('SCRAPE_TIMEOUT', 30)
MAX_REFERENCE_IMAGES = env_int('MAX_REFERENCE_IMAGES', 12)
MAX_REFERENCE_VIDEOS = env_int('MAX_REFERENCE_VIDEOS', 5)
MAX_DOWNLOAD_MB = env_int('MAX_DOWNLOAD_MB', 80)
MAX_VIDEO_FRAMES = env_int('MAX_VIDEO_FRAMES', 6)
ALLOW_PRIVATE_REFERENCE_URLS = env('ALLOW_PRIVATE_REFERENCE_URLS', '0') == '1'

DATA_DIR = ROOT / 'data'; OUTPUT_DIR = DATA_DIR / 'outputs'; REFERENCE_DIR = DATA_DIR / 'reference_assets'
REVIEW_STORE = DATA_DIR / 'reviews.json'; PROJECT_STORE = DATA_DIR / 'projects.json'; MEMORY_STORE = DATA_DIR / 'creative_memory.json'
for path in (DATA_DIR, OUTPUT_DIR, REFERENCE_DIR): path.mkdir(parents=True, exist_ok=True)
