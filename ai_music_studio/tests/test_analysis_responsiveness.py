from concurrent.futures import ThreadPoolExecutor
from threading import Event
from fastapi.testclient import TestClient
from ai_music_studio import app as studio, store
from ai_music_studio.models import BeatFeatures

def test_health_remains_responsive_during_audio_analysis(tmp_path, monkeypatch):
    started, release = Event(), Event()
    monkeypatch.setattr(store, 'UPLOADS', tmp_path)
    monkeypatch.setattr(studio, 'UPLOADS', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'beats.json')
    monkeypatch.setattr(store, 'SONG_DB', tmp_path / 'songs.json')
    def slow_analysis(path, beat_id, title, tags):
        started.set()
        release.wait(5)
        return BeatFeatures(id=beat_id, title=title, path=path, duration=8, bpm=90, energy=.1, onset_density=1, key=None, tags=tags)
    monkeypatch.setattr(studio, 'analyze_beat', slow_analysis)
    with TestClient(studio.app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        upload = pool.submit(client.post, '/beats', data={'title':'Slow test'}, files={'file':('test.wav', b'test', 'audio/wav')})
        assert started.wait(2)
        health = pool.submit(client.get, '/healthz')
        try:
            assert health.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert upload.result(timeout=3).status_code == 200
