"""Optional shared competition gate. Credentials never enter the React bundle."""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = 'owedience_demo_session'
SESSION_SECONDS = 24 * 60 * 60


def session_signature(code, message):
    return hmac.new(code.encode(), message.encode(), hashlib.sha256).hexdigest()


def session_valid(code, value):
    try:
        issued, nonce, signature = value.split('.')
        age = time.time() - int(issued)
        return 0 <= age < SESSION_SECONDS and len(nonce) == 43 and hmac.compare_digest(
            signature, session_signature(code, issued + '.' + nonce))
    except (ValueError, TypeError, AttributeError):
        return False


def access_page(error='', status=200):
    # Error text is fixed by the server; entered credentials are never echoed.
    return HTMLResponse('''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Owedience access</title><style>
*{box-sizing:border-box}body{margin:0;background:#fafbf7;color:#173f32;font:16px system-ui,sans-serif;min-height:100vh;display:grid;place-items:center;padding:24px}
main{width:100%;max-width:400px;background:white;border:1px solid #e3e7dd;border-radius:18px;padding:36px}h1{font-size:32px;letter-spacing:-1px;margin:0 0 12px}p{color:#657066;line-height:1.5}label{display:block;margin:28px 0 8px}input,button{width:100%;padding:14px;border-radius:9px;font:inherit}input{border:1px solid #cbd4c7}button{margin-top:16px;border:0;background:#173f32;color:white;cursor:pointer}.error{color:#a13b28}
</style></head><body><main><h1>owedience.</h1><p>Private competition prototype</p>
<form method="post" action="/demo/access"><label for="code">Access code</label>
<input id="code" name="access_code" type="password" autocomplete="current-password" required maxlength="512">
<button type="submit">Enter</button></form>''' + (f'<p class="error" role="alert">{error}</p>' if error else '') + '''</main></body></html>''', status_code=status, headers={
        'Cache-Control':'no-store',
        'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
        'X-Content-Type-Options':'nosniff',
    })


class DemoAccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.attempts = defaultdict(deque)

    async def dispatch(self, request, call_next):
        code = os.getenv('DEMO_ACCESS_CODE', '')
        if not code or request.url.path in ('/health', '/api/health'):
            return await call_next(request)
        origin = request.headers.get('origin')
        expected_origin = str(request.base_url).rstrip('/')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != expected_origin:
            return JSONResponse({'detail':'Same-origin request required'}, status_code=403)
        if request.url.path == '/demo/access' and request.method == 'POST':
            # Bound credential guesses without logging or persisting submitted codes.
            ip = request.client.host if request.client else 'unknown'
            attempts = self.attempts[ip]
            timestamp = time.monotonic()
            while attempts and timestamp - attempts[0] > 60:
                attempts.popleft()
            if len(attempts) >= 10:
                return access_page('Please wait a minute before trying again.', 429)
            attempts.append(timestamp)
            if request.headers.get('content-type','').split(';')[0] != 'application/x-www-form-urlencoded':
                return access_page('Enter the access code in the form.', 400)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4096:
                    return access_page('Access code is too long.', 400)
            try:
                supplied = parse_qs(body.decode('utf-8'), max_num_fields=4).get('access_code', [''])[0]
            except (ValueError, UnicodeDecodeError):
                return access_page('Please check the access code.', 401)
            if not hmac.compare_digest(supplied.encode(), code.encode()):
                return access_page('Please check the access code.', 401)
            message = str(int(time.time())) + '.' + secrets.token_urlsafe(32)
            response = RedirectResponse('/', status_code=303)
            response.set_cookie(COOKIE, message + '.' + session_signature(code, message),
                                httponly=True, secure=request.url.scheme == 'https', samesite='strict', path='/')
            response.headers['Cache-Control'] = 'no-store'
            attempts.clear()
            return response
        if not session_valid(code, request.cookies.get(COOKIE, '')):
            if request.url.path.startswith('/api/'):
                return JSONResponse({'detail':'Demo access required'}, status_code=401)
            return access_page()
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
