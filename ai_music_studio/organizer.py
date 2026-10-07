from __future__ import annotations
from dataclasses import asdict
from .matcher import rank_beats
from .models import BeatFeatures, PerformanceFeatures, SongRecord

PRODUCTION_TEMPLATE = {
    "id": "production-grid",
    "name": "Production Grid",
    "columns": ["INBOX", "ANALYZED", "MATCHED", "ASSIGNED", "READY"],
    "lanes": [
        {"id": "club-women", "label": "Club — Women"},
        {"id": "romantic-women", "label": "Romantic — Women"},
        {"id": "club-open", "label": "Club — Men / Both (Jack)"},
        {"id": "street", "label": "Street"},
        {"id": "cinematic", "label": "Cinematic"},
        {"id": "unassigned", "label": "Unassigned"},
    ],
}

VALID_STATUSES = set(PRODUCTION_TEMPLATE["columns"])
VALID_LANES = {row["id"] for row in PRODUCTION_TEMPLATE["lanes"]}

def normalize_lane(value: str) -> str:
    lane = (value or "").strip().lower()
    return lane if lane in VALID_LANES else "unassigned"

def normalize_status(value: str) -> str:
    status = (value or "").strip().upper()
    return status if status in VALID_STATUSES else "INBOX"

def performance_from_song(song: SongRecord):
    if not song.audio_path:
        return None
    return PerformanceFeatures(
        duration=song.duration,
        estimated_bpm=song.estimated_bpm,
        energy=song.energy,
        onset_density=song.onset_density,
        pause_ratio=song.pause_ratio,
    )

def suggest_matches(song: SongRecord, beats: list[BeatFeatures]):
    tags = list(song.tags)
    if song.lane and song.lane != "unassigned":
        tags.append(song.lane)
    return rank_beats(
        beats,
        performance=performance_from_song(song),
        lyric_tags=tags,
    )

def assign_best(song: SongRecord, beats: list[BeatFeatures]):
    ranked = suggest_matches(song, beats)
    if not ranked:
        return None
    best = ranked[0]
    song.assigned_beat_id = best.beat_id
    song.assigned_beat_title = best.title
    song.match_score = best.score
    song.status = "ASSIGNED"
    return best

def grid_payload(songs: list[SongRecord]):
    rows = []
    for lane in PRODUCTION_TEMPLATE["lanes"]:
        lane_songs = [s for s in songs if normalize_lane(s.lane) == lane["id"]]
        cells = {
            status: [asdict(s) for s in lane_songs if normalize_status(s.status) == status]
            for status in PRODUCTION_TEMPLATE["columns"]
        }
        rows.append({"lane": lane, "cells": cells})
    return {"template": PRODUCTION_TEMPLATE, "rows": rows}
