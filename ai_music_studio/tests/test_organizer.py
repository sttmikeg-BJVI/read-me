from ai_music_studio.models import BeatFeatures, SongRecord
from ai_music_studio.organizer import assign_best, grid_payload

def beat(beat_id, title, bpm, tags):
    return BeatFeatures(
        id=beat_id,
        title=title,
        path="x",
        duration=180,
        bpm=bpm,
        energy=0.08,
        onset_density=2.0,
        tags=tags,
    )

def test_assign_best_sets_song_assignment():
    song = SongRecord(
        id="s1",
        title="Night Drive",
        lane="cinematic",
        tags=["cinematic"],
        status="INBOX",
    )
    best = assign_best(song, [
        beat("a", "Cinematic Beat", 90, ["cinematic"]),
        beat("b", "Club Beat", 140, ["club"]),
    ])
    assert best is not None
    assert song.assigned_beat_id == "a"
    assert song.status == "ASSIGNED"

def test_grid_payload_places_song_in_lane_and_stage():
    song = SongRecord(id="s1", title="Street One", lane="street", status="READY")
    payload = grid_payload([song])
    street = next(row for row in payload["rows"] if row["lane"]["id"] == "street")
    assert street["cells"]["READY"][0]["title"] == "Street One"
