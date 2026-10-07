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

Render deployment configuration is available in render.music.yaml. A live deployment must still be created and verified.

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

## Free Render + private Supabase storage

The dedicated render.music.yaml now uses Render free compute and no paid disk. Cloud audio and JSON song/beat records are saved in the private music-library bucket on Supabase. The local disk is only a temporary audio cache; stable supabase: references restore playback after a restart. Cloud errors fail saves visibly and never silently return an empty library.

Set SUPABASE_SECRET_KEY only in Render server environment settings (new Supabase secret key or legacy service_role JWT). Never place it in HTML, GitHub or a message. Configure SUPABASE_URL and SUPABASE_STORAGE_BUCKET; STUDIO_REQUIRE_CLOUD_STORAGE=1 fails closed when missing. Set STUDIO_PASSWORD yourself (at least 16 characters), STUDIO_OWNER_EMAIL and STUDIO_SESSION_SECRET (at least 32 characters). Cloud access remains protected by private studio sign-in.

Supabase free limits: 1 GB file storage, maximum 50 MB per file and limited transfer. Render free compute sleeps after inactivity and has 512 MB RAM. Audio analysis of long files may exceed free compute capacity; live validation is required before promising supported workloads. Use smaller compressed working copies and keep original masters separately. No paid upgrade is configured.

Use one worker and one service instance. JSON metadata is stored as two private objects; this is a single-owner library, not a distributed multi-server database. Reads fetch cloud metadata and writes are serialized in the app process. Audio is uploaded before metadata; a failed metadata save can leave an unused object that consumes storage. Existing local libraries are not automatically migrated. Back up your library and originals separately.

The cloud storage tests use a simulated private service to check audio/metadata restoration after deleting the cache, upload limits, corrupted data and failed saves. They do not confirm access to the live Supabase project.

## War Machine integration

The existing **Write to the beat** workspace now calls a real server-side War Machine engine after a song + beat grid is saved. It does not replace the Studio UI or require a paid AI API.

Defined operations currently wired:

- **War Chest**: creative ammunition from the current lyric, including focus terms, sound-gravity neighborhoods, physical/metaphor pathways, cadence slots and hook paths. It does not silently write a finished song.
- **Angel's Advocate**: preserves the artist's wording and recommends delivery/cadence strengthening moves; also returns a word-preserving timing alternative.
- **Devil's Advocate**: pressure-tests dense, repeated or predictable mechanical moves and returns alternate strategies without overwriting the lyric.
- **Alchemist — Mutate**: generates three intended-pocket alternatives (air, push/pull, final-word landing) while preserving every lyric word.
- **Lab in the Booth**: reports intended grid timing separately from recorded-file measurements. Existing uploaded vocal/demo analysis has duration/BPM/energy/onset/pause measurements only; word-level recorded-vocal timing is explicitly **not measured**.

Every engine run is saved on the song. The original lyric is captured in version history before engine alternatives are recorded. Applying an Alchemist timing variant changes the local intended grid only; the user must review/listen and press **Save song + grid** to make it current.

No external writing provider is configured. Model-written lyric alternatives remain unavailable until a provider is explicitly approved and configured; the internal War Machine functions above are independent of that future dependency.
