from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class BeatFeatures:
    id: str
    title: str
    path: str
    duration: float
    bpm: float
    energy: float
    onset_density: float
    key: Optional[str] = None
    tags: List[str] = field(default_factory=list)

@dataclass
class PerformanceFeatures:
    duration: float
    estimated_bpm: float
    energy: float
    onset_density: float
    pause_ratio: float

@dataclass
class MatchResult:
    beat_id: str
    title: str
    score: float
    reasons: List[str]

@dataclass
class SongRecord:
    id: str
    title: str
    lyrics: str = ""
    lane: str = "unassigned"
    tags: List[str] = field(default_factory=list)
    status: str = "INBOX"
    audio_path: Optional[str] = None
    duration: float = 0.0
    estimated_bpm: float = 0.0
    energy: float = 0.0
    onset_density: float = 0.0
    pause_ratio: float = 0.0
    assigned_beat_id: Optional[str] = None
    assigned_beat_title: Optional[str] = None
    match_score: Optional[float] = None
    notes: str = ""
    rhythm: dict = field(default_factory=dict)
    lyric_versions: list = field(default_factory=list)
    war_machine: dict = field(default_factory=dict)
