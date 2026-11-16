"""Bounded browser adapters. No account creation, CAPTCHA solving or stealth.

Selectors fail closed when a platform changes. Submit is one explicit click after
the shared outbox records the attempt; success requires matching author + text.
"""
import datetime as dt
import hashlib
import re
import time
from urllib.parse import urljoin, urlparse, urlunparse

from .browser import BrowserAttention
from .channels import same_origin, source_url, published_url
from .core import PilotError
from .platform_support import chrome_options


SELECTORS = {
    'dev': dict(feed='.crayons-story__title a', article='#article-body', title='h1',
                author='.crayons-article__header a[href]', comments='.comment',
                identity='#profile-link, .crayons-header__menu a[href]',
                reply='textarea[name="comment[body_markdown]"]', reply_submit='button[type="submit"], input[type="submit"]',
                post_title='#article-form-title, textarea[name="article[title]"]',
                post_body='#article_body_markdown, textarea[name="article[body_markdown]"]',
                post_submit='button:has-text("Publish")',
                ai='select[name*="ai"], select[id*="ai_disclosure"]'),
    'indiehackers': dict(feed='a[href^="/post/"]', article='.post__body', title='h1',
                         author='.post__author a', comments='.comment', identity='.user-menu a[href], header a[data-testid="profile-link"]',
                         reply='textarea[placeholder*="comment" i]', reply_submit='button[type="submit"]',
                         post_title='input[name="title"]', post_body='textarea[name="body"]',
                         post_submit='button[type="submit"]', ai=''),
    'discourse': dict(feed='.topic-list a.title', article='#post_1 .cooked', title='#topic-title h1',
                      author='#post_1 .username a', comments='.topic-post',
                      identity='#current-user a[href^="/u/"], .user-menu a[href^="/u/"]',
                      reply='.d-editor-input', reply_submit='.submit-panel button.create',
                      post_title='#reply-title', post_body='.d-editor-input',
                      post_submit='.submit-panel button.create', ai=''),
}


def plain(text):
    return ' '.join(re.sub(r'(?m)^#{1,6}\s*', '', text).replace('**', '').split())


