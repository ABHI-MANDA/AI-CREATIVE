"""Durable object-storage adapter for generated/reference assets.

Supabase Storage is used when SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are
configured. Local disk remains the development fallback.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
import requests

from . import config

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_ROLE_KEY and config.SUPABASE_STORAGE_BUCKET)


def upload_file(path: Path, object_path: str) -> str | None:
    if not enabled() or not path.exists():
        return None
    object_path = object_path.lstrip("/")
    url = f"{config.SUPABASE_URL.rstrip('/')}/storage/v1/object/{config.SUPABASE_STORAGE_BUCKET}/{object_path}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": mime,
        "x-upsert": "true",
    }
    try:
        with path.open("rb") as fh:
            response = requests.post(url, headers=headers, data=fh, timeout=config.STORAGE_TIMEOUT)
        response.raise_for_status()
        if config.SUPABASE_STORAGE_PUBLIC:
            return f"{config.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{config.SUPABASE_STORAGE_BUCKET}/{object_path}"
        return None
    except Exception as exc:
        logger.warning("Storage upload failed for %s: %s", path, exc)
        return None


def persist_output(path: Path, project_id: str, creative_type: str) -> str | None:
    object_path = f"projects/{project_id}/outputs/{creative_type}/{path.name}"
    return upload_file(path, object_path)


def persist_reference(path: Path, project_id: str, filename: str) -> str | None:
    safe = Path(filename).name
    return upload_file(path, f"projects/{project_id}/references/{safe}")
