"""Application configuration. Secrets are read from .env and environment
variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

def env(name, default=""):
    """Read a setting from environment variables."""
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    return default.strip() if isinstance(default, str) else default

def api_key(name):
    """Treat template placeholders as absent credentials."""
    value = env(name)
    if value.lower() in {"", "your-key-here", "your-openrouter-key-here", "your-agnes-key-here", "change-me"}:
        return ""
    return value

def env_int(name, default, minimum=1):
    try:
        value = int(env(name, str(default)))
    except (ValueError, TypeError):
        value = default
    return max(minimum, value)

APP_NAME = env("APP_NAME", "AI Creative Studio")
HOST = env("HOST", "127.0.0.1")
PORT = env_int("PORT", 5000)
FLASK_DEBUG = env("FLASK_DEBUG", "0") == "1"
ENVIRONMENT = env("ENVIRONMENT", "development").lower()
SECRET_KEY = env("SECRET_KEY")
MAX_CONTENT_LENGTH = env_int("MAX_UPLOAD_MB", 80) * 1024 * 1024

# OpenRouter
OPENROUTER_API_KEY = api_key("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_SITE_URL = env("OPENROUTER_SITE_URL")
OPENROUTER_APP_TITLE = env("OPENROUTER_APP_TITLE", APP_NAME)
OPENROUTER_TEXT_MODEL = env("OPENROUTER_TEXT_MODEL", "openrouter/free")
OPENROUTER_FALLBACK_TEXT_MODEL = env("OPENROUTER_FALLBACK_TEXT_MODEL", "")
OPENROUTER_VISION_MODEL = env("OPENROUTER_VISION_MODEL", OPENROUTER_TEXT_MODEL)
# Media generation is optional and normally billed. Leave these blank until a
# model available to this account has been selected.
OPENROUTER_IMAGE_MODEL = env("OPENROUTER_IMAGE_MODEL")
OPENROUTER_VIDEO_MODEL = env("OPENROUTER_VIDEO_MODEL")
OPENROUTER_VIDEO_RATIO = env("OPENROUTER_VIDEO_RATIO", "16:9")
OPENROUTER_VIDEO_RESOLUTION = env("OPENROUTER_VIDEO_RESOLUTION", "720p")

# Agnes AI
AGNES_API_KEY = api_key("AGNES_API_KEY")
AGNES_BASE_URL = env("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
AGNES_TEXT_MODEL = env("AGNES_TEXT_MODEL", "agnes-2.5-flash")
AGNES_IMAGE_MODEL = env("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash")
AGNES_VIDEO_MODEL = env("AGNES_VIDEO_MODEL", "agnes-video-v2.0")


def set_runtime_api_key(provider, value):
    """Set a provider key for the current process without writing it to disk.

    This is intended for the single-team initial testing deployment. Production
    deployments should prefer environment variables or a secret manager.
    """
    global OPENROUTER_API_KEY, AGNES_API_KEY
    key = (value or "").strip()
    if provider == "openrouter":
        OPENROUTER_API_KEY = key
    elif provider == "agnes":
        AGNES_API_KEY = key
    else:
        raise ValueError("Unsupported provider.")


def provider_key_status():
    """Return configured flags only; never expose provider secrets."""
    return {
        "openrouter": bool(OPENROUTER_API_KEY),
        "agnes": bool(AGNES_API_KEY),
    }

# Shared creative controls
DEFAULT_IMAGE_SIZE = env("DEFAULT_IMAGE_SIZE", "1024x1024")
VIDEO_SECONDS = env_int("VIDEO_SECONDS", 6)
VIDEO_FPS = env_int("VIDEO_FPS", 24)
REQUEST_TIMEOUT = env_int("REQUEST_TIMEOUT", 180)
VIDEO_POLL_SECONDS = env_int("VIDEO_POLL_SECONDS", 5)
VIDEO_TIMEOUT = env_int("VIDEO_TIMEOUT", 600)
MAX_REVIEW_MEMORY = env_int("MAX_REVIEW_MEMORY", 40)
MAX_PROMPT_CHARS = env_int("MAX_PROMPT_CHARS", 12000)
SCRAPE_TIMEOUT = env_int("SCRAPE_TIMEOUT", 30)
MAX_REFERENCE_IMAGES = env_int("MAX_REFERENCE_IMAGES", 12)
MAX_REFERENCE_VIDEOS = env_int("MAX_REFERENCE_VIDEOS", 5)
MAX_DOWNLOAD_MB = env_int("MAX_DOWNLOAD_MB", 80)
MAX_VIDEO_FRAMES = env_int("MAX_VIDEO_FRAMES", 6)
ALLOW_PRIVATE_REFERENCE_URLS = env("ALLOW_PRIVATE_REFERENCE_URLS", "0") == "1"

# Rate Limiting
RATE_LIMIT_MAX_REQUESTS = env_int("RATE_LIMIT_MAX_REQUESTS", 100)
RATE_LIMIT_WINDOW = env_int("RATE_LIMIT_WINDOW", 60)

# Session/Cookie Security
SESSION_COOKIE_SECURE = env("SESSION_COOKIE_SECURE", "1") == "1"
SESSION_COOKIE_HTTPONLY = env("SESSION_COOKIE_HTTPONLY", "1") == "1"
SESSION_COOKIE_SAMESITE = env("SESSION_COOKIE_SAMESITE", "Lax")

# Production persistence / object storage
DATABASE_URL = env("DATABASE_URL")
DB_POOL_SIZE = env_int("DB_POOL_SIZE", 5)
DB_MAX_OVERFLOW = env_int("DB_MAX_OVERFLOW", 10)
SUPABASE_URL = env("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = api_key("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_STORAGE_BUCKET = env("SUPABASE_STORAGE_BUCKET", "creative-assets")
SUPABASE_STORAGE_PUBLIC = env("SUPABASE_STORAGE_PUBLIC", "0") == "1"
STORAGE_TIMEOUT = env_int("STORAGE_TIMEOUT", 120)

DATA_DIR = ROOT / "data"
OUTPUT_DIR = DATA_DIR / "outputs"
REFERENCE_DIR = DATA_DIR / "reference_assets"
REVIEW_STORE = DATA_DIR / "reviews.json"
PROJECT_STORE = DATA_DIR / "projects.json"
MEMORY_STORE = DATA_DIR / "creative_memory.json"
for path in (DATA_DIR, OUTPUT_DIR, REFERENCE_DIR):
    path.mkdir(parents=True, exist_ok=True)
