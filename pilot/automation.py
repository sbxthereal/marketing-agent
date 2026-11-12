"""Automatic publication of checked content and finite, restart-safe content plans."""
import hashlib
import json
import math
import re
import time

from .core import PilotError, require_live_permission
from .campaigns import create_campaign, is_campaign, policy, policy_hash, require_post_policy


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def automatic(config):
    return config.get('publishing', {}).get('mode', 'manual') == 'automatic'


def validate_config(config):
    pub = config['publishing']
    pub.setdefault('mode', 'manual')
    if pub['mode'] not in ('manual', 'automatic'):
        raise PilotError('publishing.mode must be manual or automatic')
    if automatic(config) and (config['ai'] != 'oneapi' or not config['reply_quality_checks'] or not config['review_recommendations']):
        raise PilotError('Automatic mode requires OneAPI and both reply quality checks')
    settings = config.setdefault('automation', {})
    if not isinstance(settings, dict):
        raise PilotError('Invalid automation settings')
    settings.setdefault('campaign_interval_hours', 168)
    interval = settings['campaign_interval_hours']
    if type(interval) is not int or not 24 <= interval <= 720:
        raise PilotError('Campaign interval must be 24–720 hours across all profiles')
    settings.setdefault('campaign_plan', [])
    plans = settings['campaign_plan']
    if not isinstance(plans, list) or len(plans) > 30:
        raise PilotError('Provide at most 30 distinct content-plan entries')
    ids = set()
    for entry in plans:
        if not isinstance(entry, dict) or not re.fullmatch(r'[a-z0-9_-]{1,80}', str(entry.get('id', ''))):
            raise PilotError('Each content-plan entry needs a stable lowercase ID')
        if entry['id'] in ids:
            raise PilotError('Content-plan IDs must be unique')
        ids.add(entry['id'])
        create_campaign(entry, config)
        if not isinstance(entry.get('template_id'), str) or not 1 <= len(entry['template_id']) <= 100:
            raise PilotError('Each content-plan entry needs a catalog template ID')
        at = entry.get('not_before', 0)
        if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
            raise PilotError('Content-plan not_before must be a Unix timestamp')


def require_automatic_policy(post, config):
    rules = policy(config, post['subreddit'])
    field = 'automated_posts' if is_campaign(post) else 'automated_replies'
    if rules.get(field) is not True or not rules.get('reference', '').strip() or not rules.get('notes', '').strip():
        raise PilotError('Automatic '+('posts' if is_campaign(post) else 'replies')+' are not configured for this community')
    reviewed = rules.get('reviewed_at', 0)
    if type(reviewed) not in (int, float) or not 0 <= time.time()-reviewed <= 30*86400:
        raise PilotError('Community rules need a fresh review before automatic publication')
    if is_campaign(post):
        require_post_policy(post, config)
    return rules


def record_quality(store, post, result, config):
    """Only called after the second OneAPI pass and local validation succeed."""
    if result['decision'] != 'recommend':
        return result
    # The reply validator adds the catalog URL. Omit only that generated trailing
    # URL when the automatic destination does not permit links; keep disclosure.
    if automatic(config) and not is_campaign(post) and policy(config, post['subreddit']).get('links_allowed') is not True:
        body, sep, link = result['draft'].rpartition('\n')
        if sep and link.startswith('https://'):
            result = dict(result, draft=body)
    with store.db() as c:
        c.execute('INSERT OR REPLACE INTO quality_receipts VALUES (?,?,?,?,?)',
                  (post['id'], fingerprint(post), fingerprint(result), policy_hash(config, post['subreddit']), time.time()))
    return result


def require_checked(c, item_id, post, result, config):
    require_live_permission(config, [])
    if not automatic(config) or config['ai'] != 'oneapi' or not config.get('review_recommendations') or not config.get('reply_quality_checks'):
        raise PilotError('Automatic publication requires the OneAPI quality pipeline')
    rules = require_automatic_policy(post, config)
    receipt = c.execute('SELECT * FROM quality_receipts WHERE item_id=?', (item_id,)).fetchone()
    if (not receipt or receipt['source_hash'] != fingerprint(post) or receipt['result_hash'] != fingerprint(result)
            or receipt['policy_hash'] != policy_hash(config, post['subreddit'])):
        raise PilotError('No matching final OneAPI quality check; this draft will not be sent automatically')
    if not 0 <= time.time()-receipt['checked_at'] <= config['publishing']['approval_hours']*3600:
        raise PilotError('Final quality check expired; no automatic resend or redraft')
    if result.get('decision') != 'recommend' or 'I work on MeDo.' not in result.get('draft', ''):
        raise PilotError('Automatic publication needs a qualified, disclosed recommendation')
    if rules.get('links_allowed') is not True and re.search(r'https?://|www\.', result['draft'], re.I):
        raise PilotError('This community does not permit promotional links')


