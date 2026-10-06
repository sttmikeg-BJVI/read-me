"""Private Supabase object storage. Credentials stay on the server."""
import json
import mimetypes
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from fastapi import HTTPException

MAX_AUDIO_BYTES = 50 * 1024 * 1024

def configured():
    return bool(os.getenv('SUPABASE_URL') or os.getenv('SUPABASE_SECRET_KEY') or os.getenv('STUDIO_REQUIRE_CLOUD_STORAGE') == '1')

def _config():
    url = os.getenv('SUPABASE_URL', '').rstrip('/')
    key = os.getenv('SUPABASE_SECRET_KEY', '')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.supabase.co') or not key:
        raise HTTPException(503, 'Cloud storage configuration is incomplete')
    return url, key, os.getenv('SUPABASE_STORAGE_BUCKET', 'music-library')

def _request(method, name, data=None, content_type='application/json', missing_ok=False):
    url, key, bucket = _config()
    route = '/object/authenticated/' if method == 'GET' else '/object/'
    headers = {'apikey': key, 'Content-Type': content_type}
    # New secret keys authenticate via apikey; legacy service_role JWTs also need Bearer.
    if not key.startswith('sb_secret_'):
        headers['Authorization'] = 'Bearer ' + key
    if method == 'POST':
        headers['x-upsert'] = 'true'
    request = Request(url + '/storage/v1' + route + quote(bucket, safe='') + '/' + quote(name, safe='/'), data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            return response.read()
    except HTTPError as error:
        # Storage often returns HTTP 400 with an embedded 404 for missing objects.
        try:
            detail = json.loads(error.read())
        except (ValueError, OSError):
            detail = {}
        if missing_ok and detail.get('error') in {'NoSuchKey', 'not_found'} and 'bucket' not in str(detail.get('message', '')).lower():
            return None
        if error.code == 413:
            raise HTTPException(413, 'File exceeds cloud storage size limit') from None
        raise HTTPException(503, 'Cloud storage request failed; nothing was reported as saved') from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(503, 'Cloud storage unavailable; retry saving when connected') from None

def load_rows(name):
    raw = _request('GET', 'metadata/' + name, missing_ok=True)
    if raw is None:
        return []
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError()
        return rows
    except ValueError:
        raise HTTPException(503, 'Saved cloud library could not be read') from None

def save_rows(name, rows):
    _request('POST', 'metadata/' + name, json.dumps(rows).encode('utf-8'))

def persist_audio(path):
    path = Path(path)
    if not configured():
        return str(path)
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise HTTPException(413, 'Free storage supports audio files up to 50 MB. Upload a smaller copy.')
    name = path.name
    _request('POST', 'audio/' + name, path.read_bytes(), mimetypes.guess_type(name)[0] or 'application/octet-stream')
    return 'supabase:' + name

def restore_audio(reference, cache_dir):
    if not reference.startswith('supabase:'):
        return reference
    name = reference[len('supabase:'):]
    if not name or Path(name).name != name or '/' in name or '\\' in name:
        raise HTTPException(400, 'Invalid saved audio reference')
    target = Path(cache_dir) / name
    if not target.exists():
        raw = _request('GET', 'audio/' + name)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return str(target)
