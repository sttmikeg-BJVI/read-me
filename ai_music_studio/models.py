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
