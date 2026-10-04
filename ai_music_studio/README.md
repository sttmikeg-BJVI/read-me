# AI Music Production Studio

Working MVP for organizing songs, analyzing beat/performance audio, matching songs to beats, and allocating work on a production grid.

## What works

- Upload beats: MP3/WAV/M4A/AAC/FLAC/OGG.
- Automatic beat analysis: duration, BPM, RMS energy, onset density, chroma-based key estimate.
- Upload song/performance audio plus lyrics, lane, tags, and notes.
- Automatic performance analysis: duration, BPM, energy, onset density, pause ratio.
- Persistent local song + beat libraries.
- Production Grid template with lanes:
  - Club — Women
  - Romantic — Women
  - Club — Men / Both (Jack)
  - Street
  - Cinematic
  - Unassigned
- Grid stages: INBOX → ANALYZED → MATCHED → ASSIGNED → READY.
- Match button returns ranked beat candidates with transparent scoring.
- Auto allocate assigns the highest-ranked beat to a song.
- Built-in audio preview for uploaded songs.
- Search/filter across the grid.
- No paid AI/API required for the core workflow.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r ai_music_studio/requirements.txt
uvicorn ai_music_studio.app:app --reload
```

Open http://127.0.0.1:8000

## Fast workflow

1. Upload your beat folder one beat at a time in the current UI.
2. Tag each beat with useful lane/mood labels.
3. Add a song with lyrics/notes and, when available, a rough vocal/demo file.
4. The song lands on the Production Grid.
5. Click **Match** to see ranked beat candidates.
6. Click **Auto allocate** to assign the best current match.
7. Move finished decisions to **READY**.

## Matching model

Current weights:
- tempo: 42%
- cadence/onset density: 28%
- energy: 20%
- tags/lane: 10%

Half-time and double-time tempo relationships are considered.

## Storage warning

This MVP uses local JSON plus a local upload directory. It is suitable for organizing and testing immediately, but it is not yet a permanent cloud media vault. Persistent object storage/database should be added before production deployment.

## Next practical upgrades

- bulk folder/ZIP beat ingestion
- manual beat assignment from the ranked list
- editable song metadata and drag/drop grid movement
- section/cue markers
- transcription adapter
- persistent cloud media storage
- richer harmonic/key compatibility scoring
