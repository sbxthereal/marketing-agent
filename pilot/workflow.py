"""Persistent discovery metrics and an at-most-once publication outbox."""
import hashlib
import json
import time
from urllib.parse import urlparse

from .core import PilotError, publisher_for, reddit_url
from .quality import similar_reply


def draft_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def comment_permalink(url, post, allowed):
    if not reddit_url(url, allowed):
        return False
    parts = urlparse(url).path.strip('/').split('/')
    source = urlparse(post['url']).path.strip('/').split('/')
    return len(parts) == 6 and parts[3] == source[3] and parts[5].isalnum()


def require_publishing(config):
    p = config.get('publishing', {})
    if config['source'] != 'browser' or p.get('enabled') is not True:
        raise PilotError('Browser publishing is not enabled')
    identity = p.get('reddit_username')
    if config.get('browser_backend') in ('ixbrowser', 'aliasmode'):
        profiles = config.get(config['browser_backend'],{}).get('profiles',[])
        identity = profiles and all(profile.get('reddit_username') for profile in profiles)
    if not identity or p.get('permission_confirmed') is not True or not p.get('permission_reference'):
        raise PilotError('Record the publishing permission and expected Reddit account first')


class Workflow:
    def __init__(self, store):
        self.store = store
        with store.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY, started REAL, seconds REAL,
                subreddit TEXT, pages INTEGER, found INTEGER, new_items INTEGER,
                duplicates INTEGER, state TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS outbox (
                item_id TEXT PRIMARY KEY, draft_hash TEXT NOT NULL, draft TEXT NOT NULL,
                state TEXT NOT NULL, requested REAL NOT NULL, updated REAL NOT NULL,
                permalink TEXT, error TEXT);
            ''')
            columns={r['name'] for r in c.execute('PRAGMA table_info(scans)')}
            if 'source' not in columns:c.execute("ALTER TABLE scans ADD COLUMN source TEXT NOT NULL DEFAULT 'new feed'")
            columns={r['name'] for r in c.execute('PRAGMA table_info(outbox)')}
            for name,definition in [('subreddit',"TEXT NOT NULL DEFAULT ''"),('author_hash',"TEXT NOT NULL DEFAULT ''"),('submitted','REAL'),('reddit_username',"TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:c.execute('ALTER TABLE outbox ADD COLUMN '+name+' '+definition)
            for row in c.execute("SELECT o.item_id,i.payload FROM outbox o JOIN items i ON i.id=o.item_id WHERE o.subreddit=''").fetchall():
                post=json.loads(row['payload'])
                c.execute('UPDATE outbox SET subreddit=?,author_hash=? WHERE item_id=?',(post['subreddit'].lower(),self.author_key(post),row['item_id']))

    @staticmethod
    def author_key(post):
        author=post.get('author','').lower()
        return draft_hash(author) if author and author not in ('[deleted]','[removed]') else ''

    def seen(self, item_id):
        with self.store.db() as c:
            return c.execute('SELECT 1 FROM items WHERE id=?', (item_id,)).fetchone() is not None

    def scan_state(self, subreddit):
        with self.store.db() as c:
            row=c.execute("SELECT state FROM scans WHERE subreddit=? AND source='new feed' ORDER BY id DESC LIMIT 1",(subreddit,)).fetchone()
            return row['state'] if row else ''

    def expire(self, days):
        with self.store.db() as c:
            # Erase content on time without erasing the send ledger/cooldowns.
            c.execute("UPDATE outbox SET draft='',permalink=NULL,error=NULL,state=CASE WHEN state IN ('published','uncertain') THEN state ELSE 'expired' END WHERE requested<? AND state NOT IN ('checking','submitting')",(time.time()-days*86400,))
            c.execute("UPDATE outbox SET author_hash='' WHERE COALESCE(submitted,updated)<?",(time.time()-30*86400,))

    def record_scan(self, subreddit, started, pages, found, new, duplicates, state, error='',source='new feed'):
        with self.store.db() as c:
            c.execute('INSERT INTO scans(started,seconds,subreddit,pages,found,new_items,duplicates,state,error,source) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (started, time.time()-started, subreddit, pages, found, new, duplicates, state, error[:200],source))
            c.execute('DELETE FROM scans WHERE started<?', (time.time()-30*86400,))

    def metrics(self):
        with self.store.db() as c:
            scans = [dict(r) for r in c.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 12')]
            totals = dict(c.execute('SELECT COUNT(*) scans,COALESCE(SUM(pages),0) pages,COALESCE(SUM(new_items),0) new_items,COALESCE(SUM(duplicates),0) duplicates FROM scans WHERE started>?', (time.time()-86400,)).fetchone())
            totals['outbox'] = dict(c.execute('SELECT state,COUNT(*) FROM outbox GROUP BY state'))
            return dict(totals, recent=scans)

    def duplicate_draft(self, item_id, draft):
        with self.store.db() as c:
            rows=c.execute("SELECT result FROM items WHERE id!=? AND updated>? AND status IN ('review','approved','published') AND result IS NOT NULL",(item_id,time.time()-7*86400))
            return any(similar_reply(draft,json.loads(r['result']).get('draft','')) for r in rows)

    def _attempts(self,c,now):
        attempts=[dict(r) for r in c.execute("SELECT item_id,subreddit,author_hash,draft,COALESCE(submitted,updated) at FROM outbox WHERE state IN ('published','uncertain','checking','submitting') AND COALESCE(submitted,updated)>?",(now-30*86400,))]
        # A manually recorded publication also consumes the posting allowance.
        for r in c.execute("SELECT id,payload,result,updated FROM items WHERE status='published' AND updated>? AND id NOT IN (SELECT item_id FROM outbox)",(now-30*86400,)):
            p=json.loads(r['payload']);result=json.loads(r['result'] or '{}')
            attempts.append(dict(item_id=r['id'],subreddit=p['subreddit'].lower(),author_hash=self.author_key(p),draft=result.get('draft',''),at=r['updated']))
        return attempts

    def _wait_reason(self,c,post,config,now,item_id=''):
        pub=config['publishing'];attempts=[r for r in self._attempts(c,now) if r['item_id']!=item_id]
        if c.execute("SELECT 1 FROM outbox WHERE state IN ('checking','submitting') AND item_id!=?",(item_id,)).fetchone():
            return 'Another publication is being checked'
        recent=[r for r in attempts if now-r['at']<86400]
        if len(recent)>=pub.get('max_per_day',3):return 'Daily reply limit reached; waiting for the rolling 24-hour window'
        if recent and now-max(r['at'] for r in recent)<pub.get('min_interval_seconds',7200):
            return 'Global reply cooldown is active'
        local=[r for r in recent if r['subreddit']==post['subreddit'].lower()]
        if len(local)>=pub.get('max_per_subreddit_day',2):return 'Community daily reply limit reached'
        if local and now-max(r['at'] for r in local)<pub.get('subreddit_interval_seconds',21600):return 'Community reply cooldown is active'
        author=self.author_key(post)
        if author and any(r['author_hash']==author and now-r['at']<pub.get('author_cooldown_hours',168)*3600 for r in attempts):
            return 'This author was already contacted within the cooldown period'
        return ''

    def pacing(self,config):
        now=time.time();pub=config.get('publishing',{})
        with self.store.db() as c:attempts=self._attempts(c,now)
        recent=sorted((r['at'] for r in attempts if now-r['at']<86400),reverse=True)
        next_at=recent[0]+pub.get('min_interval_seconds',7200) if recent else 0
        if len(recent)>=pub.get('max_per_day',3):next_at=max(next_at,recent[pub.get('max_per_day',3)-1]+86400)
        return {'last_24h':len(recent),'max_per_day':pub.get('max_per_day',3),'interval_seconds':pub.get('min_interval_seconds',7200),
                'max_per_subreddit_day':pub.get('max_per_subreddit_day',2),'subreddit_interval_seconds':pub.get('subreddit_interval_seconds',21600),
                'next_global_at':next_at if next_at>now else None}

    def request_publish(self, item_id, expected_hash, config):
        require_publishing(config)
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if not row or row['status'] != 'approved' or not row['result']:
                raise PilotError('Only approved drafts can be queued for publication')
            draft = json.loads(row['result'])['draft']
            if not expected_hash or draft_hash(draft) != expected_hash:
                raise PilotError('The draft changed; refresh and review it again')
            if time.time()-row['updated'] > config.get('publishing', {}).get('approval_hours', 24)*3600:
                raise PilotError('Approval expired; review a fresh draft')
            if c.execute('SELECT 1 FROM outbox WHERE item_id=?', (item_id,)).fetchone():
                raise PilotError('A publication attempt already exists; reconcile it before any resend')
            for previous in c.execute("SELECT draft FROM outbox WHERE requested>? AND state NOT IN ('cancelled','expired','blocked')",(time.time()-7*86400,)):
                if similar_reply(draft,previous['draft']):raise PilotError('Reply is too similar to another queued or recent reply; write a more specific response')
            for previous in self._attempts(c,time.time()):
                if time.time()-previous['at']<7*86400 and similar_reply(draft,previous['draft']):
                    raise PilotError('Reply is too similar to a recent publication')
            now = time.time()
            post=json.loads(row['payload'])
            publisher=publisher_for(config,post['subreddit'])
            if not publisher:
                raise PilotError('Select the expected Reddit account for this community before queuing')
            c.execute('INSERT INTO outbox(item_id,draft_hash,draft,state,requested,updated,subreddit,author_hash,reddit_username) VALUES(?,?,?,?,?,?,?,?,?)', (item_id, expected_hash, draft, 'ready', now, now,post['subreddit'].lower(),self.author_key(post),publisher.lower()))

    def recover(self):
        # Never automatically repeat a send after a crash. Checking is read-only,
        # but choosing uncertainty here makes the recovery policy conservative.
        with self.store.db() as c:
            c.execute("UPDATE outbox SET state='uncertain',error='Worker restarted during publication; inspect Reddit before retrying',updated=? WHERE state IN ('checking','submitting')", (time.time(),))
            if c.execute("SELECT 1 FROM outbox WHERE state='uncertain' LIMIT 1").fetchone():
                c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','1')")
                c.execute("INSERT OR REPLACE INTO settings VALUES ('message','An uncertain publication needs reconciliation before resuming')")

    def retry_preflight(self,item_id,expected_hash,config):
        require_publishing(config)
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT o.*,i.status AS item_status,i.result,i.payload FROM outbox o JOIN items i ON i.id=o.item_id WHERE item_id=?',(item_id,)).fetchone()
            if not row or row['state']!='blocked' or row['item_status']!='approved' or row['draft_hash']!=expected_hash or not row['result'] or draft_hash(json.loads(row['result'])['draft'])!=expected_hash:
                raise PilotError('Only a failed preflight with an unchanged approved draft can be retried')
            if time.time()-row['requested']>config['publishing'].get('approval_hours',24)*3600:
                raise PilotError('Approval expired; review a fresh draft')
            if not row['reddit_username'] or row['reddit_username'] != publisher_for(config,json.loads(row['payload'])['subreddit']).lower():
                raise PilotError('Publishing account changed; restore the account recorded for this attempt')
            c.execute("UPDATE outbox SET state='ready',error=NULL,updated=? WHERE item_id=?",(time.time(),item_id))

    def claim_publication(self, config):
        require_publishing(config)
        now = time.time()
        pub = config['publishing']
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows = c.execute("SELECT o.*,i.payload,i.status AS item_status,i.result FROM outbox o JOIN items i ON i.id=o.item_id WHERE o.state='ready' ORDER BY requested").fetchall()
            for row in rows:
                if row['item_status'] != 'approved' or now-row['requested'] > pub.get('approval_hours', 24)*3600 or not row['result'] or draft_hash(json.loads(row['result'])['draft']) != row['draft_hash']:
                    c.execute("UPDATE outbox SET state='cancelled',error='Approval expired or draft changed',updated=? WHERE item_id=?", (now,row['item_id']))
                    continue
                post=json.loads(row['payload'])
                if not row['reddit_username'] or row['reddit_username'] != publisher_for(config,post['subreddit']).lower():
                    c.execute("UPDATE outbox SET state='blocked',error='Publishing account changed; this attempt cannot be rerouted',updated=? WHERE item_id=?",(now,row['item_id']))
                    continue
                reason=self._wait_reason(c,post,config,now,row['item_id'])
                if reason:
                    c.execute('UPDATE outbox SET error=? WHERE item_id=?',(reason,row['item_id']))
                    continue
                c.execute("UPDATE outbox SET state='checking',error=NULL,updated=? WHERE item_id=?", (now,row['item_id']))
                return dict(row, post=post)
            return None

    def mark_submitting(self, item_id, config=None):
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT o.state,i.status,i.result,i.payload,o.draft_hash FROM outbox o JOIN items i ON i.id=o.item_id WHERE item_id=?', (item_id,)).fetchone()
            if self.store.setting('paused') == '1' or not row or row['state'] != 'checking' or row['status'] != 'approved' or not row['result'] or draft_hash(json.loads(row['result'])['draft']) != row['draft_hash']:
                raise PilotError('Publication cancelled before submission')
            if config:
                reason=self._wait_reason(c,json.loads(row['payload']),config,time.time(),item_id)
                if reason:raise PilotError(reason)
            c.execute("UPDATE outbox SET state='submitting',submitted=?,updated=? WHERE item_id=?", (time.time(),time.time(),item_id))

    def publication_done(self, job, permalink, config):
        if not comment_permalink(permalink, job['post'], config['subreddits']):
            raise PilotError('Published comment permalink did not match the source thread')
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE outbox SET state='published',permalink=?,updated=?,error=NULL WHERE item_id=?", (permalink,time.time(),job['item_id']))
            c.execute("UPDATE items SET status='published',publication_url=?,updated=? WHERE id=?", (permalink,time.time(),job['item_id']))

    def publication_failed(self, item_id, message):
        with self.store.db() as c:
            c.execute("UPDATE outbox SET state=CASE WHEN state='submitting' THEN 'uncertain' ELSE 'blocked' END,error=?,updated=? WHERE item_id=?", (message[:200],time.time(),item_id))

    def outbox(self):
        with self.store.db() as c:
            return [dict(r) for r in c.execute('SELECT item_id,draft_hash,state,permalink,error,reddit_username FROM outbox ORDER BY requested DESC LIMIT 200')]

    def enrich(self, post):
        with self.store.db() as c:
            c.execute('UPDATE items SET payload=? WHERE id=? AND status=?', (json.dumps(post),post['id'],'processing'))


def shortlist(post, catalog, limit=4):
    """Rank, never reject: keyword absence must not hide relevant demand."""
    text = (post['title']+' '+post['body']).lower()
    ranked = sorted(catalog, key=lambda t: sum(k.lower() in text for k in t.get('keywords', [])), reverse=True)
    return ranked[:limit]
