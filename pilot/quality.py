"""Local reply checks; no extra model call for boilerplate or duplicates."""
import re
from difflib import SequenceMatcher
from .context import original_text, discussion

STOPWORDS = set('the and that this with from have your you for are can could would should want need which what how does do into about some just like more work medo'.split())


def reply_body(text):
    text = re.sub(r'https?://\S+', '', text)
    text = text.replace('Automated MeDo team response.', '')
    return re.sub(r'I work on MeDo\.', '', text, flags=re.I).strip()


def similar_reply(first, second):
    a, b = [re.findall(r'[a-z0-9]+', reply_body(t).lower()) for t in (first, second)]
    if not a or not b:
        return False
    shingles = lambda w: set(zip(w, w[1:], w[2:]))
    x, y = shingles(a), shingles(b)
    jaccard = len(x & y)/max(1, len(x | y))
    return jaccard >= .55 or SequenceMatcher(None, a, b, autojunk=False).ratio() >= .78


def concerns(draft, post):
    body = reply_body(draft)
    words = body.split()
    issues = []
    followup=bool(post.get('conversation_parent'))
    cap = 120 if followup or post.get('platform','reddit') != 'reddit' else 90
    floor = 8 if followup else 25
    if not floor <= len(words) <= cap:
        issues.append('Reply must be '+str(floor)+'–'+str(cap)+' words')
    if not followup and not re.search(r'\bMeDo\b', ' '.join(words[:25])):
        issues.append('Recommend MeDo directly in the opening sentence')
    if re.search(r'\b(great question|game.?changer|seamless|leverage|revolutionary|powerful platform|low.friction|hope this helps|check it out|sign up now|mobile product)\b', body, re.I):
        issues.append('Remove generic or sales language')
    if re.search(r'\b(guaranteed|unlimited|cheapest|100% secure|production.ready|i.ve used|i used|my clients|we use it)\b', body, re.I):
        issues.append('Unsupported guarantee or first-hand claim')
    source = post['title']+' '+original_text(post) + ' ' + ' '.join(c['body'] for c in discussion(post) if c['role'] == 'op')
    for subject in ('app store','contact form'):
        if subject in body.lower() and subject not in source.lower():
            issues.append('Remove the unrequested '+subject+' aside')
    specific = lambda t: {w for w in re.findall(r'[a-z]{4,}', t.lower()) if w not in STOPWORDS}
    if len(specific(source) & specific(body)) < (1 if followup else 2):
        issues.append('Reply lacks enough details from this person’s request')
    return issues


def hold(result, reason):
    return dict(result, decision='review', reason=reason[:1000], template_id='', claim_ids=[], draft='')


def context_skip(post, username=''):
    if username and post.get('author', '').lower() == username.lower():
        return 'Own account’s thread; no promotional reply'
    from .conversations import stop_reason
    if post.get('conversation_parent'):
        return stop_reason(original_text(post))
    replies = ' '.join(c['body'] for c in discussion(post))
    if re.search(r'\bmedo\b', replies, re.I):
        return 'MeDo is already mentioned in the visible discussion; avoid repeating the recommendation'
    return ''
