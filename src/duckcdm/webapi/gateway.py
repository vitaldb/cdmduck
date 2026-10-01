"""Gateway mode: accept only requests relayed by a trusted authenticating reverse proxy.

A request is accepted when all of these hold:
  * the TCP peer address starts with one of the trusted prefixes (the gateway's network),
  * the gateway marker header has the expected value (e.g. X-Auth-Method: parent_gateway),
  * a user id is present in one of the identity headers,
  * the user id is listed in the allow-list file (re-read on change, so edits apply without restart).
Everything else gets 401 (not from the gateway) or 403 (user not allowed).
"""
import os
import threading

from fastapi.responses import HTMLResponse, JSONResponse

USER_HEADERS = ('X-Auth-User-ID', 'User-ID', 'X-Auth-Subject')


class Gateway:
    def __init__(self, trusted_prefixes=('172.23.',), marker_header='X-Auth-Method', marker_value='parent_gateway',
                 allow_file=None, log_file=None):
        self.trusted = tuple(p for p in trusted_prefixes if p)
        self.marker_header = marker_header
        self.marker_value = marker_value
        self.allow_file = allow_file
        self._allow = set()
        self._mtime = None
        self._lock = threading.Lock()
        self.log_file = log_file
        self.denied_message = ('Access is limited to researchers with an approved data review (DRB) for this '
                               'data source. Please contact the data steward to be added.')

    def allowed_users(self):
        if not self.allow_file:
            return None
        try:
            m = os.stat(self.allow_file).st_mtime
        except OSError:
            return set()
        with self._lock:
            if m != self._mtime:
                with open(self.allow_file, encoding='utf-8') as f:
                    self._allow = {ln.split('#')[0].strip() for ln in f if ln.split('#')[0].strip()}
                self._mtime = m
            return self._allow

    def _log(self, request, peer, verdict):
        """Audit line: time, peer, verdict, path, identity-related headers and the names of all other headers."""
        if not self.log_file:
            return
        import json
        import time
        h = request.headers
        ident = {k: v for k, v in h.items() if k.lower().startswith(('x-auth', 'user-', 'x-forwarded', 'x-real-ip'))}
        rec = {'t': time.strftime('%Y-%m-%d %H:%M:%S'), 'peer': peer, 'verdict': verdict, 'path': request.url.path,
               'identity': ident, 'headers': sorted(k for k in h.keys() if k not in ident)}
        with self._lock, open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    def identify(self, request):
        user, err = self._identify(request)
        self._log(request, request.client.host if request.client else '',
                  'ok' if err is None else str(err.status_code))
        return user, err

    def _identify(self, request):
        """(user id, None) or (None, error response)."""
        peer = request.client.host if request.client else ''
        if not peer.startswith(self.trusted):
            return None, JSONResponse({'message': 'not via gateway'}, status_code=401)
        if self.marker_header and request.headers.get(self.marker_header) != self.marker_value:
            return None, JSONResponse({'message': 'not via gateway'}, status_code=401)
        user = next((request.headers.get(h) for h in USER_HEADERS if request.headers.get(h)), None)
        if not user:
            return None, JSONResponse({'message': 'no user identity'}, status_code=401)
        allow = self.allowed_users()
        if allow is not None and user not in allow:
            return None, JSONResponse({'message': f'user {user} is not authorized for this data source'},
                                      status_code=403)
        return user, None

    DENIED_PAGE = (
        '<!doctype html><meta charset="utf-8"><title>DuckCDM</title>'
        '<body style="font-family:sans-serif;max-width:40em;margin:4em auto;line-height:1.6">'
        '<h2>DuckCDM</h2><p>{msg}</p></body>')

    def install(self, app):
        @app.middleware('http')
        async def _gate(request, call_next):
            user, err = self.identify(request)
            if err is not None:
                if request.url.path.startswith('/WebAPI') or 'text/html' not in request.headers.get('accept', ''):
                    return err
                msg = (self.denied_message if err.status_code == 403 else
                       'This service is only available through the institutional research portal.')
                return HTMLResponse(self.DENIED_PAGE.format(msg=msg), status_code=err.status_code)
            request.state.user = user
            return await call_next(request)