class Automation:
    def __init__(self, store, flow, config, catalog):
        self.store, self.flow, self.config, self.catalog = store, flow, config, catalog
        with store.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS campaign_plan_runs (plan_id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL, item_id TEXT NOT NULL, created REAL NOT NULL)')

    def active(self):
        return (automatic(self.config) and self.config['publishing'].get('enabled') is True
                and self.store.setting('paused') != '1')

    def queue_ready(self):
        if not self.active():
            return
        from .workflow import draft_hash, require_publishing
        from .campaigns import publication_text
        require_live_permission(self.config, self.catalog)
        require_publishing(self.config)
        with self.store.db() as c:
            # Legacy drafts have no receipt and are never swept into publication.
            rows = c.execute("SELECT i.id,i.result FROM items i JOIN quality_receipts q ON q.item_id=i.id LEFT JOIN outbox o ON o.item_id=i.id WHERE i.status='review' AND o.item_id IS NULL ORDER BY q.checked_at LIMIT 20").fetchall()
        for row in rows:
            result = json.loads(row['result']) if row['result'] else {}
            if result.get('decision') != 'recommend':
                continue
            try:
                self.flow.request_publish(row['id'], draft_hash(publication_text(result)), self.config, automatic=True)
            except PilotError as error:
                with self.store.db() as c:
                    c.execute("UPDATE items SET error=? WHERE id=? AND status='review'", (str(error)[:300], row['id']))

    def schedule(self):
        if not self.active():
            return
        from .workflow import require_publishing
        require_live_permission(self.config, self.catalog)
        require_publishing(self.config)
        settings = self.config.get('automation', {})
        now = time.time()
        for entry in settings.get('campaign_plan', []):
            if entry.get('not_before', 0) > now:
                continue
            template = next((t for t in self.catalog if t['id'] == entry['template_id'] and t.get('verified') is True), None)
            if not template:
                continue
            post = create_campaign(entry, self.config)
            try:
                require_automatic_policy(post, self.config)
            except PilotError:
                continue
            post.update(id='campaign_plan_'+fingerprint(entry['id'])[:24], planned_template_id=entry['template_id'],
                        discovery_source='Scheduled MeDo content plan: '+entry['id'])
            with self.store.db() as c:
                c.execute('BEGIN IMMEDIATE')
                if c.execute('SELECT 1 FROM campaign_plan_runs WHERE plan_id=?', (entry['id'],)).fetchone():
                    continue
                if c.execute('SELECT 1 FROM campaign_plan_runs WHERE created>?', (now-settings.get('campaign_interval_hours', 168)*3600,)).fetchone():
                    return
                # A plan stays pending while another post is drafting/queued.
                if c.execute("SELECT 1 FROM items WHERE json_extract(payload,'$.kind')='campaign' AND status IN ('queued','processing','approved')").fetchone():
                    return
                c.execute('INSERT INTO items(id,payload,created,updated,priority) VALUES (?,?,?,?,?)', (post['id'], json.dumps(post), now, now, 30))
                c.execute('INSERT INTO campaign_plan_runs VALUES (?,?,?,?)', (entry['id'], fingerprint(entry), post['id'], now))
            return  # One new campaign per scheduler pass, never a catch-up burst.

    def status(self):
        mode = 'automatic' if automatic(self.config) else 'manual'
        blockers = []
        if mode == 'automatic':
            from .workflow import require_publishing
            for check in (lambda: require_live_permission(self.config, self.catalog), lambda: require_publishing(self.config)):
                try: check()
                except PilotError as error: blockers.append(str(error))
            if self.store.setting('paused') == '1':
                blockers.append('Worker is paused')
        with self.store.db() as c:
            completed = [r['plan_id'] for r in c.execute('SELECT plan_id FROM campaign_plan_runs ORDER BY created')]
            ready = c.execute("SELECT COUNT(*) FROM outbox WHERE approval_source='automatic' AND state IN ('ready','checking','submitting')").fetchone()[0]
        destinations = []
        for sub in self.config.get('subreddits', []):
            entry = {'community': sub}
            for kind in ('reply', 'campaign'):
                post = {'kind': kind, 'subreddit': sub, 'policy_hash': policy_hash(self.config, sub)}
                try:
                    require_automatic_policy(post, self.config)
                    entry[kind] = 'eligible'
                except PilotError as error:
                    entry[kind] = str(error)
            destinations.append(entry)
        return {'mode': mode, 'blockers': blockers, 'automatic_pending': ready, 'destinations': destinations,
                'planned_campaigns': len(self.config.get('automation', {}).get('campaign_plan', [])), 'started_plans': completed}
