"""Platform routing, one persistent account per channel, and autonomous workers.

All channels share the existing SQLite outbox, OneAPI budget and posting limits.
The legacy ``subreddit`` column is a destination key for non-Reddit items.
"""
import copy
import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import urlparse, urlunparse

from .core import PilotError

PLATFORMS = ('dev', 'indiehackers', 'discourse')


def origin_url(value):
    try:
        u = urlparse(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443):
            return False
        if u.path not in ('', '/') or u.query or u.fragment or '.' not in u.hostname:
            return False
        try:
            if not ipaddress.ip_address(u.hostname).is_global: return False
        except ValueError:
            if u.hostname.endswith(('.local', '.internal', '.localhost')): return False
        return True
    except (ValueError, TypeError):
        return False


def same_origin(url, channel):
    try:
        u, base = urlparse(url), urlparse(channel['origin'])
        return bool(origin_url(channel['origin']) and u.scheme == 'https' and u.hostname == base.hostname
                    and not u.username and not u.password and u.port in (None, 443))
    except (ValueError, TypeError, KeyError):
        return False


def source_url(url, channel):
    if not same_origin(url, channel): return False
    u = urlparse(url)
    if u.query or u.fragment: return False
    patterns = {'dev': r'^/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+/?$',
                'indiehackers': r'^/post/[a-zA-Z0-9_-]+/?$',
                'discourse': r'^/t/[a-zA-Z0-9_-]+/[0-9]+(?:/[0-9]+)?/?$'}
    if not re.fullmatch(patterns[channel['type']], u.path): return False
    return channel['type'] != 'dev' or u.path.split('/')[1] not in ('t', 'settings', 'search', 'organizations')


def published_url(url, post, channel):
    if not same_origin(url, channel): return False
    u = urlparse(url)
    clean = urlunparse((u.scheme, u.netloc, u.path, '', '', ''))
    if not source_url(clean, channel) or u.query: return False
    if post['kind'] == 'campaign': return clean != post['url']
    src = urlparse(post['url'])
    if channel['type'] == 'discourse':
        parts, original = u.path.strip('/').split('/'), src.path.strip('/').split('/')
        return len(parts) == 4 and parts[:3] == original[:3] and int(parts[3]) > 1
    return u.path.rstrip('/') == src.path.rstrip('/') and bool(re.fullmatch(r'comment[-_a-zA-Z0-9]+', u.fragment))


def validate_channels(config):
    channels = config.setdefault('channels', [])
    if not isinstance(channels, list) or len(channels) > 8:
        raise PilotError('Configure at most eight distinct channels; one account each')
    ids, origins = set(), set()
    for ch in channels:
        if not isinstance(ch, dict) or ch.get('type') not in PLATFORMS:
            raise PilotError('Unsupported channel type')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,30}', str(ch.get('id', ''))) or ch['id'] == 'reddit' or ch['id'] in ids:
            raise PilotError('Channel IDs must be unique and cannot be reddit')
        ids.add(ch['id'])
        ch.setdefault('enabled', False)
        ch.setdefault('origin', '')
        ch.setdefault('feeds', [])
        ch.setdefault('campaign_plan', [])
        ch.setdefault('policy', {})
        ch.setdefault('username', '')
        ch.setdefault('profile_count', 1)
        ch.setdefault('poll_seconds', 1800)
        if type(ch['enabled']) is not bool or type(ch['profile_count']) is not int or ch['profile_count'] != 1:
            raise PilotError('Use one fixed account/profile per channel')
        if type(ch['poll_seconds']) is not int or not 900 <= ch['poll_seconds'] <= 86400:
            raise PilotError('Channel scans must be 15 minutes to 24 hours apart')
        if not isinstance(ch['username'], str) or (ch['username'] and not re.fullmatch(r'[\w.-]{2,60}', ch['username'])):
            raise PilotError('Invalid expected channel username')
        if ch['origin']:
            fixed = {'dev': 'https://dev.to', 'indiehackers': 'https://www.indiehackers.com'}.get(ch['type'])
            if not origin_url(ch['origin']) or (fixed and ch['origin'] != fixed) or ch['origin'] in origins:
                raise PilotError('Use one fixed public HTTPS origin per channel')
            origins.add(ch['origin'])
        elif ch['enabled']:
            raise PilotError('An enabled channel needs its forum origin')
        if not isinstance(ch['feeds'], list) or len(ch['feeds']) > 4 or any(not same_origin(u, ch) for u in ch['feeds']):
            raise PilotError('Provide at most four feeds on the configured origin')
        if ch.get('compose_url') and not same_origin(ch['compose_url'], ch):
            raise PilotError('Composer must stay on the configured origin')
        p = ch['policy']
        if not isinstance(p, dict): raise PilotError('Invalid channel policy')
        for key in ('collection', 'automated_replies', 'automated_posts', 'links_allowed', 'site_exception'):
            if type(p.get(key, False)) is not bool: raise PilotError('Invalid channel permission')
        if any(not isinstance(p.get(k, ''), str) or len(p.get(k, '')) > 3000 for k in ('reference', 'notes')):
            raise PilotError('Invalid channel policy reference')
        from .automation import validate_config
        validate_config(channel_config(config, ch))


