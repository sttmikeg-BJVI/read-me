from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from .models import BeatFeatures

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
UPLOADS = DATA / "uploads"
DB = DATA / "beats.json"

def ensure_dirs():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    if not DB.exists():
        DB.write_text("[]", encoding="utf-8")

def load_beats():
    ensure_dirs()
    rows = json.loads(DB.read_text(encoding="utf-8"))
    return [BeatFeatures(**r) for r in rows]

def save_beats(beats):
    ensure_dirs()
    DB.write_text(json.dumps([asdict(b) for b in beats], indent=2), encoding="utf-8")
