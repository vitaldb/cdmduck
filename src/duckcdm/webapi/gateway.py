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

from fastapi.responses import JSONResponse

USER_HEADERS = ('X-Auth-User-ID', 'User-ID', 'X-Auth-Subject')


class Gateway:
    def __init__(self, trusted_prefixes=('172.23.',), marker_header='X-Auth-Method', marker_value='parent_gateway',
                 allow_file=None):
        self.trusted = tuple(p for p in trusted_prefixes if p)
        self.marker_header = marker_header
        self.marker_value = marker_value
        self.allow_file = allow_file
        self._allow = set()
        self._mtime = None
        self._lock = threading.Lock()

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

    def identify(self, request):
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

    def install(self, app):
        @app.middleware('http')
        async def _gate(request, call_next):
            user, err = self.identify(request)
            if err is not None:
                return err
            request.state.user = user
            return await call_next(request)