def channel_config(base, ch, username=None):
    config = copy.deepcopy(base)
    config['channels'] = []
    config['platform'] = ch['id']
    config['channel'] = ch
    if ch['type'] == 'dev': config['max_output_tokens'] = max(2200, config['max_output_tokens'])
    config['browser_backend'] = 'chrome'
    config['subreddits'] = [ch['id']]
    p = ch['policy']
    identity = ch['username'] if username is None else username
    config['publishing'].update(mode='automatic', enabled=bool(ch['enabled'] and identity), reddit_username=identity,
                                permission_confirmed=True, permission_reference=p.get('reference', ''))
    config['permissions'] = dict(browser_collection=p.get('collection', False), ai_processing=True,
                                community_rules_reviewed=bool(p.get('reference')), reference=p.get('reference', ''))
    config['community_policies'] = {ch['id']: dict(p, standalone_posts=p.get('automated_posts', False))}
    config['automation'] = dict(campaign_interval_hours=base.get('automation', {}).get('campaign_interval_hours', 168),
                                campaign_plan=[dict(plan, subreddit=ch['id']) for plan in ch['campaign_plan']])
    return config


def paused(store, config):
    return store.setting('paused') == '1' or store.setting('paused.'+config.get('platform', 'reddit')) == '1'


def require_channel_ready(store, config, post=None):
    platform = config.get('platform', 'reddit')
    if post and post.get('platform', 'reddit') != platform:
        raise PilotError('A publication cannot cross platform/account boundaries')
    if store.setting('paused.'+platform) == '1': raise PilotError('Channel is paused')
    if platform == 'reddit': return
    ch = config['channel']
    if not ch['enabled']: raise PilotError('Channel is disabled')
    if ch['type'] == 'indiehackers' and not ch['policy'].get('site_exception'):
        raise PilotError('Indie Hackers automation needs a site-specific permission exception')
    if not ch['feeds']: raise PilotError('Configure at least one forum feed')
    p = ch['policy']
    if not p.get('collection') or not p.get('reference') or not p.get('notes'):
        raise PilotError('Channel collection rules have not been configured')
    if type(p.get('reviewed_at')) not in (int,float) or not 0 <= time.time()-p['reviewed_at'] <= 30*86400:
        raise PilotError('Channel rules need a fresh review before activity continues')
    if not config['publishing']['reddit_username']: raise PilotError('Sign in and connect this channel account first')


def channel_style(post, campaign=False):
    kind = post.get('platform_type', post.get('platform', 'reddit'))
    if kind == 'reddit': return ''
    if campaign:
        return ('\nThis destination is '+kind+', not Reddit. Use a practical developer tutorial with a proposed prompt, '
                'steps, tests and limitations; no fabricated execution results. For DEV use 300–600 words with short '
                'Markdown headings. For founder forums use 110–220 words and a concrete proposed build. '
                'Do not claim to be a customer or a human author. Code adds team and automation disclosures.')
    return ('\nDestination: '+kind+'. Answer the specific question directly in a helpful developer/founder tone. '
            'Use 40–100 words, include a concrete proposed next step and recommend MeDo only if it solves the stated need. '
            'Do not reply to an unrelated article merely to promote. Do not fabricate personal experience. '
            'Code adds team and automation disclosures.')


