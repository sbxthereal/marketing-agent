"""Incremental browser discovery and deterministic reviewed comment submission."""
import time
from urllib.parse import urljoin, urlparse, urlunparse
import re

from .core import PilotError, reddit_url, require_live_permission, validate_post
from . import dom
from .workflow import comment_permalink
from .discovery import timestamp, search_target
from .platform_support import chrome_options
from .context import original_text, thread_context, op_followups_hash


def normalize_records(records, subreddit, config):
    output = {}
    for rec in records:
        href = rec.get('href', '')
        if not isinstance(href, str):
            continue
        url = urljoin('https://www.reddit.com', href)
        try:
            if not reddit_url(url, [subreddit]):
                continue
        except ValueError:
            continue
        u = urlparse(url)
        parts = [p for p in u.path.split('/') if p]
        if len(parts) < 4:
            continue
        is_comment = len(parts) >= 6 and re.fullmatch(r'[a-zA-Z0-9]+', parts[5]) is not None
        key = ('t1_' + parts[5]) if is_comment else ('t3_' + parts[3])
        title = str(rec.get('title', '')).strip()[:1000]
        body = str(rec.get('body', '')).strip()[:20000]
        if not title or len(title + body) < 20:
            continue
        post = {'id': key, 'kind': 'comment' if is_comment else 'post', 'subreddit': subreddit,
                'url': urlunparse(('https', 'www.reddit.com', u.path, '', '', '')),
                'title': title, 'body': body, 'author':str(rec.get('author',''))[:30],
                'posted_at':str(rec.get('created',''))[:50]}
        validate_post(post, config)
        if key not in output or len(output[key]['body']) < len(body):
            output[key] = post
    return list(output.values())


class BrowserAttention(PilotError):
    """Access/account/challenge error requiring a person rather than a retry."""
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def public_page_url(value):
    """Do not put query strings, credentials, or fragments in dashboard alerts."""
    u = urlparse(value)
    if u.scheme == 'https' and u.hostname in ('reddit.com', 'www.reddit.com', 'old.reddit.com') and not u.username and not u.password:
        return urlunparse(('https', u.hostname, u.path, '', '', ''))
    return ''


