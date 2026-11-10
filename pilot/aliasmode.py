"""AliasMode's documented local API; no cloud accounts or external listeners."""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

from .core import PilotError


def profile_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise PilotError('Invalid AliasMode profile ID')
    return value


def local_cdp_address(value):
    """A server response cannot redirect browser control away from this machine."""
    if not isinstance(value, str):
        raise PilotError('AliasMode returned no browser debugging address')
    try:
        u = urlparse(value)
        valid = (u.scheme == 'ws' and u.hostname in ('127.0.0.1', 'localhost') and
                 u.port and 1024 <= u.port <= 65535 and not u.username and not u.password and
                 re.fullmatch(r'/devtools/browser/[A-Za-z0-9_-]+', u.path) and
                 not u.query and not u.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise PilotError('AliasMode debugging address must stay on loopback')
    return 'ws://127.0.0.1:' + str(u.port) + u.path


def request(port, path, *, query=None, payload=None, timeout=120):
    from .gateway import NoRedirect
    if type(port) is not int or not 1024 <= port <= 65535:
        raise PilotError('Invalid AliasMode local API port')
    allowed = {'/api/v1/status', '/api/v1/user/list', '/api/v1/user/create',
               '/api/v1/browser/start', '/api/v1/browser/stop', '/api/v1/browser/active'}
    if path not in allowed:
        raise PilotError('Unsupported AliasMode operation')
    url = 'http://127.0.0.1:' + str(port) + path
    if query:
        url += '?' + urlencode(query)
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('oversized response')
        result = json.loads(raw)
        if not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 0:
            raise ValueError('API failure')
        return result.get('data')
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        # API errors may contain profile/account data; do not echo them into logs.
        raise PilotError('AliasMode local API failed; check the desktop app, Local mode, and profile ID') from None


def open_profile(port, ident):
    data = request(port, '/api/v1/browser/start', query={'user_id': profile_id(ident)})
    if not isinstance(data, dict) or not isinstance(data.get('ws'), dict):
        raise PilotError('AliasMode returned no browser debugging address')
    return local_cdp_address(data['ws'].get('puppeteer'))


def stop_profile(port, ident):
    request(port, '/api/v1/browser/stop', query={'user_id': profile_id(ident)})
