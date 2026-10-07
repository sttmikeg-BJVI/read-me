from dataclasses import replace

from fastapi.testclient import TestClient

from ai_music_studio.app import app
from ai_music_studio.models import BeatFeatures, SongRecord
from ai_music_studio import store
from ai_music_studio.war_machine import run_engine


def sample_rhythm(beat_id="beat1"):
    return {
        "beat_id": beat_id,
        "grid": {"bpm": 120, "bars": 4, "meter": 4, "denominator": 4, "subdivision": 16, "offset": 0, "section": "verse"},
        "phrases": [
            {
                "text": "Cold light on the shore",
                "start": 0,
                "duration": 3.2,
                "push": -0.125,
                "intent": "anticipation",
                "words": [
                    {"text": "Cold", "syllables": 1, "stress": True},
                    {"text": "light", "syllables": 1, "stress": False},
                    {"text": "on", "syllables": 1, "stress": False},
                    {"text": "the", "syllables": 1, "stress": False},
                    {"text": "shore", "syllables": 1, "stress": True},
                ],
            },
            {
                "text": "Pressure made me move",
                "start": 4,
                "duration": 3.2,
                "push": 0,
                "intent": "neutral",
                "words": [
                    {"text": "Pressure", "syllables": 2, "stress": True},
                    {"text": "made", "syllables": 1, "stress": False},
                    {"text": "me", "syllables": 1, "stress": False},
                    {"text": "move", "syllables": 1, "stress": True},
                ],
            },
        ],
        "pattern": None,
    }


def sample_song():
    return SongRecord(
        id="song1",
        title="War Machine Sample",
        lyrics="Cold light on the shore\nPressure made me move",
        rhythm=sample_rhythm(),
        audio_path="supabase:sample.wav",
        duration=12.5,
        estimated_bpm=118,
        energy=0.12,
        onset_density=2.5,
        pause_ratio=0.22,
    )


def sample_beat():
    return BeatFeatures(id="beat1", title="Sample Beat", path="x", duration=180, bpm=120, energy=.1, onset_density=2, tags=["cinematic"])


def test_defined_operations_are_real_and_preserve_artist_words():
    song, beat = sample_song(), sample_beat()
    for operation in ["war_chest", "angel", "devil", "mutate", "lab"]:
        out = run_engine(song, beat, operation, "test request")
        assert out["status"] == "complete"
        assert out["context"]["timing_basis"].startswith("user-edited intended")
    chest = run_engine(song, beat, "war_chest")["result"]
    assert chest["cadence_slots"]
    assert chest["sound_gravity"]
    mutation = run_engine(song, beat, "mutate")["result"]
    assert len(mutation["variants"]) == 3
    assert all(v["lyrics"] == song.lyrics for v in mutation["variants"])
    lab = run_engine(song, beat, "lab")["result"]
    assert lab["recorded_vocal_measurements"]["word_level_timing_measured"] is False
    assert lab["recorded_vocal_measurements"]["measured_audio_duration_seconds"] == 12.5


def test_api_persists_run_versions_and_reopens(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(store, "DB", tmp_path / "beats.json")
    monkeypatch.setattr(store, "SONG_DB", tmp_path / "songs.json")
    import ai_music_studio.app as module
    monkeypatch.setattr(module, "UPLOADS", tmp_path / "uploads")
    store.ensure_dirs()
    store.save_beats([sample_beat()])
    store.save_songs([sample_song()])

    with TestClient(app) as client:
        response = client.post("/songs/song1/war-machine", json={"operation": "mutate", "request": "give me pocket alternatives"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["status"] == "complete"
        assert len(body["result"]["result"]["variants"]) == 3

    reopened = store.load_songs()[0]
    assert reopened.lyrics == sample_song().lyrics
    assert reopened.war_machine["runs"]
    assert any(v["kind"] == "original" for v in reopened.lyric_versions)
    assert len([v for v in reopened.lyric_versions if v["kind"] == "engine-timing-alternative"]) == 3
