import contextlib
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse


class PilotError(Exception):
    pass


def company_address(value):
    """Only explicit RFC1918 IPv4 addresses, never wildcard/public binds."""
    try:
        address = ipaddress.IPv4Address(value)
        return any(address in ipaddress.IPv4Network(net) for net in
                   ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    except (ValueError, TypeError):
        return False


def publisher_for(config, subreddit):
    if config.get('browser_backend') == 'ixbrowser':
        for profile in config.get('ixbrowser',{}).get('profiles',[]):
            if subreddit.lower() in {s.lower() for s in profile['subreddits']}:
                return profile['reddit_username']
        return ''
    return config.get('publishing',{}).get('reddit_username','')


def utc_day():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def load_config(root, filename='config.json'):
    root = Path(root)
    path = root / filename
    if not path.exists():
        path = root / 'config.example.json'
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if config.get('source') not in ('demo', 'browser') or config.get('ai') not in ('fixture', 'oneapi'):
        raise PilotError('Invalid source or AI mode')
    if config.get('network_dashboard_host') and not company_address(config['network_dashboard_host']):
        raise PilotError('network_dashboard_host must be an explicit private IPv4 address')
    for name, low, high in [('poll_seconds', 60, 86400), ('port', 1024, 65535),
                            ('max_daily_calls', 1, 10000), ('max_daily_tokens', 1000, 10000000),
                            ('max_output_tokens', 200, 4000), ('max_attempts', 1, 5),
                            ('max_items_per_cycle', 1, 20), ('retention_days', 1, 30)]:
        if type(config.get(name)) is not int or not low <= config[name] <= high:
            raise PilotError('Invalid config: ' + name)
    if not isinstance(config.get('model'), str) or not config['model'] or len(config['model']) > 160:
        raise PilotError('Invalid model name')
    subs = config.get('subreddits', [])
    if not isinstance(subs, list) or len(subs) > 2 or any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9_]{2,21}', s) for s in subs):
        raise PilotError('Configure at most two valid subreddits')
    defaults = {'lookback_hours':24,'max_feed_pages':3,'browser_fallback':True,'browser_max_steps':4,
                'search_queries':[], 'search_interval_seconds':1800,'search_lookback_hours':72,
                'max_search_pages':2,'max_idle_poll_seconds':1800,'reply_quality_checks':True,'review_recommendations':True,'browser_headless':False}
    for key,value in defaults.items():
        config.setdefault(key,value)
    for name,low,high in [('lookback_hours',1,168),('max_feed_pages',1,10),('browser_max_steps',1,8),
                          ('search_interval_seconds',900,86400),('search_lookback_hours',1,168),
                          ('max_search_pages',1,3),('max_idle_poll_seconds',300,86400)]:
        if type(config[name]) is not int or not low <= config[name] <= high:
            raise PilotError('Invalid config: '+name)
    if not isinstance(config['search_queries'],list) or len(config['search_queries'])>8 or any(not isinstance(q,str) or not 3<=len(q)<=250 for q in config['search_queries']):
        raise PilotError('Provide at most eight bounded search queries')
    for key in ('reply_quality_checks','review_recommendations','browser_fallback','browser_headless'):
        if type(config[key]) is not bool:raise PilotError('Invalid boolean: '+key)
    for key in ('review_model','browser_model'):
        if key in config and (not isinstance(config[key],str) or not 1<=len(config[key])<=160):raise PilotError('Invalid model name: '+key)
    pub = config.setdefault('publishing',{'enabled':False})
    if type(pub.get('enabled')) is not bool:
        raise PilotError('publishing.enabled must be boolean')
    for name,default,low,high in [('max_per_day',3,1,20),('min_interval_seconds',7200,60,86400),('approval_hours',24,1,48),
                                 ('max_per_subreddit_day',2,1,20),('subreddit_interval_seconds',21600,60,86400),
                                 ('author_cooldown_hours',168,24,720)]:
        pub.setdefault(name,default)
        if type(pub[name]) is not int or not low<=pub[name]<=high:
            raise PilotError('Invalid publishing setting: '+name)
    if pub.get('reddit_username') and not re.fullmatch(r'[A-Za-z0-9_-]{3,20}',pub['reddit_username']):
        raise PilotError('Invalid expected Reddit username')
    from .ixbrowser import validate_profiles
    validate_profiles(config)
    return config


