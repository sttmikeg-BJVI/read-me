from ai_music_studio.matcher import rank_beats
from ai_music_studio.models import BeatFeatures, PerformanceFeatures

def test_rank_prefers_closer_tempo_and_density():
    perf = PerformanceFeatures(duration=20, estimated_bpm=92, energy=0.08, onset_density=2.1, pause_ratio=0.2)
    close = BeatFeatures(id="a", title="Close", path="x", duration=180, bpm=94, energy=0.08, onset_density=2.0, tags=["cinematic"])
    far = BeatFeatures(id="b", title="Far", path="y", duration=180, bpm=140, energy=0.18, onset_density=5.0, tags=["club"])
    ranked = rank_beats([far, close], performance=perf, lyric_tags=["cinematic"])
    assert ranked[0].beat_id == "a"
    assert ranked[0].score > ranked[1].score

def test_half_time_tempo_can_match():
    perf = PerformanceFeatures(duration=20, estimated_bpm=75, energy=0.05, onset_density=1.8, pause_ratio=0.2)
    beat = BeatFeatures(id="a", title="Half time", path="x", duration=180, bpm=150, energy=0.05, onset_density=1.8, tags=[])
    ranked = rank_beats([beat], performance=perf)
    assert ranked[0].score >= 70
