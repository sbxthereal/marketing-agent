"""Bounded thread context with author roles separate from untrusted text."""
import hashlib
import json

MARKER = '[Existing replies, context only]'


def original_text(post):
    return post.get('original_body', post['body'].partition(MARKER)[0]).strip()


def discussion(post):
    if 'discussion' in post:
        return post['discussion']
    legacy = post['body'].partition(MARKER)[2].strip()
    return [{'role': 'community', 'body': legacy[:5000]}] if legacy else []


def op_followups_hash(data):
    author = data.get('author', '').lower()
    replies = []
    if author and author not in ('[deleted]', '[removed]'):
        replies = [' '.join(c['body'].split()) for c in data['comments']
                   if c.get('author', '').lower() == author]
    return hashlib.sha256(json.dumps(sorted(replies), ensure_ascii=False).encode()).hexdigest()


def thread_context(data):
    author = data.get('author', '').lower()
    def is_op(comment):
        return bool(author and author not in ('[deleted]', '[removed]') and
                    comment.get('author', '').lower() == author)
    # Keep OP clarifications even if they fall below the first twelve comments.
    comments = sorted(data['comments'], key=lambda c: not is_op(c))[:14]
    replies, remaining = [], 5000
    for comment in comments:
        body = comment['body'][:min(1400, remaining)]
        if not remaining:
            break
        if not body:
            continue
        replies.append({'role': 'op' if is_op(comment) else 'community', 'body': body})
        remaining -= len(body)
    original = data['body'][:10000]
    display = '\n\n'.join(('OP follow-up: ' if c['role'] == 'op' else 'Community reply: ') + c['body'] for c in replies)
    return {'original_body': original, 'discussion': replies,
            'op_followups_hash': op_followups_hash(data),
            'body': original + ('\n\n' + MARKER + '\n' + display if display else '')}
