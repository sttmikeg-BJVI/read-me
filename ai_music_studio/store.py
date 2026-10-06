from __future__ import annotations
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from .models import BeatFeatures, SongRecord

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv('STUDIO_DATA_DIR', str(BASE / 'data')))
UPLOADS = DATA / "uploads"
DB = DATA / "beats.json"
SONG_DB = DATA / "songs.json"

def ensure_dirs():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    if not DB.exists():
        DB.write_text("[]", encoding="utf-8")
    if not SONG_DB.exists():
        SONG_DB.write_text("[]", encoding="utf-8")

def _load_rows(path: Path):
    ensure_dirs()
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (json.JSONDecodeError, OSError):
        return []

def load_beats():
    return [BeatFeatures(**r) for r in _load_rows(DB)]

def save_beats(beats):
    ensure_dirs()
    _atomic_write(DB, [asdict(b) for b in beats])

def load_songs():
    return [SongRecord(**r) for r in _load_rows(SONG_DB)]

def save_songs(songs):
    ensure_dirs()
    _atomic_write(SONG_DB, [asdict(s) for s in songs])

def _atomic_write(path, rows):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(rows, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
