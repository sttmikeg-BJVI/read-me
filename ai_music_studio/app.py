from __future__ import annotations
import shutil
import uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from .analyzer import analyze_beat, analyze_performance
from .matcher import rank_beats
from .store import ensure_dirs, load_beats, save_beats, UPLOADS

app = FastAPI(title="AI Music Production Studio")
ensure_dirs()

INDEX = """
<!doctype html><html><head><meta charset='utf-8'><title>AI Music Production Studio</title>
<style>body{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 18px}section{border:1px solid #ccc;padding:18px;margin:16px 0;border-radius:10px}input,textarea,button{margin:6px 0;padding:8px;width:100%;box-sizing:border-box}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}audio{width:100%}</style>
</head><body>
<h1>AI Music Production Studio</h1>
<section><h2>1. Add beat</h2><form action='/beats' method='post' enctype='multipart/form-data'><input name='title' placeholder='Beat title'><input name='tags' placeholder='Tags: cinematic,street,romantic'><input type='file' name='file' accept='audio/*'><button>Add + Analyze Beat</button></form></section>
<section><h2>2. Match a delivery</h2><form action='/match' method='post' enctype='multipart/form-data'><textarea name='lyrics' placeholder='Paste lyrics here'></textarea><input name='tags' placeholder='Lane/tags'><input type='file' name='performance' accept='audio/*'><button>Analyze Delivery + Match</button></form></section>
<section><a href='/beats'>View beat library (JSON)</a></section>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return INDEX

@app.post("/beats")
async def add_beat(
    title: str = Form(...),
    tags: str = Form(""),
    file: UploadFile = File(...),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".mp3",".wav",".m4a",".aac",".flac",".ogg"}:
        raise HTTPException(400, "Unsupported audio type")
    beat_id = uuid.uuid4().hex[:12]
    target = UPLOADS / f"{beat_id}{ext}"
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    beat = analyze_beat(str(target), beat_id, title, [x.strip() for x in tags.split(",") if x.strip()])
    beats = load_beats()
    beats.append(beat)
    save_beats(beats)
    return {"beat": beat}

@app.get("/beats")
def beats():
    return {"beats": load_beats()}

@app.get("/beats/{beat_id}/audio")
def beat_audio(beat_id: str):
    beat = next((b for b in load_beats() if b.id == beat_id), None)
    if not beat:
        raise HTTPException(404, "Beat not found")
    return FileResponse(beat.path)

@app.post("/match")
async def match(
    lyrics: str = Form(""),
    tags: str = Form(""),
    performance: UploadFile | None = File(None),
):
    perf_features = None
    if performance and performance.filename:
        ext = Path(performance.filename).suffix.lower() or ".wav"
        target = UPLOADS / f"performance_{uuid.uuid4().hex[:12]}{ext}"
        with target.open("wb") as out:
            shutil.copyfileobj(performance.file, out)
        perf_features = analyze_performance(str(target))
    results = rank_beats(
        load_beats(),
        performance=perf_features,
        lyric_tags=[x.strip() for x in tags.split(",") if x.strip()],
    )
    return {
        "lyrics_received": bool(lyrics.strip()),
        "performance": perf_features,
        "matches": results,
    }
