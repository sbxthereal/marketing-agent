"""Local ixBrowser API and fixed profile routing shared with AliasMode."""
import copy
import json
import re
import urllib.request
from urllib.parse import urlparse

from .core import PilotError, require_live_permission


def validate_profiles(config):
    backend = config.get('browser_backend', 'chrome')
    if backend not in ('chrome', 'ixbrowser', 'aliasmode'):
        raise PilotError('browser_backend must be chrome, ixbrowser, or aliasmode')
    if backend == 'chrome':
        return
    settings = config.get(backend, {})
    if not isinstance(settings, dict):
        raise PilotError('Invalid managed browser settings')
    label = 'AliasMode' if backend == 'aliasmode' else 'ixBrowser'
    port = settings.get('port', 50400 if backend == 'aliasmode' else 53200)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise PilotError('Invalid ' + label + ' local API port')
    profiles = settings.get('profiles')
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 2:
        raise PilotError('Select one or two existing ' + label + ' profiles for the pilot')
    ids, communities, accounts = set(), set(), set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise PilotError('Invalid ' + label + ' profile')
        ident, username, subs = profile.get('id'), profile.get('reddit_username'), profile.get('subreddits')
        valid_id = (type(ident) is int and ident > 0) if backend == 'ixbrowser' else (
            isinstance(ident, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident))
        if not valid_id or ident in ids:
            raise PilotError(label + ' profile IDs must be valid and unique')
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,20}', username) or username.lower() in accounts:
            raise PilotError('Each ' + label + ' profile needs its distinct expected Reddit username')
        if not isinstance(subs, list) or not subs or any(not isinstance(s, str) for s in subs):
            raise PilotError('Assign communities to each ' + label + ' profile')
        for sub in subs:
            if sub.lower() in communities:
                raise PilotError('Assign each community to exactly one ' + label + ' profile')
            communities.add(sub.lower())
        ids.add(ident); accounts.add(username.lower())
    if communities != {s.lower() for s in config['subreddits']}:
        raise PilotError(label + ' assignments must exactly match the selected communities')


def local_debug_address(value):
    if not isinstance(value, str):
        raise PilotError('ixBrowser returned no debugging address')
    u = urlparse(value if '://' in value else 'http://' + value)
    try:
        valid = (u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost') and
                 u.port and 1024 <= u.port <= 65535 and not u.username and not u.password and
                 u.path in ('', '/') and not u.query and not u.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise PilotError('ixBrowser debugging address must stay on loopback')
    return 'http://127.0.0.1:' + str(u.port)


def open_profile(port, profile_id):
    from .gateway import NoRedirect
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/api/v2/profile-open',
        data=json.dumps({'profile_id':profile_id,'load_profile_info_page':False,
                         'load_extensions':True,'cookies_backup':False,'args':[]}).encode(),
        headers={'Content-Type':'application/json'}, method='POST')
    # Local control traffic must never go through an environment/company proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=45) as response:
            body = response.read(65537)
        if len(body) > 65536:
            raise ValueError('oversized response')
        result = json.loads(body)
        if result.get('error', {}).get('code') != 0:
            raise ValueError('API failure')
        return local_debug_address(result['data']['debugging_address'])
    except PilotError:
        raise
    except Exception:
        raise PilotError('ixBrowser could not open the selected profile; check the desktop client, local API access, and profile ID') from None


class ProfilePool:
    """Sequential profiles with fixed community ownership, no rotation or failover."""
    def __init__(self, root, config, catalog, workflow):
        require_live_permission(config, catalog)
        validate_profiles(config)
        self.root, self.config, self.catalog, self.workflow = root, config, catalog, workflow
        self.backend = config['browser_backend']
        self.readers = {}
        self.assignments = {s.lower():p for p in config[self.backend]['profiles'] for s in p['subreddits']}

    def _reader(self, profile):
        if profile['id'] not in self.readers:
            from .browser import BrowserReader
            child = copy.deepcopy(self.config)
            child['subreddits'] = profile['subreddits']
            child[self.backend + '_profile_id'] = profile['id']
            child['browser_profile_label'] = profile.get('name') or self.backend + ' profile ' + str(profile['id'])
            child['publishing']['reddit_username'] = profile['reddit_username']
            prefix = ':ix:' if self.backend == 'ixbrowser' else ':alias:'
            child['search_state_suffix'] = prefix + str(profile['id'])
            self.readers[profile['id']] = BrowserReader(self.root, child, self.catalog, self.workflow)
        return self.readers[profile['id']]

    def _for_post(self, post):
        profile = self.assignments.get(post['subreddit'].lower())
        if not profile:
            raise PilotError('No managed browser profile is assigned to this community')
        return self._reader(profile)

    def collect(self):
        output = []
        for profile in self.config[self.backend]['profiles']:
            output.extend(self._reader(profile).collect())
        return output

    def enrich(self, post):
        return self._for_post(post).enrich(post)

    def publish(self, job, workflow):
        return self._for_post(job['post']).publish(job, workflow)

    def verify_publication(self, job, url):
        return self._for_post(job['post']).verify_publication(job, url)

    def verify_attention(self, attention):
        if attention.get('backend') != self.backend:
            raise PilotError('Browser backend changed; restore the original profile assignment')
        profile = next((p for p in self.config[self.backend]['profiles'] if p['id'] == attention.get('profile_id')), None)
        if not profile or profile['reddit_username'] != attention.get('expected_account'):
            raise PilotError('Original browser profile/account is no longer configured')
        return self._reader(profile).verify_attention(attention)

    def close(self):
        error = None
        try:
            for reader in self.readers.values():
                try:
                    reader.close()
                except Exception as exception:
                    error = exception
        finally:
            self.readers.clear()
        if error:
            raise error