class BrowserReader:
    def __init__(self, root, config, catalog, workflow=None):
        require_live_permission(config, catalog)
        self.root, self.config, self.workflow = root, config, workflow
        self.pw = self.context = None
        self._start()

    def _start(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.profile = self.root / 'runtime' / 'chrome-profile'
        backend = self.config.get('browser_backend', 'chrome')
        self.external = backend in ('ixbrowser', 'aliasmode')
        self.attached_browser = None
        try:
            if self.external:
                if backend == 'aliasmode':
                    from .aliasmode import open_profile
                    default_port = 50400
                else:
                    from .ixbrowser import open_profile
                    default_port = 53200
                self.cdp_url = open_profile(self.config[backend].get('port',default_port), self.config[backend + '_profile_id'])
                self.attached_browser = self.pw.chromium.connect_over_cdp(self.cdp_url)
                self.context = self.attached_browser.contexts[0]
                if any(p.url != 'about:blank' and urlparse(p.url).hostname not in
                       ('www.reddit.com','reddit.com','old.reddit.com') for p in self.context.pages):
                    raise PilotError('Use a dedicated managed profile containing only Reddit tabs; close unrelated tabs before resuming')
                self.page = self.context.new_page()
            else:
                self.context = self.pw.chromium.launch_persistent_context(
                    str(self.profile), **chrome_options(self.config.get('browser_headless',False)),
                    args=['--remote-debugging-port=0','--remote-debugging-address=127.0.0.1'],
                    accept_downloads=False, viewport={'width':1280,'height':900})
                self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(12000)
            (self.page if self.external else self.context).route('**/*', self._route)
        except Exception as error:
            self.close()
            if isinstance(error, PilotError):
                raise
            raise PilotError('Could not launch the dedicated Chrome profile') from None

    def _route(self, route):
        req = route.request
        if req.is_navigation_request() and req.frame == self.page.main_frame:
            u = urlparse(req.url)
            if u.scheme != 'https' or u.hostname not in ('reddit.com','www.reddit.com','old.reddit.com'):
                return route.abort()
        return route.continue_()

    def _guard(self, response=None):
        if response and response.status in (401,403,429):
            self._attention('access', 'Reddit access/rate limit needs attention; no bypass attempted')
        body = self.page.locator('body').inner_text(timeout=12000)
        title = self.page.title().lower()
        has_posts = self.page.locator('shreddit-post,.thing.link,a[id^="search-post-title-"]').count() > 0
        if ('prove your humanity' in title or 'verify you are human' in title or
                (not has_posts and any(s in body.lower() for s in ('prove your humanity', 'complete the captcha', 'verify you are human', 'verify you’re human')))):
            self._attention('captcha', 'Reddit human verification required. Solve it in this server browser, then check access.')
        if any(s in body.lower() for s in ("you've been blocked",'verify you are human','verify you’re human','unusual traffic','complete the captcha','blocked by network security',"you're doing that too much",'you’re doing that too much','looks like you’ve been doing that a lot')):
            self._attention('access', 'Reddit challenge or block detected')
        if '/login' in self.page.url:
            self._attention('login', 'Sign in through the dedicated browser before resuming')
        return body

    def _attention(self, kind, message):
        backend = self.config.get('browser_backend', 'chrome')
        ident = self.config.get(backend + '_profile_id', '')
        details = {'kind': kind, 'backend': backend, 'profile_id': ident,
                   'profile_label': self.config.get('browser_profile_label') or ('Dedicated Chrome profile' if backend == 'chrome' else backend + ' profile ' + str(ident)),
                   'expected_account': self.config.get('publishing', {}).get('reddit_username', ''),
                   'url': public_page_url(getattr(self, 'target_url', self.page.url))}
        self.handoff_pending = True
        raise BrowserAttention(message, details)

    def verify_attention(self, attention):
        backend = self.config.get('browser_backend', 'chrome')
        if (attention.get('backend') != backend or attention.get('profile_id', '') != self.config.get(backend + '_profile_id', '') or
                attention.get('expected_account', '') != self.config.get('publishing', {}).get('reddit_username', '')):
            raise PilotError('Profile/account assignment changed; restore the original assignment before checking')
        url = attention.get('url', '')
        u = urlparse(url)
        parts = u.path.split('/')
        if (not public_page_url(url) or len(parts) < 3 or parts[1] != 'r' or
                parts[2].lower() not in {s.lower() for s in self.config['subreddits']}):
            raise PilotError('No approved source page is recorded; inspect the original browser session')
        self.handoff_pending = True
        # The human may have navigated back after solving. Never navigate, solve,
        # reload a challenge, or open another account as part of this check.
        if self.page.is_closed() or public_page_url(self.page.url) != url:
            pages = [p for p in self.context.pages if public_page_url(p.url) == url]
            if len(pages) != 1:
                raise PilotError('Open the recorded source page in the same server profile, then check again')
            self.page = pages[0]
            if getattr(self, 'external', False):
                self.page.route('**/*', self._route)
        self.target_url = url
        self._guard()
        composer = u.path.rstrip('/').endswith('/submit') and self.page.locator('textarea[name="title"],input[name="title"]').count() == 1
        if not composer and not self.page.locator('shreddit-post,.thing.link,a[id^="search-post-title-"]').count():
            raise PilotError('No readable Reddit posts yet. Complete verification and return to the recorded source page.')
        expected = attention.get('expected_account')
        if expected and self._account().lower() != expected.lower():
            raise PilotError('The browser is signed into a different Reddit account')
        self.handoff_pending = False

    def _goto(self, url):
        self.target_url = url
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
        return self._guard(response)

    def _records(self, search=False):
        try:
            self.page.locator('a[id^="search-post-title-"]' if search else 'shreddit-post,.thing.link').first.wait_for(state='attached', timeout=12000)
        except Exception:
            pass
        return self.page.evaluate(dom.SEARCH if search else dom.FEED)

    def collect(self):
        output=[]
        for sub in self.config['subreddits']:
            output.extend(self._collect_source(sub,'https://www.reddit.com/r/'+sub+'/new/'))
        if self.workflow and self.config.get('search_queries'):
            store=self.workflow.store
            suffix=self.config.get('search_state_suffix','')
            if time.time()-float(store.setting('search_last'+suffix,'0')) >= self.config.get('search_interval_seconds',1800):
                cursor=int(store.setting('search_cursor'+suffix,'0'))
                target=search_target(self.config,cursor)
                if target:
                    sub,query,url=target
                    output.extend(self._collect_source(sub,url,query))
                    store.set_setting('search_last'+suffix,time.time())
                    store.set_setting('search_cursor'+suffix,cursor+1)
        return output

    def _collect_source(self, sub, url, query=''):
        output = []
        # The same parser loop handles each source, with independent coverage logs.
        started, pages, found, duplicates, new = time.time(), 0, 0, 0, 0
        state, page_ids, candidates = 'complete', set(), {}
        try:
            body = self._goto(url)
            cutoff = time.time()-self.config.get('search_lookback_hours' if query else 'lookback_hours',72 if query else 24)*3600
            max_pages = self.config.get('max_search_pages' if query else 'max_feed_pages',2 if query else 3)
            previous_complete = not query and self.workflow and self.workflow.scan_state(sub) == 'complete'
            for index in range(max_pages):
                records = self._records(bool(query))
                if not records and index == 0:
                    if any(s in body.lower() for s in ('no posts yet','there are no posts','no results','couldn’t find any results',"couldn't find any results")):
                        break
                    if not self.config.get('browser_fallback', True) or not self.workflow:
                        raise PilotError('Reddit feed layout was not recognized')
                    from .agent_browser import agent_discover
                    cdp_url = getattr(self,'cdp_url',None)
                    if not cdp_url:
                        port = (self.profile/'DevToolsActivePort').read_text(encoding='utf-8-sig').splitlines()[0]
                        cdp_url = 'http://127.0.0.1:'+port
                    session=self.context.new_cdp_session(self.page)
                    try: target_id=session.send('Target.getTargetInfo')['targetInfo']['targetId']
                    finally: session.detach()
                    records = agent_discover(cdp_url,self.page.url,self.root,self.config,self.workflow.store,pump=lambda:self.page.wait_for_timeout(100),target_id=target_id)
                    if not records or not normalize_records(records,sub,self.config):
                        raise PilotError('Browser agent returned no valid thread links on an unrecognized page')
                pages += 1
                boundary, unseen_on_page, consecutive_seen, new_on_page = False, 0, 0, 0
                for rec in records:
                    if rec.get('sticky'):
                        continue
                    parsed = normalize_records([rec],sub,self.config)
                    if not parsed:
                        continue
                    post = parsed[0]
                    if post['kind'] != 'post' or post['id'] in page_ids:
                        continue
                    page_ids.add(post['id']); found += 1; unseen_on_page += 1
                    created = timestamp(rec.get('created'))
                    if created and created < cutoff:
                        boundary = True
                        continue
                    boundary = False  # A newer record after an old one invalidates that boundary.
                    if self.workflow and self.workflow.seen(post['id']):
                        duplicates += 1; consecutive_seen += 1
                        continue
                    consecutive_seen = 0
                    new_on_page += 1
                    post['discovery_source'] = 'search: '+query if query else 'new feed'
                    candidates[post['id']] = post
                # A seen run in the middle of a batch is not a safe checkpoint.
                # One extra bounded batch also catches a new tail after an old run.
                if boundary or (previous_complete and consecutive_seen >= 3 and new_on_page == 0):
                    break
                if index+1 == max_pages or (index > 0 and unseen_on_page == 0):
                    state = 'coverage_capped'
                    break
                # Scroll the rendered feed, without direct/internal API calls.
                self.page.mouse.wheel(0, 1500)
                self.page.wait_for_timeout(700)
                self._guard()
            # Commit each successful community separately. Other communities
            # failing later cannot lose this checkpoint.
            for post in candidates.values():
                if self.workflow:
                    new += int(self.workflow.store.ingest(post))
                else:
                    new += 1
                output.append(post)
            if self.workflow:
                self.workflow.record_scan(sub,started,pages,found,new,duplicates,state,source='search: '+query if query else 'new feed')
        except Exception as e:
            if self.workflow:
                self.workflow.record_scan(sub,started,pages,found,0,duplicates,'error',str(e),source='search: '+query if query else 'new feed')
            raise
        return output

    def enrich(self, post):
        self._goto(post['url'])
        self.page.locator('shreddit-post,.thing.link').first.wait_for(state='attached')
        data = self.page.evaluate(dom.THREAD)
        if not data['title']:
            raise PilotError('Thread title could not be read')
        if data['id'] != post['id']:
            raise PilotError('Thread identity did not match its source URL')
        result = dict(post, title=data['title'][:1000], **thread_context(data),
                      author=data.get('author') or post.get('author',''),posted_at=data.get('created') or post.get('posted_at',''))
        result['closed'] = bool(data['locked'] or data['archived'])
        return result

    def _account(self):
        names = self.page.evaluate(dom.ACCOUNT)
        if not names:
            button = self.page.locator('#expand-user-drawer-button')
            if button.count() and button.is_visible():
                button.click()
                self.page.locator('#user-drawer-content a[href*="/user/"]').first.wait_for(state='visible')
                names = self.page.evaluate(dom.ACCOUNT)
                self.page.keyboard.press('Escape')
        if len(names) != 1:
            self._attention('account', 'Could not verify the logged-in Reddit account')
        return names[0]

    def _existing_comment(self, post, draft, username):
        data = self.page.evaluate(dom.THREAD)
        normalize = lambda s: ' '.join(s.split())
        for c in data['comments']:
            if c['author'].lower() != username.lower() or normalize(c['body']) != normalize(draft):
                continue
            permalink = urljoin('https://www.reddit.com',c['href'])
            if not comment_permalink(permalink,post,self.config['subreddits']) and c['id'].startswith('t1_'):
                permalink = post['url'].rstrip('/')+'/'+c['id'][3:]+'/'
            if comment_permalink(permalink,post,self.config['subreddits']):
                return permalink
        return None

    def publish(self, job, workflow):
        from .campaigns import is_campaign
        if is_campaign(job['post']):
            from .thread_publish import publish_thread
            return publish_thread(self,job,workflow)
        post, draft = job['post'], job['draft']
        self._goto(post['url'])
        username = self._account()
        if username.lower() != self.config['publishing']['reddit_username'].lower():
            self._attention('account', 'Dedicated browser is signed into the wrong Reddit account')
        current = self.page.evaluate(dom.THREAD)
        if current['id'] != post['id']:
            raise PilotError('Thread identity changed before publication')
        if current['locked'] or current['archived']:
            raise PilotError('Thread is locked or archived')
        original_body = original_text(post)
        if current['title'].strip() != post['title'].strip() or current['body'][:10000].strip() != original_body:
            raise PilotError('Original post changed since review; generate a fresh draft')
        existing = self._existing_comment(post,draft,username)
        if existing:
            return existing
        if post.get('op_followups_hash') and post['op_followups_hash'] != op_followups_hash(current):
            raise PilotError('The original author added or changed a follow-up; review a fresh draft before replying')
        # Recheck the live discussion even if the draft was approved hours ago.
        if current.get('author','').lower()==username.lower():
            raise PilotError('Own account’s thread; no promotional reply')
        if any(c['author'].lower()==username.lower() or re.search(r'\bmedo\b',c['body'],re.I) for c in current['comments']):
            raise PilotError('Account already participated or MeDo is already mentioned; no repeated pitch')
        # Only a top-level comment composer, never a reply-to-comment control.
        selector = 'shreddit-composer:visible:not(shreddit-comment shreddit-composer),form.usertext:visible:not(.thing.comment form.usertext)'
        container = self.page.locator(selector).first
        if not container.count():
            trigger = self.page.locator('comment-composer-host [data-testid="trigger-button"]:visible')
            if not trigger.count():
                trigger = self.page.get_by_text('Add a comment',exact=True)
            if trigger.count() == 1:
                trigger.click()
        container = self.page.locator(selector).first
        box = container.locator('[contenteditable="true"][role="textbox"],textarea[name="text"],textarea[name="comment"],div[contenteditable="true"]').first
        if not box.count() or not box.is_visible():
            raise PilotError('Top-level comment editor is unavailable; inspect the thread')
        box.fill(draft)
        observed = box.input_value() if box.evaluate('(e)=>e.tagName') == 'TEXTAREA' else box.inner_text()
        if ' '.join(observed.split()) != ' '.join(draft.split()):
            raise PilotError('Comment editor did not preserve the approved draft')
        button = container.get_by_role('button',name=re.compile(r'^(Comment|Save)$',re.I))
        if button.count() != 1 or not button.is_enabled():
            raise PilotError('Comment submit control is unavailable or ambiguous')
        self._guard()
        workflow.mark_submitting(job['item_id'],self.config)  # Durable before the external write.
        button.click()
        deadline = time.monotonic()+18
        while time.monotonic() < deadline:
            self._guard()
            permalink = self._existing_comment(post,draft,username)
            if permalink:
                # Reload its permalink: an optimistic local insertion is not proof.
                self._goto(permalink)
                if self._existing_comment(post,draft,username):
                    return permalink
                raise PilotError('Comment appeared locally but could not be read back')
            self.page.wait_for_timeout(700)
        raise PilotError('Submission outcome is uncertain; no automatic resend')

    def verify_publication(self, job, url):
        from .campaigns import is_campaign
        if is_campaign(job['post']):
            from .thread_publish import verify_thread
            return verify_thread(self,job,url)
        if not comment_permalink(url,job['post'],self.config['subreddits']):
            raise PilotError('Provide a comment permalink from the original thread')
        self._goto(url)
        result=self._existing_comment(job['post'],job['draft'],self.config['publishing']['reddit_username'])
        if not result:
            raise PilotError('Could not verify the approved text and expected author at that permalink')
        return result

    def close(self):
        try:
            if getattr(self,'external',False):
                if getattr(self,'page',None) and not self.page.is_closed():
                    self.page.unroute('**/*', self._route)
                    if not getattr(self, 'handoff_pending', False):
                        self.page.close()
            elif self.context:
                self.context.close()
        finally:
            if self.pw:
                self.pw.stop()
            self.context = self.pw = None
