"""Single-owner sign-in for a separately deployed private studio."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlsplit
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = 'music_studio_session'
LOGIN = '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Music Studio sign in</title><style>body{background:#111;color:#eee;font:16px Arial;margin:40px auto;padding:20px;max-width:420px}input,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0;background:#222;color:#eee;border:1px solid #555;border-radius:7px}</style></head><body><h1>Music Studio</h1><p>Your private songwriting workspace.</p><form method="post" action="/login"><label>Email<input name="email" type="email" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in</button></form></body></html>'''

def config():
    return os.getenv('STUDIO_OWNER_EMAIL', ''), os.getenv('STUDIO_PASSWORD', ''), os.getenv('STUDIO_SESSION_SECRET', '')

def enabled():
    return bool(os.getenv('RENDER') or os.getenv('STUDIO_REQUIRE_AUTH') == '1' or any(config()))

def configured():
    email, password, secret = config()
    return bool(email and len(password) >= 16 and len(secret) >= 32)

def token(email, secret):
    payload = base64.urlsafe_b64encode(json.dumps({'email': email, 'expires': time.time()+7*86400, 'nonce': secrets.token_hex(16)}, separators=(',', ':')).encode()).decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload+'.'+signature

def valid_token(value):
    email, _, secret = config()
    try:
        payload, signature = value.split('.')
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(expected, signature):
            return False
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data['expires'] > time.time() and hmac.compare_digest(data['email'], email)
    except (ValueError, KeyError, TypeError):
        return False

def same_origin(request):
    origin = request.headers.get('origin')
    if not origin:
        return request.headers.get('sec-fetch-site') != 'cross-site'
    source = urlsplit(origin)
    return source.scheme == request.url.scheme and source.netloc == request.url.netloc

class StudioAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path == '/healthz' or not enabled():
            return await call_next(request)
        if not configured():
            return JSONResponse({'detail': 'Private studio sign-in has not been configured.'}, status_code=503)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not same_origin(request):
            return JSONResponse({'detail': 'Cross-site request rejected.'}, status_code=403)
        if request.url.path == '/login':
            return await call_next(request)
        if not valid_token(request.cookies.get(COOKIE, '')):
            if request.method == 'GET' and 'text/html' in request.headers.get('accept', ''):
                return RedirectResponse('/login', status_code=303)
            return JSONResponse({'detail': 'Sign in to your music studio.'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

def install_auth(app):
    from fastapi import Request
    app.add_middleware(StudioAuthMiddleware)
    failures = {}

    @app.get('/login', response_class=HTMLResponse, include_in_schema=False)
    async def login_page():
        return HTMLResponse(LOGIN, headers={'Cache-Control': 'no-store'})

    @app.post('/login', include_in_schema=False)
    async def login(request: Request):
        if not configured():
            return JSONResponse({'detail': 'Sign-in is not configured.'}, status_code=503)
        client = request.client.host if request.client else 'unknown'
        now = time.time()
        attempts = [t for t in failures.get(client, []) if t > now-60]
        if len(attempts) >= 5:
            return HTMLResponse('Too many attempts. Wait a minute, then try again.', status_code=429)
        form = await request.form()
        email, password, secret = config()
        submitted_email = str(form.get('email', '')).lower()
        submitted_password = str(form.get('password', ''))
        # Compare fixed-size digests; never persist submitted passwords.
        check = lambda a, b: hmac.compare_digest(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())
        if not (check(submitted_email, email.lower()) & check(submitted_password, password)):
            failures[client] = attempts+[now]
            return HTMLResponse(LOGIN+'<p>Email or password did not match.</p>', status_code=401, headers={'Cache-Control': 'no-store'})
        failures.pop(client, None)
        response = RedirectResponse('/', status_code=303)
        response.set_cookie(COOKIE, token(email, secret), max_age=7*86400, httponly=True, secure=request.url.scheme == 'https', samesite='strict')
        return response

    @app.post('/logout', include_in_schema=False)
    async def logout():
        response = RedirectResponse('/login', status_code=303)
        response.delete_cookie(COOKIE)
        return response
