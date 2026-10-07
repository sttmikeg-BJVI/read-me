from __future__ import annotations
from typing import Iterable, Optional
from .models import BeatFeatures, PerformanceFeatures, MatchResult

DEFAULT_WEIGHTS = {
    "tempo": 0.42,
    "cadence": 0.28,
    "energy": 0.20,
    "tags": 0.10,
}

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def _relative_similarity(a: float, b: float, tolerance: float) -> float:
    if a <= 0 or b <= 0:
        return 0.5
    return _clamp01(1.0 - abs(a - b) / max(tolerance, abs(a), abs(b)))

def _tempo_similarity(perf_bpm: float, beat_bpm: float) -> float:
    if perf_bpm <= 0 or beat_bpm <= 0:
        return 0.5
    candidates = [beat_bpm, beat_bpm / 2.0, beat_bpm * 2.0]
    return max(_relative_similarity(perf_bpm, c, 18.0) for c in candidates if c > 0)

def rank_beats(
    beats: Iterable[BeatFeatures],
    performance: Optional[PerformanceFeatures] = None,
    lyric_tags=None,
    weights=None,
):
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    tags = {t.lower().strip() for t in (lyric_tags or []) if t.strip()}
    results = []
    for beat in beats:
        reasons = []
        if performance:
            tempo = _tempo_similarity(performance.estimated_bpm, beat.bpm)
            cadence = _relative_similarity(performance.onset_density, beat.onset_density, 2.0)
            energy = _relative_similarity(performance.energy, beat.energy, 0.08)
        else:
            tempo, cadence, energy = 0.5, 0.5, 0.5
        beat_tags = {t.lower().strip() for t in beat.tags}
        tag_score = (len(tags & beat_tags) / len(tags)) if tags else 0.5

        if tempo >= 0.8:
            reasons.append("tempo pocket aligns well")
        elif tempo >= 0.6:
            reasons.append("tempo is workable with minor delivery adjustment")
        else:
            reasons.append("tempo fit is weaker")
        if cadence >= 0.75:
            reasons.append("rhythmic density fits the delivery")
        if energy >= 0.75:
            reasons.append("energy level is compatible")
        if tag_score >= 0.75 and tags:
            reasons.append("mood/lane tags align")

        score = 100.0 * (
            w["tempo"] * tempo +
            w["cadence"] * cadence +
            w["energy"] * energy +
            w["tags"] * tag_score
        ) / max(sum(w.values()), 1e-9)

        results.append(MatchResult(
            beat_id=beat.id,
            title=beat.title,
            score=round(score, 1),
            reasons=reasons,
        ))
    return sorted(results, key=lambda x: x.score, reverse=True)
