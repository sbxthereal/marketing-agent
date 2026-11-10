"""Real Chrome tests against locally fulfilled pages. Never contacts Reddit."""
import html
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from pilot.browser import BrowserReader, BrowserAttention
from pilot.core import Store, PilotError
from pilot.workflow import Workflow, draft_hash
from pilot.context import thread_context


@unittest.skipUnless(os.environ.get('PILOT_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'), 'Set PILOT_BROWSER_TESTS=1 in the browser environment')
class BrowserFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(channel='chrome',headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.db');self.flow=Workflow(self.store)
        self.config={'source':'browser','ai':'oneapi','subreddits':['pilottest'],'max_feed_pages':1,'lookback_hours':24,'browser_fallback':False,
          'publishing':{'enabled':True,'permission_confirmed':True,'permission_reference':'LOCAL ROUTED TEST ONLY','reddit_username':'pilot_test','min_interval_seconds':1,'max_per_day':3}}
        self.submits=0;self.comments=[];self.followups=[];self.username='pilot_test';self.locked=False;self.reject=False;self.search_visits=0
        self.post={'id':'t3_abc123','kind':'post','subreddit':'pilottest','url':'https://www.reddit.com/r/pilottest/comments/abc123/help/','title':'Need a portfolio website','body':'I need a website to showcase my design projects.'}
        self.context=self.browser.new_context();self.addCleanup(self.context.close)
        self.context.route('**/*',self.route)
        self.page=self.context.new_page()
        self.reader=BrowserReader.__new__(BrowserReader)
        self.reader.root=Path(self.tmp.name);self.reader.config=self.config;self.reader.workflow=self.flow;self.reader.page=self.page

    def page_html(self,feed=False):
        post=f'<shreddit-post author="asker" id="{self.post["id"]}" post-title="{self.post["title"]}" permalink="{self.post["url"]}" '+('is-locked' if self.locked else '')+f'><h1>{self.post["title"]}</h1><div slot="text-body">{self.post["body"]}</div></shreddit-post>'
        if feed:
            return '<!doctype html><title>Local test feed</title>'+post+'<shreddit-post is-ad post-title="Advertisement" permalink="/r/pilottest/comments/ad123/ad/"></shreddit-post>'
        comments=''.join(f'<shreddit-comment author="pilot_test" thingid="t1_c123" permalink="{self.post["url"]}c123/"><div slot="comment">{html.escape(c)}</div></shreddit-comment>' for c in self.comments)
        comments+=''.join(f'<shreddit-comment author="{author}"><div slot="comment">{html.escape(body)}</div></shreddit-comment>' for author,body in self.followups)
        return '''<!doctype html><style>shreddit-post,shreddit-comment,shreddit-composer{display:block} [contenteditable]{border:1px solid;height:60px}</style>'''+post+f'<div id="user-drawer-content"><a href="/user/{self.username}/">Profile</a></div>'+comments+'''
        <shreddit-composer style="display:none"><div contenteditable="true" role="textbox">WRONG HIDDEN EDITOR</div><button>Comment</button></shreddit-composer>
        <comment-composer-host><button data-testid="trigger-button" onclick="this.hidden=true;document.getElementById('real').style.display='block'">Join the conversation</button>
        <shreddit-composer id="real" style="display:none"><div contenteditable="true" role="textbox"></div><button onclick="send()">Comment</button></shreddit-composer></comment-composer-host>
        <script>async function send(){const text=document.querySelector('#real [contenteditable]').innerText;const r=await fetch('/fixture-submit',{method:'POST',body:JSON.stringify({text})});const d=await r.json();if(d.ok){const c=document.createElement('shreddit-comment');c.setAttribute('author','pilot_test');c.setAttribute('thingid','t1_c123');c.setAttribute('permalink',location.pathname+'c123/');const b=document.createElement('div');b.slot='comment';b.textContent=text;c.append(b);document.body.append(c);}}</script>'''

    def route(self,route):
        # Every network request is intercepted. Only the fake local pages exist.
        if route.request.url.endswith('/fixture-submit'):
            self.submits+=1
            if not self.reject:self.comments.append(json.loads(route.request.post_data)['text'])
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'ok':not self.reject}))
        elif '/r/pilottest/search/' in route.request.url:
            self.search_visits+=1
            now=dt.datetime.now(dt.timezone.utc).isoformat()
            old=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=8)).isoformat()
            cards=''.join(f'<div data-testid="sdui-post-unit"><time datetime="{created}"></time><a id="search-post-title-t3_{key}" href="/r/pilottest/comments/{key}/help/">{title}</a></div>' for key,title,created in [('abc123',self.post['title'],now),('search456','Which builder for a travel app?',now),('old123','Need a very old website',old)])
            route.fulfill(status=200,content_type='text/html',body='<!doctype html>'+cards)
        elif 'www.reddit.com/r/pilottest/' in route.request.url:
            route.fulfill(status=200,content_type='text/html',body=self.page_html('/new/' in route.request.url))
        else:route.abort()

    def job(self):
        self.store.ingest(self.post)
        self.draft='I work on MeDo. Start with a project gallery and a clear contact link.\n\nhttps://medo.dev/'
        self.store.complete(self.post['id'],{'decision':'recommend','draft':self.draft})
        self.store.review(self.post['id'],'approve')
        self.flow.request_publish(self.post['id'],draft_hash(self.draft),self.config)
        return self.flow.claim_publication(self.config)

    def test_feed_extraction_dedup_and_full_thread(self):
        first=self.reader.collect();second=self.reader.collect()
        self.assertEqual(len(first),1);self.assertEqual(second,[])
        self.assertEqual(self.flow.metrics()['duplicates'],1)
        self.assertEqual(self.store.summary()['usage']['calls'],0)
        enriched=self.reader.enrich(first[0]);self.assertIn('showcase',enriched['body'])

    def test_search_discovers_new_thread_without_repeating_feed_or_old_results(self):
        self.config.update(search_queries=['travel app'],max_search_pages=1,search_lookback_hours=72,search_interval_seconds=1800)
        first=self.reader.collect();second=self.reader.collect()
        self.assertEqual({p['id'] for p in first},{'t3_abc123','t3_search456'})
        self.assertEqual(second,[]);self.assertEqual(self.search_visits,1)
        self.assertEqual(self.store.setting('search_cursor'),'1')
        scans=self.flow.metrics()['recent']
        self.assertTrue(any(r['source']=='search: travel app' and r['state']=='complete' for r in scans))
        # A complete search must not erase capped new-feed coverage.
        self.assertEqual(self.flow.scan_state('pilottest'),'coverage_capped')

    def test_complete_worker_only_calls_model_for_new_posts(self):
        from pilot.service import Worker
        from pilot.core import load_config
        config=load_config(Path(__file__).resolve().parents[1],'config.example.json')
        config.update(self.config)
        config['permissions']={'browser_collection':True,'ai_processing':True,'community_rules_reviewed':True,'reference':'LOCAL ROUTED TEST ONLY'}
        config['publishing']={'enabled':False}
        catalog=[{'id':'portfolio','name':'Test portfolio','url':'https://medo.dev/','verified':True,'claims':{'projects':'Project showcase'},'limitations':[]}]
        class CountingGateway:
            n=0
            def chat(self,*args):
                self.n+=1
                return json.dumps({'decision':'recommend','reason':'Explicit request','template_id':'portfolio','evidence':'showcase my design projects','claim_ids':['projects'],'draft':'Start with your three strongest projects.'}),{'prompt_tokens':100,'completion_tokens':50},10
        worker=Worker(Path(self.tmp.name),config,catalog,self.store)
        worker.browser=self.reader;worker.gateway=CountingGateway()
        worker.cycle();worker.next_scan=0;worker.cycle()
        self.assertEqual(worker.gateway.n,2)
        self.assertEqual(self.store.summary()['usage']['calls'],2)
        self.assertEqual(self.store.items()[0]['status'],'review')
        self.assertEqual(self.submits,0)

    def test_real_form_submit_verified_after_navigation(self):
        job=self.job();url=self.reader.publish(job,self.flow)
        self.flow.publication_done(job,url,self.config)
        self.assertEqual(self.submits,1)
        self.assertEqual(self.store.items()[0]['status'],'published')
        self.assertEqual([' '.join(c.split()) for c in self.comments],[' '.join(self.draft.split())])
        self.assertTrue(url.endswith('/c123/'))

    def test_existing_exact_comment_does_not_submit_again(self):
        job=self.job();self.comments=[self.draft]
        self.assertTrue(self.reader.publish(job,self.flow).endswith('/c123/'))
        self.assertEqual(self.submits,0)

    def test_other_existing_comment_from_account_blocks_second_pitch(self):
        job=self.job();self.comments=['An earlier reply with different wording.']
        with self.assertRaisesRegex(PilotError,'already participated'):self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_wrong_account_never_submits(self):
        job=self.job();self.username='wrong_account'
        with self.assertRaises(BrowserAttention):self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_reconciliation_is_read_only(self):
        job=self.job();self.comments=[self.draft]
        self.assertTrue(self.reader.verify_publication(job,self.post['url']+'c123/').endswith('/c123/'))
        self.assertEqual(self.submits,0)

    def test_locked_thread_never_submits(self):
        job=self.job();self.locked=True
        with self.assertRaises(PilotError):self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_changed_post_requires_fresh_review(self):
        job=self.job();self.post['body']='Actually I no longer want any recommendations.'
        with self.assertRaisesRegex(PilotError,'changed'):self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_new_author_requirement_blocks_approved_reply(self):
        snapshot=thread_context({'author':'asker','body':self.post['body'],'comments':[]})
        self.post.update(snapshot)
        job=self.job();self.followups=[('asker','Solved already, no more recommendations.')]
        with self.assertRaisesRegex(PilotError,'follow-up'):self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_unrelated_community_followup_does_not_change_author_snapshot(self):
        self.post.update(thread_context({'author':'asker','body':self.post['body'],'comments':[]}))
        job=self.job();self.followups=[('someone_else','I would like a native app myself.')]
        self.assertTrue(self.reader.publish(job,self.flow).endswith('/c123/'))
        self.assertEqual(self.submits,1)

    def test_rejected_submission_is_uncertain_and_cannot_retry(self):
        job=self.job();self.reject=True
        with self.assertRaisesRegex(PilotError,'uncertain'):
            self.reader.publish(job,self.flow)
        self.flow.publication_failed(job['item_id'],'uncertain')
        self.assertEqual(self.submits,1)
        self.assertEqual(self.flow.outbox()[0]['state'],'uncertain')
        self.assertIsNone(self.flow.claim_publication(self.config))


if __name__=='__main__':unittest.main()