class Channels:
    """Runs on the existing worker thread; HTTP handlers enqueue commands only."""
    def __init__(self, root, base, catalog, store, flow):
        self.root, self.base, self.catalog, self.store, self.flow = root, base, catalog, store, flow
        self.browsers, self.next_scan, self.failures = {}, {}, {}

    def config(self, ch):
        account = self.store.setting('channel.account.'+ch['id'], ch['username'])
        return channel_config(self.base, ch, account)

    def browser(self, ch, cfg):
        if ch['id'] not in self.browsers:
            from .forum_browser import ForumBrowser
            self.browsers[ch['id']] = ForumBrowser(self.root, cfg)
        self.browsers[ch['id']].config = cfg
        return self.browsers[ch['id']]

    def command(self, channel_id, action):
        if action not in ('open', 'connect', 'check', 'pause', 'resume'):
            raise PilotError('Invalid channel action')
        if channel_id != 'reddit' and not any(ch['id'] == channel_id for ch in self.base['channels']): raise PilotError('Unknown channel')
        self.store.set_setting('channel.command.'+channel_id, action)

    def alert(self, ch, message):
        self.store.set_setting('paused.'+ch['id'], '1')
        self.store.set_setting('channel.alert.'+ch['id'], message[:300])

    def controls(self, ch, cfg):
        key = 'channel.command.'+ch['id']
        action = self.store.setting(key)
        if not action: return
        self.store.set_setting(key, '')  # No command replay after a crash.
        if action == 'pause':
            self.alert(ch, 'Paused by operator'); return
        browser = self.browser(ch, cfg)
        if action == 'open':
            browser.open_login()
            self.alert(ch, 'Sign in in this Windows browser, then choose Connect account.'); return
        identity = browser.identity()
        if not identity: raise PilotError('Sign-in or CAPTCHA is still pending in the same browser profile')
        expected = cfg['publishing']['reddit_username']
        if expected and identity.casefold() != expected.casefold():
            raise PilotError('Signed-in account differs from the bound account; switching is blocked')
        with self.store.db() as c:
            if c.execute("SELECT 1 FROM outbox o JOIN items i ON i.id=o.item_id WHERE o.state='uncertain' AND json_extract(i.payload,'$.platform')=?", (ch['id'],)).fetchone():
                raise PilotError('An uncertain publication needs reconciliation before this channel resumes')
        if action == 'connect' or expected:
            self.store.set_setting('channel.account.'+ch['id'], identity)
            self.store.set_setting('paused.'+ch['id'], '0')
            self.store.set_setting('channel.alert.'+ch['id'], '')
            self.failures[ch['id']] = 0
            self.next_scan[ch['id']] = 0
            cfg = self.config(ch)
            # Only failed preflights can resume automatically; a submit attempt
            # with uncertain outcome is never retried.
            with self.store.db() as c:
                pending = c.execute("SELECT o.item_id,o.draft_hash FROM outbox o JOIN items i ON i.id=o.item_id WHERE o.state='blocked' AND json_extract(i.payload,'$.platform')=?",(ch['id'],)).fetchall()
            for row in pending:
                try: self.flow.retry_preflight(row['item_id'],row['draft_hash'],cfg)
                except PilotError: pass

    def tick(self):
        from .automation import Automation
        from .core import validate_post, require_live_permission
        from .gateway import Gateway, analyze
        from .campaigns import analyze_campaign
        from .discovery import timestamp
        from .quality import hold, context_skip
        for ch in self.base.get('channels', []):
            cfg = self.config(ch)
            try:
                self.controls(ch, cfg)
                cfg = self.config(ch)
                if paused(self.store, cfg): continue
                try:
                    require_channel_ready(self.store, cfg)
                    require_live_permission(cfg, self.catalog)
                except PilotError:
                    continue  # Readiness is visible in status; no noisy retry loop.
                automation = Automation(self.store, self.flow, cfg, self.catalog)
                automation.schedule()
                browser = self.browser(ch, cfg)
                if time.time() >= self.next_scan.get(ch['id'], 0):
                    started = time.time()
                    posts = browser.collect()
                    new = sum(self.store.ingest(validate_post(p, cfg)) for p in posts)
                    self.flow.record_scan(ch['id'], started, len(ch['feeds']), len(posts), new, len(posts)-new, 'ok', source=ch['type'])
                    self.next_scan[ch['id']] = time.time()+ch['poll_seconds']
                    self.store.set_setting('channel.last_scan.'+ch['id'], str(time.time()))
                gateway = None
                for _ in range(min(2, cfg['max_items_per_cycle'])):
                    post = self.store.claim(cfg['max_attempts'], platform=ch['id'])
                    if not post: break
                    try:
                        if post['kind'] != 'campaign':
                            post = browser.enrich(post)
                            post = validate_post(post, cfg)
                            self.flow.enrich(post)
                            at = timestamp(post.get('posted_at', ''))
                            if not at or time.time()-at > cfg['lookback_hours']*3600:
                                self.store.complete(post['id'], dict(decision='skip', reason='Missing timestamp or older than the discovery window', template_id='', evidence='', claim_ids=[], draft=''))
                                continue
                            skip = context_skip(post,cfg['publishing']['reddit_username'])
                            if skip:
                                self.store.complete(post['id'],dict(decision='skip',reason=skip,template_id='',evidence='',claim_ids=[],draft=''))
                                continue
                        if gateway is None: gateway = Gateway(self.root/'.env')
                        fn = analyze_campaign if post['kind'] == 'campaign' else analyze
                        selected = [t for t in self.catalog if t['id'] == post['planned_template_id']] if post.get('planned_template_id') else self.catalog
                        result = fn(gateway, post, selected, cfg, self.store)
                        if result is None:
                            self.store.defer_budget(post['id']); break
                        if result['decision'] == 'recommend' and self.flow.duplicate_draft(post['id'], result['draft']):
                            result = hold(result, 'Similar content already exists; no duplicate promotion')
                        self.store.complete(post['id'], result)
                    except Exception as error:
                        from .browser import BrowserAttention
                        if isinstance(error,BrowserAttention): self.store.defer_attention(post['id'])
                        else: self.store.fail(post['id'], str(error) if isinstance(error, PilotError) else 'Channel drafting failed', cfg['max_attempts'])
                        raise
                automation.queue_ready()
                job = self.flow.claim_publication(cfg)
                if job:
                    try:
                        url = browser.publish(job, self.flow)
                        self.flow.publication_done(job, url, cfg)
                    except Exception:
                        self.flow.publication_failed(job['item_id'], 'Publication stopped; verify this profile before resuming')
                        raise
                self.failures[ch['id']] = 0
                self.store.set_setting('channel.error.'+ch['id'],'')
            except Exception as error:
                from .browser import BrowserAttention
                self.failures[ch['id']] = self.failures.get(ch['id'], 0)+1
                message = str(error) if isinstance(error, PilotError) else 'Browser operation failed; check the Windows session'
                self.store.set_setting('channel.error.'+ch['id'], message[:300])
                with self.store.db() as c:
                    uncertain = c.execute("SELECT 1 FROM outbox o JOIN items i ON i.id=o.item_id WHERE o.state='uncertain' AND json_extract(i.payload,'$.platform')=?",(ch['id'],)).fetchone()
                if isinstance(error, BrowserAttention) or self.failures[ch['id']] >= 3 or uncertain:
                    self.alert(ch, message)
                self.next_scan[ch['id']] = time.time()+min(3600, 300*2**self.failures[ch['id']])

    def status(self):
        from .automation import Automation
        reddit = Automation(self.store,self.flow,self.base,self.catalog).status()
        blockers = list(reddit['blockers'])
        if not self.base['publishing'].get('reddit_username'): blockers.append('Sign in and connect the Reddit account')
        if self.store.setting('paused.reddit') == '1': blockers.append('Reddit channel is paused')
        with self.store.db() as c:
            counts = dict(c.execute("SELECT status,COUNT(*) FROM items WHERE COALESCE(json_extract(payload,'$.platform'),'reddit')='reddit' GROUP BY status"))
        output = [dict(id='reddit',platform='reddit',name='Reddit',profiles=1,account=self.base['publishing'].get('reddit_username',''),
                       state='ready' if not blockers else 'waiting',blockers=blockers,alert='',counts=counts,last_scan='')]
        for ch in self.base.get('channels', []):
            cfg = self.config(ch)
            blockers = []
            from .core import require_live_permission
            for check in (lambda: require_channel_ready(self.store, cfg), lambda: require_live_permission(cfg, self.catalog)):
                try: check()
                except PilotError as e: blockers.append(str(e))
            if self.store.setting('paused') == '1': blockers.append('All channels are paused')
            with self.store.db() as c:
                counts = dict(c.execute("SELECT status,COUNT(*) FROM items WHERE json_extract(payload,'$.platform')=? GROUP BY status", (ch['id'],)))
            output.append(dict(id=ch['id'], platform=ch['type'], name=ch.get('name', ch['id']), profiles=1 if ch['origin'] else 0,
                               account=cfg['publishing']['reddit_username'], state='ready' if not blockers else 'waiting', blockers=blockers,
                               alert=self.store.setting('channel.alert.'+ch['id']), counts=counts,
                               last_scan=self.store.setting('channel.last_scan.'+ch['id']),
                               error=self.store.setting('channel.error.'+ch['id'])))
        return output

    def close(self):
        for browser in self.browsers.values(): browser.close()