def load_catalog(root, config):
    catalog = json.loads((Path(root) / config['catalog_file']).read_text(encoding='utf-8-sig'))
    if not isinstance(catalog, list) or not catalog:
        raise PilotError('Provide a nonempty verified template catalog')
    seen = set()
    for item in catalog:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in seen:
            raise PilotError('Invalid/duplicate template ID')
        seen.add(item['id'])
        u = urlparse(item.get('url', ''))
        if u.scheme != 'https' or not u.hostname or u.username or u.password:
            raise PilotError('Template links must be HTTPS')
        if not isinstance(item.get('claims'), dict) or not item['claims'] or not all(isinstance(k, str) and isinstance(v, str) for k, v in item['claims'].items()):
            raise PilotError('Each template needs verified claim IDs and descriptions')
        if not isinstance(item.get('name'), str) or not isinstance(item.get('limitations'), list):
            raise PilotError('Template name and limitations are required')
        if config['source'] == 'browser' and (item.get('demo') or item.get('verified') is not True or u.hostname.endswith('.invalid')):
            raise PilotError('Live operation requires real, verified templates; demo catalog is blocked')
    return catalog


def require_live_permission(config, catalog):
    p = config.get('permissions', {})
    if config['source'] != 'browser' or config['ai'] != 'oneapi':
        raise PilotError('Live browser collection requires browser source and OneAPI mode')
    if not all(p.get(k) is True for k in ('browser_collection', 'ai_processing', 'community_rules_reviewed')) or not str(p.get('reference', '')).strip():
        raise PilotError('Live collection is disabled until written browser/AI permission and community review are recorded')
    if not config.get('subreddits') or any(t.get('verified') is not True or t.get('demo') for t in catalog):
        raise PilotError('Select approved communities and verified real templates first')


def reddit_url(value, allowed):
    u = urlparse(value)
    if u.scheme != 'https' or u.hostname not in ('www.reddit.com', 'reddit.com', 'old.reddit.com') or u.username or u.password or u.port not in (None, 443):
        return False
    m = re.match(r'^/r/([A-Za-z0-9_]+)/comments/[A-Za-z0-9]+(?:/|$)', u.path)
    return bool(m and m.group(1).lower() in {s.lower() for s in allowed})


def validate_post(post, config):
    if not isinstance(post, dict):
        raise PilotError('Invalid post')
    for key, limit in [('id', 200), ('url', 2048), ('subreddit', 50), ('title', 1000), ('body', 20000)]:
        if not isinstance(post.get(key), str) or len(post[key]) > limit or (key != 'body' and not post[key]):
            raise PilotError('Invalid post field: ' + key)
    if post.get('kind') not in ('post', 'comment'):
        raise PilotError('Invalid post kind')
    if config['source'] == 'browser' and not reddit_url(post['url'], config['subreddits']):
        raise PilotError('Post URL is outside the approved communities')
    # Labels used in demo/benchmark fixtures never reach the model.
    result = {k: post[k] for k in ('id', 'url', 'subreddit', 'title', 'body', 'kind')}
    for key,cap in [('author',30),('posted_at',50),('discovery_source',300)]:
        if isinstance(post.get(key),str): result[key]=post[key][:cap]
    return result


