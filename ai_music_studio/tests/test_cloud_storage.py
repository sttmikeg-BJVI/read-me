import io
import json
from urllib.error import HTTPError
import pytest
from fastapi import HTTPException
from ai_music_studio import cloud_storage as cloud, store
from ai_music_studio.models import SongRecord

@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://test.supabase.co')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'sb_secret_test_only')
    objects = {}
    def request(req, timeout):
        name = req.full_url.split('/music-library/')[1]
        assert req.get_header('Apikey') == 'sb_secret_test_only'
        if req.method == 'POST':
            assert req.get_header('X-upsert') == 'true'
            objects[name] = req.data
            return io.BytesIO(b'{}')
        if name not in objects:
            body = json.dumps({'error': 'not_found', 'message': 'Object not found', 'statusCode': '404'}).encode()
            raise HTTPError(req.full_url, 400, 'missing', {}, io.BytesIO(body))
        assert '/object/authenticated/' in req.full_url
        return io.BytesIO(objects[name])
    monkeypatch.setattr(cloud, 'urlopen', request)
    return objects

def test_cloud_metadata_and_audio_survive_deleted_local_cache(remote, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'UPLOADS', tmp_path)
    monkeypatch.setattr(store, 'DB', tmp_path / 'beats.json')
    monkeypatch.setattr(store, 'SONG_DB', tmp_path / 'songs.json')
    assert store.load_songs() == []
    source = tmp_path / 'song_test.wav'
    source.write_bytes(b'RIFF-test-audio')
    reference = cloud.persist_audio(source)
    assert reference == 'supabase:song_test.wav'
    song = SongRecord(id='song1', title='My song', lyrics='keep these words', audio_path=reference)
    store.save_songs([song])
    source.unlink()
    store.SONG_DB.unlink()
    reopened = store.load_songs()[0]
    assert reopened.lyrics == song.lyrics
    assert reopened.audio_path == reference
    assert cloud.restore_audio(reference, tmp_path) == str(source)
    assert source.read_bytes() == b'RIFF-test-audio'

def test_cloud_failure_and_corruption_never_return_empty_library(remote, monkeypatch):
    remote['metadata/songs.json'] = b'not-json'
    with pytest.raises(HTTPException) as error:
        cloud.load_rows('songs.json')
    assert error.value.status_code == 503
    def fail(req, timeout):
        raise HTTPError(req.full_url, 401, 'denied', {}, io.BytesIO(b'{}'))
    monkeypatch.setattr(cloud, 'urlopen', fail)
    with pytest.raises(HTTPException) as error:
        cloud.save_rows('songs.json', [])
    assert error.value.status_code == 503

def test_cloud_limits_and_paths(remote, tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, 'MAX_AUDIO_BYTES', 4)
    source = tmp_path / 'too_large.wav'
    source.write_bytes(b'12345')
    with pytest.raises(HTTPException) as error:
        cloud.persist_audio(source)
    assert error.value.status_code == 413
    assert remote == {}
    with pytest.raises(HTTPException):
        cloud.restore_audio('supabase:../private', tmp_path)

def test_required_cloud_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv('SUPABASE_URL', raising=False)
    monkeypatch.delenv('SUPABASE_SECRET_KEY', raising=False)
    monkeypatch.setenv('STUDIO_REQUIRE_CLOUD_STORAGE', '1')
    assert cloud.configured()
    with pytest.raises(HTTPException) as error:
        cloud.load_rows('songs.json')
    assert error.value.status_code == 503