class ForumBrowser:
    def __init__(self, root, config):
        from playwright.sync_api import sync_playwright
        self.root, self.config = root, config
        self.channel = config['channel']
        self.selectors = dict(SELECTORS[self.channel['type']], **self.channel.get('selectors', {}))
        self.pw = self.context = None
        self.login_mode = False
        try:
            self.pw = sync_playwright().start()
            profile = root/'runtime'/'channel-profiles'/self.channel['id']
            self.context = self.pw.chromium.launch_persistent_context(str(profile), **chrome_options(False),
                accept_downloads=False, viewport={'width':1280, 'height':900})
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.context.route('**/*', self.route)
            self.page.set_default_timeout(10000)
        except Exception:
            self.close(); raise

    def route(self, route):
        # Subresources may use the platform CDN; automatic document navigation
        # remains on the fixed origin. Manual login can use the site's SSO.
        request = route.request
        if request.is_navigation_request() and request.frame == self.page.main_frame:
            if not self.login_mode and not same_origin(request.url, self.channel):
                route.abort(); return
        route.continue_()

    def guard(self):
        if not same_origin(self.page.url, self.channel):
            raise BrowserAttention('Return to the configured platform after completing sign-in', {'kind':'login'})
        title = self.page.title().lower()
        text = self.page.locator('body').inner_text()[:1800].lower()
        challenge = bool(re.search(r'just a moment|prove your humanity|attention required|access denied|verify you are human', title))
        if len(text) < 1200 and re.search(r'prove your humanity|verify you are human|complete the captcha', text): challenge = True
        if challenge:
            raise BrowserAttention('CAPTCHA/access check: solve it in this same Windows profile, then Check and resume',
                                   {'kind':'captcha', 'profile_label':self.channel['id']})

    def goto(self, url):
        if not same_origin(url, self.channel): raise PilotError('Navigation outside the configured platform is blocked')
        self.login_mode = False
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=45000)
        if response and response.status in (401, 403, 429):
            raise BrowserAttention('Platform refused access or rate-limited this profile; activity is paused', {'kind':'access'})
        self.guard()

    def open_login(self):
        if not self.channel['origin']: raise PilotError('Choose the forum origin before opening its profile')
        self.login_mode = True
        path = {'dev':'/enter', 'indiehackers':'/sign-in', 'discourse':'/login'}[self.channel['type']]
        self.page.goto(self.channel['origin']+path, wait_until='domcontentloaded', timeout=45000)
        self.page.bring_to_front()

    def identity(self):
        self.guard()
        if self.channel['type'] == 'discourse' and self.page.locator('#current-user').count() and not self.page.locator(self.selectors['identity']).count():
            self.page.locator('#current-user').click()
        links = self.page.locator(self.selectors['identity']).evaluate_all('(xs)=>xs.map(x=>({href:x.getAttribute("href"),user:x.getAttribute("data-username")}))')
        candidates = set()
        for link in links:
            path = urlparse(urljoin(self.channel['origin'], link.get('href') or '')).path
            pattern = r'^/u/([\w.-]+)(?:/.*)?$' if self.channel['type'] == 'discourse' else r'^/([\w.-]+)/?$'
            m = re.fullmatch(pattern, path)
            if m and m[1] not in ('dashboard', 'settings', 'readinglist', 'enter', 'sign-in', 'new'):
                candidates.add(m[1])
        if len(candidates) != 1:
            raise BrowserAttention('Could not verify the signed-in account from the profile menu', {'kind':'login'})
        self.login_mode = False
        return candidates.pop()

    def collect(self):
        posts = {}
        for feed in self.channel['feeds']:
            self.goto(feed)
            try:
                self.page.locator(self.selectors['feed']).first.wait_for(state='attached',timeout=10000)
            except Exception:
                raise PilotError('Feed did not expose readable thread links; coverage is unknown, not zero demand') from None
            links = self.page.locator(self.selectors['feed']).evaluate_all('(xs)=>xs.slice(0,30).map(x=>({url:x.href,title:x.innerText}))')
            for link in links:
                u = urlparse(link['url'])
                url = urlunparse((u.scheme, u.netloc, u.path.rstrip('/'), '', '', ''))
                if not source_url(url, self.channel) or len(link['title'].strip()) < 10: continue
                ident = self.channel['id']+':'+hashlib.sha256(url.encode()).hexdigest()[:32]
                posts[ident] = dict(id=ident, platform=self.channel['id'], platform_type=self.channel['type'], kind='post',
                                    subreddit=self.channel['id'], url=url, title=link['title'].strip()[:1000], body='',
                                    discovery_source=self.channel['type']+' browser feed')
        return list(posts.values())

    def enrich(self, post):
        if not source_url(post['url'], self.channel): raise PilotError('Invalid source thread')
        self.goto(post['url'])
        article = self.page.locator(self.selectors['article'])
        article.first.wait_for(state='attached',timeout=10000)
        if article.count() != 1: raise PilotError('Article body could not be identified uniquely; adapter needs checking')
        body = article.inner_text().strip()[:10000]
        if len(body) < 40: raise PilotError('Full source text is unavailable; no recommendation will be generated')
        title = self.page.locator(self.selectors['title']).first.inner_text().strip()[:1000]
        author_links = self.page.locator(self.selectors['author']).evaluate_all('(xs)=>xs.map(x=>x.getAttribute("href"))')
        pattern = r'^/u/([\w.-]+)(?:/.*)?$' if self.channel['type'] == 'discourse' else r'^/([\w.-]+)/?$'
        authors = [re.fullmatch(pattern, urlparse(urljoin(self.page.url, x or '')).path) for x in author_links]
        author = next((m[1] for m in authors if m), '')
        times = self.page.locator('time[datetime], .relative-date[data-time]').evaluate_all('(xs)=>xs.map(x=>x.getAttribute("datetime")||x.getAttribute("data-time"))')
        # Only the first post's timestamp: missing/ambiguous age fails closed in the worker.
        created = times[0] if times else ''
        if created.isdigit():
            value = int(created)/(1000 if len(created)==13 else 1)
            created = dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat()
        comments = self.page.locator(self.selectors['comments']).evaluate_all('''(xs)=>xs.slice(0,14).map(x=>({
          body:(x.querySelector('.comment__body,.cooked,.comment__content')||x).innerText.slice(0,1200),
          author:x.getAttribute('data-author')||x.querySelector('.username a,.comment__header a[href]')?.getAttribute('href')||''}))''')
        discussion, remaining = [], 5000
        for c in comments:
            if self.channel['type'] == 'discourse' and plain(c['body']) == plain(body): continue
            m = re.fullmatch(pattern, urlparse(urljoin(self.page.url, c['author'])).path)
            who = m[1] if m else c['author']
            text = c['body'][:remaining]
            if text: discussion.append(dict(role='op' if author and who == author else 'community', body=text))
            remaining -= len(text)
        return dict(post, title=title, body=body, original_body=body, author=author, posted_at=created, discussion=discussion)

    def unique(self, selector):
        nodes = self.page.locator(selector).filter(visible=True)
        if nodes.count() != 1: raise PilotError('Expected one visible composer control; publishing stopped')
        return nodes

    def ai_disclosure(self):
        if self.channel['type'] != 'dev': return
        # DEV's explicit disclosure control is mandatory, in addition to the body notice.
        select = self.page.locator(self.selectors['ai'])
        if select.count() != 1:
            raise PilotError('DEV AI disclosure control is unavailable; no article submitted')
        options = select.locator('option').evaluate_all('(xs)=>xs.map(x=>({value:x.value,text:x.textContent}))')
        matches = [x for x in options if re.search('fully autonomous', x['text'], re.I)]
        if len(matches) != 1: raise PilotError('DEV Fully Autonomous disclosure option is unavailable')
        select.select_option(matches[0]['value'])

    def publish(self, job, flow):
        post = job['post']
        campaign = post['kind'] == 'campaign'
        if campaign:
            self.goto(self.channel.get('compose_url') or self.channel['origin'])
        else:
            current = self.enrich(post)
            if current['title'] != post['title'] or current['body'] != post['body'] or current['discussion'] != post.get('discussion', []):
                raise PilotError('Source or discussion changed since drafting; stale reply was not submitted')
        if self.identity().casefold() != job['reddit_username'].casefold():
            raise BrowserAttention('Wrong account in this fixed profile; no publication attempted', {'kind':'account'})
        if self.channel['type'] == 'discourse':
            button = '#create-topic' if campaign else '.topic-footer-main-buttons button.reply'
            self.unique(button).click()
        body = self.unique(self.selectors['post_body' if campaign else 'reply'])
        if body.input_value().strip(): raise PilotError('Composer already contains text; preserving the existing draft')
        if campaign:
            title = self.unique(self.selectors['post_title'])
            if title.input_value().strip(): raise PilotError('Composer already contains a title; preserving it')
        body.fill(job['draft'])
        if campaign:
            title.fill(job['title'])
            self.ai_disclosure()
        if body.input_value() != job['draft']: raise PilotError('Composer text mismatch')
        if campaign and title.input_value() != job['title']: raise PilotError('Composer title mismatch')
        if campaign:
            submit = self.unique(self.selectors['post_submit'])
        else:
            form = body.locator('xpath=ancestor::form[1]')
            submit = form.locator(self.selectors['reply_submit']) if form.count() else self.page.locator(self.selectors['reply_submit'])
            submit = submit.filter(visible=True)
            if submit.count() != 1: raise PilotError('Reply submit control is ambiguous')
        if not submit.is_enabled(): raise PilotError('Platform has disabled the submit control')
        # Last identity/challenge checks before the irreversible boundary.
        self.guard()
        if self.identity().casefold() != job['reddit_username'].casefold(): raise PilotError('Account changed before submission')
        flow.mark_submitting(job['item_id'], self.config)
        submit.click()
        # Never retry a click, including after a timeout.
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            self.guard()
            url = self.readback(job)
            if url:
                self.goto(url)
                confirmed = self.readback(job)
                if confirmed and published_url(confirmed, post, self.channel): return confirmed
                raise PilotError('Publication did not survive readback; reconcile without resending')
            self.page.wait_for_timeout(300)
        raise PilotError('No verified publication found after submit; reconcile without resending')

    def readback(self, job):
        campaign = job['post']['kind'] == 'campaign'
        if campaign:
            if not published_url(self.page.url, job['post'], self.channel): return ''
            current = self.enrich(dict(job['post'], kind='post', url=self.page.url))
            if (current['author'].casefold() == job['reddit_username'].casefold() and
                    plain(current['body']) == plain(job['draft']) and current['title'] == job['title']):
                return self.page.url
            return ''
        records = self.page.locator(self.selectors['comments']).evaluate_all('''(xs)=>xs.map(x=>({
          body:(x.querySelector('.comment__body,.cooked,.comment__content')||x).innerText,
          author:x.getAttribute('data-author')||x.querySelector('.username a,.comment__header a[href]')?.getAttribute('href')||'',
          links:[...x.querySelectorAll('a[href]')].map(a=>a.href)}))''')
        for row in records:
            author = row['author'].strip('/').split('/')[-1]
            if author.casefold() != job['reddit_username'].casefold() or plain(row['body']) != plain(job['draft']): continue
            for url in row['links']:
                if published_url(url, job['post'], self.channel): return url
        return ''

    def close(self):
        if self.context:
            self.context.close(); self.context = None
        if self.pw:
            self.pw.stop(); self.pw = None
