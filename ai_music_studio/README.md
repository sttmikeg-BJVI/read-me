# AI Music Production Studio

MVP goal: ingest beats and lyric performances, analyze tempo/cadence/energy, then rank the best beat matches with transparent reasons.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r ai_music_studio/requirements.txt
uvicorn ai_music_studio.app:app --reload
```

Open http://127.0.0.1:8000

## MVP workflow

1. Upload beat files (MP3/WAV/M4A).
2. Add lyrics as text.
3. Optionally upload a raw vocal-performance recording.
4. Analyze beats.
5. Match lyrics/performance against beats.
6. Preview ranked results.

## Design constraints

- Preserve original uploaded masters.
- Local/open-source analysis first.
- No paid API is required for core MVP matching.
- No publishing, billing, marketplace, or customer-account system in this MVP.
- Transcription is intentionally swappable; raw timing features are retained even without transcription.

## Render

Deploy as a Python web service with:

- Build: `pip install -r ai_music_studio/requirements.txt`
- Start: `uvicorn ai_music_studio.app:app --host 0.0.0.0 --port $PORT`

Persistent production storage/database should be added before treating Render as the permanent media vault.
