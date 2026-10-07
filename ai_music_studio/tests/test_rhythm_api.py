import math
from dataclasses import asdict
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from ai_music_studio import store
from ai_music_studio.app import app
from ai_music_studio.models import SongRecord

def setup_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'UPLOADS', tmp_path / 'uploads')
    monkeypatch.setattr(store, 'DB', tmp_path / 'beats.json')
    monkeypatch.setattr(store, 'SONG_DB', tmp_path / 'songs.json')
    import ai_music_studio.app as module
    monkeypatch.setattr(module, 'UPLOADS', tmp_path / 'uploads')
    store.ensure_dirs()

def payload(beat_id):
    return {'title': 'WAR MACHINE SAMPLE SONG', 'lyrics': 'Hold the light', 'notes': 'automated acceptance fixture', 'rhythm': {
        'beat_id': beat_id, 'grid': {'bpm': 120, 'bars': 4, 'meter': 4, 'denominator': 4,
            'subdivision': 16, 'offset': 0.1, 'section': 'verse'},
        'phrases': [{'text': 'Hold the light', 'start': 0, 'duration': 3.2, 'push': -0.125,
            'intent': 'anticipation', 'words': [{'text': w, 'syllables': 1, 'stress': w == 'light'} for w in ['Hold', 'the', 'light']]}], 'pattern': None}}

def test_real_audio_upload_song_grid_save_reopen(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    # Real WAV bytes: synthetic 120 BPM click beat, not a production song.
    sr = 22050
    y = np.zeros(sr * 8, dtype=np.float32)
    for t in np.arange(0.1, 8, 0.5):
        start = int(t * sr)
        n = min(1102, len(y)-start)
        y[start:start+n] = .5*np.sin(2*np.pi*160*np.arange(n)/sr)*np.exp(-np.arange(n)/200)
    audio = tmp_path/'beat.wav'
    sf.write(audio, y, sr)
    with TestClient(app) as client:
        with audio.open('rb') as f:
            uploaded = client.post('/beats/bulk', data={'title_prefix': 'WAR MACHINE SAMPLE BEAT'}, files={'files': ('sample-beat.wav', f, 'audio/wav')})
        assert uploaded.status_code == 200, uploaded.text
        beat = uploaded.json()['beats'][0]
        assert 0 < beat['bpm'] < 400
        assert client.get(f"/beats/{beat['id']}/audio").status_code == 200
        with audio.open('rb') as f:
            song_response = client.post('/songs', data={'title': 'WAR MACHINE SAMPLE SONG', 'lyrics': 'Hold the light'}, files={'audio': ('sample-performance.wav', f, 'audio/wav')})
        assert song_response.status_code == 200, song_response.text
        song = song_response.json()['song']
        assert client.get(f"/songs/{song['id']}/audio").status_code == 200
        data = payload(beat['id'])
        response = client.put(f"/songs/{song['id']}/rhythm", json=data)
        assert response.status_code == 200, response.text
        engine = client.post(f"/songs/{song['id']}/war-machine", json={'operation': 'lab', 'request': 'verify intended pocket against available performance measurements'})
        assert engine.status_code == 200, engine.text
        lab = engine.json()['result']['result']
        assert lab['recorded_vocal_measurements']['has_recorded_audio'] is True
        assert lab['recorded_vocal_measurements']['word_level_timing_measured'] is False
        reopened = client.get('/api/state').json()['songs'][0]
        assert reopened['title'] == 'WAR MACHINE SAMPLE SONG'
        assert reopened['war_machine']['runs']
        assert reopened['rhythm'] == data['rhythm']
        assert reopened['lyrics'] == data['lyrics']
        assert store.load_songs()[0].rhythm == data['rhythm']
        for bad in [0, 401]:
            data['rhythm']['grid']['bpm'] = bad
            assert client.put(f"/songs/{song['id']}/rhythm", json=data).status_code == 422
        assert client.get('/').status_code == 200
        assert client.get('/studio-assets/rhythm.js').status_code == 200
        assert client.get('/studio-assets/pocket_ui.js').status_code == 200
        assert client.get('/studio-assets/store.py').status_code == 404

def test_legacy_song_defaults_preserve_fields(tmp_path, monkeypatch):
    setup_store(tmp_path, monkeypatch)
    import json
    legacy = asdict(SongRecord(id='legacy', title='Old song', lyrics='keep me', assigned_beat_id='oldbeat'))
    legacy.pop('rhythm')
    store.SONG_DB.write_text(json.dumps([legacy]), encoding='utf-8')
    song = store.load_songs()[0]
    assert song.lyrics == 'keep me'
    assert song.assigned_beat_id == 'oldbeat'
    assert song.rhythm == {}
