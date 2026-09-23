"""One-time migration from data/*.json to DATABASE_URL.

Usage:
  set DATABASE_URL=postgresql://...
  python scripts_migrate_json_to_db.py
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import config, database
from core.store import load_json

if not database.enabled():
    raise SystemExit("DATABASE_URL is not configured.")

database.init_db()
for name, path, default in [
    ("projects", config.PROJECT_STORE, {"projects": []}),
    ("reviews", config.REVIEW_STORE, {"reviews": []}),
    ("creative_memory", config.MEMORY_STORE, {"lessons": [], "stats": {"reviews": 0, "approved": 0, "rejected": 0}}),
]:
    data = load_json(path, default)
    database.save_collection(name, data)
    print(f"migrated {name}: {path}")
print("Migration complete.")
