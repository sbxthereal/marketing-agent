"""Reviewed text-post submission; uncertain outcomes are never automatically retried."""
import re
import time
from urllib.parse import urlparse

from . import dom
from .core import PilotError
from .campaigns import require_post_policy, thread_permalink


def verify_thread(reader, job, url):
    if not thread_permalink(url,job['post'],reader.config['subreddits']):
        raise PilotError('Provide a post permalink in the approved community')
    reader._goto(url)
    data = reader.page.evaluate(dom.THREAD)
    normalize = lambda text: ' '.join(text.split())
    expected_id = 't3_'+urlparse(url).path.strip('/').split('/')[3]
    expected_user = job.get('reddit_username') or reader.config['publishing']['reddit_username']
    if (data['id'] != expected_id or data['author'].lower() != expected_user.lower() or
            normalize(data['title']) != normalize(job['title']) or normalize(data['body']) != normalize(job['draft'])):
        raise PilotError('Could not verify the approved post title, body and expected author')
    return url


def publish_thread(reader, job, workflow):
    post = job['post']
    require_post_policy(post,reader.config)
    reader._goto(post['url'])
    username = reader._account()
    if username.lower() != reader.config['publishing']['reddit_username'].lower():
        reader._attention('account','Dedicated browser is signed into the wrong Reddit account')
    page = reader.page
    title = page.locator('textarea[name="title"]:visible,input[name="title"]:visible')
    if not title.count():
        title = page.get_by_role('textbox',name=re.compile(r'^Title',re.I))
    body = page.locator('textarea[name="text"]:visible,shreddit-composer [contenteditable="true"][role="textbox"]:visible')
    if title.count() != 1 or body.count() != 1:
        raise PilotError('Text-post composer is unavailable or ambiguous; inspect it before retrying')
    # A URL alone is insufficient: Reddit may allow changing the target in the form.
    community = page.locator('input[name="sr"],input[name="subreddit"],select[name="sr"]')
    if community.count() != 1 or community.input_value().lower() != post['subreddit'].lower():
        raise PilotError('Cannot verify the composer community; use manual publication and verify its permalink')
    title.fill(job['title'])
    body.fill(job['draft'])
    normalize = lambda text: ' '.join(text.split())
    body_text = body.input_value() if body.evaluate('(e)=>e.tagName') == 'TEXTAREA' else body.inner_text()
    if normalize(title.input_value()) != normalize(job['title']) or normalize(body_text) != normalize(job['draft']):
        raise PilotError('Post editor did not preserve the approved title and body')
    button = page.get_by_role('button',name=re.compile(r'^(Post|Submit)$',re.I))
    if button.count() != 1 or not button.is_enabled():
        raise PilotError('Post submit is unavailable or requires a community field such as flair')
    reader._guard()
    if page.url.rstrip('/') != post['url'].rstrip('/') or community.input_value().lower() != post['subreddit'].lower():
        raise PilotError('Post destination changed before submission')
    require_post_policy(post,reader.config)
    if reader._account().lower() != username.lower():
        reader._attention('account','Reddit account changed before submission')
    workflow.mark_submitting(job['item_id'],reader.config)
    button.click()
    deadline = time.monotonic()+18
    while time.monotonic() < deadline:
        reader._guard()
        if thread_permalink(page.url,post,reader.config['subreddits']):
            return verify_thread(reader,job,page.url)
        page.wait_for_timeout(700)
    raise PilotError('Post submission outcome is uncertain; no automatic resend')
