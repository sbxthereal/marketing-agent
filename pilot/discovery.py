"""Cheap intent ranking and bounded, rotating public-UI searches."""
import datetime as dt
import re
import time
from urllib.parse import urlencode

DEFAULT_QUERIES = [
    '(build OR create) AND (app OR website) AND (help OR beginner OR recommend)',
    '"client portal" OR "booking app" OR "internal tool"',
    '"portfolio" OR "waitlist" OR "landing page"',
]


def timestamp(value):
    try:
        if isinstance(value, (int, float)):
            return float(value)
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def rank(post, now=None):
    """Prioritize, never discard keyword misses. Full thread decides actual fit."""
    now = time.time() if now is None else now
    text = (post['title']+' '+post.get('body', '')).lower()
    signals, score = [], 0
    if re.search(r'\b(need|looking for|recommend|which|how (can|do)|help|easiest|alternative)\b|\?', text):
        signals.append('asks for help'); score += 30
    if re.search(r'\b(app|website|portfolio|waitlist|landing page|client portal|internal tool|booking|dashboard|database|prototype|mvp)\b', text):
        signals.append('app or website need'); score += 25
    if re.search(r'\b(beginner|non.?technical|no.?code|without coding|spreadsheet|manual|struggling)\b', text):
        signals.append('builder fits the context'); score += 10
    if re.search(r'\b(i launched|we launched|i built|we built|check out my|my new tool|here.s how|tutorial)\b', post['title'].lower()):
        signals.append('possible showcase'); score -= 25
    created = timestamp(post.get('posted_at'))
    if created and now-created < 86400:
        signals.append('under 24 hours old'); score += 10
    return max(0, min(100, score)), signals


def search_target(config, cursor):
    queries = config.get('search_queries', [])
    targets = [(sub, query) for query in queries for sub in config['subreddits']]
    if not targets:
        return None
    sub, query = targets[cursor % len(targets)]
    url = 'https://www.reddit.com/r/'+sub+'/search/?'+urlencode({
        'q':query, 'restrict_sr':'on', 'sort':'new', 't':'week', 'type':'posts'})
    return sub, query, url


def scan_delay(config, idle_cycles):
    return min(max(config['poll_seconds'],config.get('max_idle_poll_seconds', 1800)), config['poll_seconds'] * 2**min(idle_cycles, 4))