def validate_result(result, post, catalog):
    if not isinstance(result, dict) or set(result) != {'decision', 'reason', 'template_id', 'evidence', 'claim_ids', 'draft'}:
        raise PilotError('Model returned an invalid schema')
    if result['decision'] not in ('recommend', 'skip', 'review'):
        raise PilotError('Invalid decision')
    for key, cap in [('reason', 1000), ('template_id', 100), ('evidence', 1000), ('draft', 1600)]:
        if not isinstance(result[key], str) or len(result[key]) > cap:
            raise PilotError('Invalid result field: ' + key)
    if not result['reason'] or not isinstance(result['claim_ids'], list) or not all(isinstance(x, str) for x in result['claim_ids']):
        raise PilotError('Invalid reasoning/claims')
    if result['decision'] != 'recommend':
        if result['draft'] or result['template_id'] or result['claim_ids']:
            raise PilotError('Skipped/uncertain items must not contain a promotional draft')
        return result
    templates = {t['id']: t for t in catalog}
    template = templates.get(result['template_id'])
    if not template or not result['evidence'] or result['evidence'] not in post['title'] + '\n' + post['body'].split('[Existing replies, context only]')[0]:
        raise PilotError('Missing exact evidence or unknown template')
    if not result['claim_ids'] or any(c not in template['claims'] for c in result['claim_ids']):
        raise PilotError('Unsupported template claim')
    if not result['draft'].strip() or re.search(r'https?://|www\.|\]\(', result['draft'], re.I):
        raise PilotError('Draft must be plain text without model-generated links')
    result = dict(result)
    # Disclosure and approved URL are generated by code, never chosen by the model.
    result['draft'] = result['draft'].strip() + '\n\nI work on MeDo.\n' + template['url']
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
              created REAL NOT NULL, updated REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              next_try REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
              result TEXT, error TEXT, publication_url TEXT);
            CREATE TABLE IF NOT EXISTS calls (
              id INTEGER PRIMARY KEY, day TEXT NOT NULL, item_id TEXT, model TEXT NOT NULL,
              reserved INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
              status TEXT NOT NULL, latency_ms INTEGER, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            if 'priority' not in {r['name'] for r in c.execute('PRAGMA table_info(items)')}:
                c.execute('ALTER TABLE items ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def db(self):
        c = sqlite3.connect(str(self.path), timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def setting(self, key, default=''):
        with self.db() as c:
            row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return row['value'] if row else default

    def set_setting(self, key, value):
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(value)))

    def ingest(self, post):
        from .discovery import rank
        now = time.time()
        with self.db() as c:
            cursor = c.execute('INSERT OR IGNORE INTO items(id,payload,created,updated,priority) VALUES (?,?,?,?,?)',
                               (post['id'], json.dumps(post, ensure_ascii=False), now, now, rank(post,now)[0]))
            return cursor.rowcount == 1

    def claim(self, max_attempts):
        now = time.time()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            # Recover leases, retaining attempt counts across crashes.
            c.execute("UPDATE items SET status=CASE WHEN attempts>=? THEN 'error' ELSE 'queued' END, error='Interrupted run; lease expired' WHERE status='processing' AND lease_until<?", (max_attempts, now))
            # Aging lets unusual requests rise instead of starving behind keywords.
            row = c.execute("SELECT * FROM items WHERE status='queued' AND next_try<=? AND attempts<? ORDER BY priority+MIN(100,(?-created)/3600.0*5) DESC,created LIMIT 1", (now, max_attempts,now)).fetchone()
            if not row:
                return None
            c.execute("UPDATE items SET status='processing', attempts=attempts+1, lease_until=?, updated=? WHERE id=?", (now + 180, now, row['id']))
            return json.loads(row['payload'])

    def complete(self, item_id, result):
        status = 'skipped' if result['decision'] == 'skip' else 'review'
        with self.db() as c:
            c.execute('UPDATE items SET status=?,result=?,error=NULL,updated=?,lease_until=0 WHERE id=?',
                      (status, json.dumps(result, ensure_ascii=False), time.time(), item_id))

    def fail(self, item_id, message, max_attempts):
        with self.db() as c:
            row = c.execute('SELECT attempts FROM items WHERE id=?', (item_id,)).fetchone()
            attempt = row['attempts']
            c.execute('UPDATE items SET status=?,error=?,next_try=?,updated=?,lease_until=0 WHERE id=?',
                      ('queued' if attempt < max_attempts else 'error', message[:200], time.time() + min(3600, 60 * 2 ** attempt), time.time(), item_id))

    def defer_budget(self, item_id):
        with self.db() as c:
            c.execute("UPDATE items SET status='queued',attempts=MAX(0,attempts-1),lease_until=0,next_try=?,error='Daily budget reached' WHERE id=?", (time.time() + 3600, item_id))

    def reserve(self, item_id, model, tokens, config):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT COUNT(*) AS n,COALESCE(SUM(reserved),0) AS tokens FROM calls WHERE day=?', (utc_day(),)).fetchone()
            if row['n'] >= config['max_daily_calls'] or row['tokens'] + tokens > config['max_daily_tokens']:
                return None
            return c.execute("INSERT INTO calls(day,item_id,model,reserved,status,created) VALUES (?,?,?,?,'pending',?)", (utc_day(), item_id, model, tokens, time.time())).lastrowid

    def finish_call(self, call_id, usage, latency_ms, ok):
        input_n = usage.get('prompt_tokens')
        output_n = usage.get('completion_tokens')
        known = type(input_n) is int and type(output_n) is int and min(input_n, output_n) >= 0
        with self.db() as c:
            if known:
                c.execute('UPDATE calls SET reserved=?,input_tokens=?,output_tokens=?,status=?,latency_ms=? WHERE id=?',
                          (input_n + output_n, input_n, output_n, 'ok' if ok else 'failed', latency_ms, call_id))
            else:
                # Failed/time-out calls may have consumed tokens: retain the reservation.
                c.execute('UPDATE calls SET status=?,latency_ms=? WHERE id=?', ('usage_unknown' if ok else 'failed', latency_ms, call_id))

    def review(self, item_id, action, publication_url='', allowed=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT status,result,payload FROM items WHERE id=?', (item_id,)).fetchone()
            if not row:
                raise PilotError('Unknown item')
            if action == 'approve' and row['status'] == 'review' and json.loads(row['result']).get('decision') == 'recommend':
                status = 'approved'
            elif action == 'reject' and row['status'] in ('review', 'approved'):
                status = 'rejected'
            elif action == 'published' and row['status'] == 'approved' and reddit_url(publication_url, allowed or []):
                target=urlparse(publication_url).path.strip('/').split('/')
                source=urlparse(json.loads(row['payload'])['url']).path.strip('/').split('/')
                if len(target)!=6 or len(source)<4 or target[3]!=source[3] or not target[5].isalnum():
                    raise PilotError('Provide a comment permalink from the original thread')
                status = 'published'
            else:
                raise PilotError('Invalid review transition or publication URL')
            c.execute('UPDATE items SET status=?,publication_url=?,updated=? WHERE id=?', (status, publication_url or None, time.time(), item_id))

    def items(self):
        with self.db() as c:
            rows = c.execute('SELECT * FROM items ORDER BY created DESC LIMIT 200').fetchall()
            return [dict(r, payload=json.loads(r['payload']), result=json.loads(r['result']) if r['result'] else None) for r in rows]

    def summary(self):
        with self.db() as c:
            counts = dict(c.execute('SELECT status,COUNT(*) FROM items GROUP BY status').fetchall())
            usage = dict(c.execute('SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(reserved),0) AS budget_tokens,COALESCE(SUM(input_tokens IS NULL),0) AS unknown_usage_calls FROM calls WHERE day=?', (utc_day(),)).fetchone())
        return {'counts': counts, 'usage': usage, 'day_utc': utc_day(), 'paused': self.setting('paused') == '1', 'last_cycle': self.setting('last_cycle'), 'message': self.setting('message')}

    def expire(self, days):
        cutoff = time.time() - days * 86400
        with self.db() as c:
            rows = c.execute("SELECT id,payload FROM items WHERE created<? AND status!='processing'", (cutoff,)).fetchall()
            for r in rows:
                payload = json.loads(r['payload'])
                if payload.get('body') == '[Expired]':
                    continue
                payload['body'] = '[Expired]'
                payload['title'] = '[Expired]'
                payload['url'] = ''
                payload.pop('author',None)
                c.execute("UPDATE items SET payload=?,result=NULL,publication_url=NULL,status='expired',error=NULL WHERE id=?", (json.dumps(payload), r['id']))
            c.execute('DELETE FROM calls WHERE created<?', (time.time() - 90 * 86400,))
