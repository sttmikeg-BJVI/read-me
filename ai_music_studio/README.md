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

## Beat grid and intended lyric pocket

The existing song editor now includes **Write to the beat**. Upload beats using the existing uploader, choose a saved song or enter a new title and lyrics, select a beat, and press **Map lyrics from lyrics field**. Each line initially occupies one bar; blank lines reserve a bar as a rest. Set BPM and adjust the first downbeat while listening. The clock uses quarter-note BPM; meter denominator changes the bar length (6/8 contains three quarter-note beats).

The scrolling timeline follows audio playback and supports click-to-seek, 1/4 through 1/64 subdivisions, word blocks and approximate syllable landing marks. Expand the phrase editor to change start, duration, push/pull, estimated syllable counts, word stress and delivery intent. These are intended timings, not measured vocal timings. Estimates should be corrected by the songwriter; automatic downbeat, section, stress and phonetic rhyme detection are not implemented.

Analysis reports syllables per second, available space within a bar, cross-bar phrases, off-quarter-beat placements, repeated density/length and the final word's intended landing. It does not assign a quality score or infer intent. Breathing room and density notes are rough prompts to listen, not musical verdicts.

Capture a flow to reuse phrase starts, lengths and push/pull on new words. Apply uses the current word syllable counts rather than pretending the new words have the same phonetic pattern. More-space/double-time/half-time controls modify intended timing while preserving text. They do not rewrite lyrics.

**Save song + grid** persists selected beat, corrected BPM/meter/alignment, lyric phrases and captured flow in the existing song JSON record. Older songs load with an empty rhythm field. Open the song to restore its settings. Existing uploads, analysis, matching, allocation, production grid and browser microphone dictation remain in place. Microphone dictation does not record timestamped vocal audio.

**Build writing context** produces structured context with the user's request, BPM, meter, section, subdivisions, phrase timing, stresses, density and last-word landing. This repository has no existing AI generation/rewrite provider or integration. AI-generated rewrites are not implemented; context preparation alone is not P4 acceptance.

No online deployment configuration, authentication or existing production URL was present on this branch. Local runtime verification is not public deployment. Do not expose this unauthenticated upload server publicly without hosting, access control and persistent storage decisions.

## Verification

```bash
python -m pip install -r ai_music_studio/requirements.txt pytest httpx
python -m pytest ai_music_studio/tests -q
node --test ai_music_studio/tests/test_rhythm.js
```

The integration test uploads real WAV bytes from a synthetic click fixture, creates a song, saves grid timing and reads it back from disk. It is not a test of a human vocal performance or a full commercially produced instrumental.

## Next practical upgrades

- bulk folder/ZIP beat ingestion
- manual beat assignment from the ranked list
- editable song metadata and drag/drop grid movement
- section/cue markers
- transcription adapter
- persistent cloud media storage
- richer harmonic/key compatibility scoring
