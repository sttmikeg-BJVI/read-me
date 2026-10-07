from __future__ import annotations
import math
import os
from pathlib import Path
# Limit thread memory on free Render compute. Compilation is warmed during build.
if os.getenv('RENDER'):
    os.environ.setdefault('NUMBA_NUM_THREADS', '1')
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
import numpy as np
import librosa
from .models import BeatFeatures, PerformanceFeatures

KEY_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

def _safe_float(value, default=0.0):
    try:
        v=float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default

def _estimate_key(y, sr):
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    if chroma.size == 0:
        return None
    idx = int(np.argmax(np.mean(chroma, axis=1)))
    return KEY_NAMES[idx]

def analyze_beat(path: str, beat_id: str, title: str, tags=None) -> BeatFeatures:
    y, sr = librosa.load(path, sr=None, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = _safe_float(np.asarray(tempo).reshape(-1)[0] if np.asarray(tempo).size else 0.0)
    rms = librosa.feature.rms(y=y)
    energy = _safe_float(np.mean(rms))
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    onset_density = len(onsets) / duration if duration > 0 else 0.0
    return BeatFeatures(
        id=beat_id,
        title=title,
        path=str(Path(path)),
        duration=duration,
        bpm=bpm,
        energy=energy,
        onset_density=onset_density,
        key=_estimate_key(y, sr),
        tags=list(tags or []),
    )

def analyze_performance(path: str) -> PerformanceFeatures:
    y, sr = librosa.load(path, sr=None, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = _safe_float(np.asarray(tempo).reshape(-1)[0] if np.asarray(tempo).size else 0.0)
    rms = librosa.feature.rms(y=y)
    energy = _safe_float(np.mean(rms))
    onset_times = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    onset_density = len(onset_times) / duration if duration > 0 else 0.0
    frame_rms = np.asarray(rms).reshape(-1)
    threshold = max(float(np.median(frame_rms)) * 0.35, 1e-5) if frame_rms.size else 1e-5
    pause_ratio = float(np.mean(frame_rms < threshold)) if frame_rms.size else 0.0
    return PerformanceFeatures(
        duration=duration,
        estimated_bpm=bpm,
        energy=energy,
        onset_density=onset_density,
        pause_ratio=pause_ratio,
    )
