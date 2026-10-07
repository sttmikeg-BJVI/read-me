from fastapi.testclient import TestClient
from ai_music_studio.app import app
from ai_music_studio.auth import COOKIE, token, valid_token

def settings(monkeypatch):
    monkeypatch.setenv('STUDIO_OWNER_EMAIL', 'owner@example.com')
    monkeypatch.setenv('STUDIO_PASSWORD', 'test-only-long-password')
    monkeypatch.setenv('STUDIO_SESSION_SECRET', 's'*40)
    monkeypatch.setenv('STUDIO_REQUIRE_AUTH', '1')

def test_private_login_protects_api_audio_and_upload(monkeypatch):
    settings(monkeypatch)
    with TestClient(app, base_url='https://studio.example') as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/api/state').status_code == 401
        assert client.get('/beats/anything/audio').status_code == 401
        assert client.post('/songs', data={'title': 'unauthorized'}).status_code == 401
        assert client.get('/', headers={'Accept':'text/html'}, follow_redirects=False).status_code == 303
        assert client.post('/login', data={'email':'owner@example.com','password':'wrong'}).status_code == 401
        good = client.post('/login', data={'email':'owner@example.com','password':'test-only-long-password'}, follow_redirects=False)
        assert good.status_code == 303
        assert 'HttpOnly' in good.headers['set-cookie'] and 'Secure' in good.headers['set-cookie']
        assert client.get('/api/state').status_code == 200
        assert client.put('/songs/unknown/rhythm', json={}, headers={'Origin':'https://evil.example'}).status_code == 403
        assert client.post('/logout', follow_redirects=False).status_code == 303
        assert client.get('/api/state').status_code == 401

def test_cloud_fails_closed_without_credentials(monkeypatch):
    for key in ['STUDIO_OWNER_EMAIL','STUDIO_PASSWORD','STUDIO_SESSION_SECRET','STUDIO_REQUIRE_AUTH']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('RENDER','true')
    with TestClient(app) as client:
        assert client.get('/api/state').status_code == 503
        assert client.get('/healthz').status_code == 200

def test_tampered_expired_wrong_owner_sessions_rejected(monkeypatch):
    settings(monkeypatch)
    value = token('owner@example.com', 's'*40)
    assert valid_token(value)
    assert not valid_token(value[:-1]+'x')
    assert not valid_token(token('other@example.com','s'*40))
    monkeypatch.setattr('ai_music_studio.auth.time.time',lambda:10**12)
    assert not valid_token(value)
