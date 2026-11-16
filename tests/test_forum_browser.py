"""Intercepted browser integration fixtures; never send to an external platform."""
import html
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import socket
import threading
import time
import unittest
from unittest.mock import Mock

from pilot.core import Store, load_config, PilotError
from pilot.channels import channel_config
from pilot.forum_browser import ForumBrowser, SELECTORS
from pilot.workflow import Workflow
from pilot.automation import Automation, record_quality
from pilot.browser import BrowserAttention
from pilot.campaigns import create_campaign

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('PILOT_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Enable intercepted Chrome fixtures')
class ForumBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start(); cls.browser=cls.pw.chromium.launch(channel='chrome',headless=True)
    @classmethod
    def tearDownClass(cls): cls.browser.close(); cls.pw.stop()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.store=Store(self.root/'state.db'); self.flow=Workflow(self.store)
        base=load_config(ROOT,'config.browser.example.json'); base['ai']='oneapi'
        self.ch=json.loads((ROOT/'channels.example.json').read_text())[0]
        self.ch['username']='medo_team'; self.ch['policy']['reviewed_at']=time.time()
        self.cfg=channel_config(base,self.ch)
        self.context=self.browser.new_context(); self.addCleanup(self.context.close)
        self.context.route('**/*',self.route)
        self.reader=ForumBrowser.__new__(ForumBrowser)
        self.reader.page=self.context.new_page(); self.reader.page.set_default_timeout(1000)
        self.reader.channel=self.ch; self.reader.config=self.cfg; self.reader.selectors=SELECTORS['dev']; self.reader.login_mode=False
        self.source='https://dev.to/person/client-portal-123'; self.published='https://dev.to/medo_team/portal-tutorial-123'
        self.title='Need a client portal with project status'
        self.body='I need a client portal that has a project list and a status screen. How can I start building this?'
        self.identity='medo_team'; self.author='medo_team'; self.challenge=False; self.ai_control=True
        self.submits=0; self.sent=None; self.comment=False; self.draft_existing=False

    def article(self, title, body, author):
        return '<div class="crayons-article__header"><a href="/'+author+'">'+author+'</a><h1>'+html.escape(title)+'</h1><time datetime="2026-11-16T12:00:00Z"></time></div><div id="article-body" style="white-space:pre-wrap">'+html.escape(body)+'</div>'

    def route(self, route):
        url=route.request.url
        header='<header><a id="profile-link" href="/'+self.identity+'">My profile</a></header>'
        if url.endswith('/fixture-submit'):
            self.submits+=1; self.sent=json.loads(route.request.post_data)
            dest=self.source if self.comment else self.published
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'url':dest})); return
        if self.challenge:
            body='<title>Prove your humanity</title><h1>Prove your humanity</h1>'
        elif url in self.ch['feeds']:
            body=header+'<h2 class="crayons-story__title"><a href="'+self.source+'">'+self.title+'</a></h2><h2 class="crayons-story__title"><a href="https://evil.test/person/x">Untrusted destination article</a></h2>'
        elif url=='https://dev.to/new':
            ai='<select name="ai_disclosure"><option value="none">Hand Written</option><option value="autonomous">Fully Autonomous</option></select>' if self.ai_control else ''
            body=header+'<textarea id="article-form-title"></textarea><textarea id="article_body_markdown">'+('Existing draft' if self.draft_existing else '')+'</textarea>'+ai+'<button onclick="send()">Publish</button><script>async function send(){const r=await fetch("/fixture-submit",{method:"POST",body:JSON.stringify({title:document.querySelector("#article-form-title").value,body:document.querySelector("#article_body_markdown").value,ai:document.querySelector("select").value})});location.href=(await r.json()).url}</script>'
        elif url==self.published and self.sent:
            body=header+self.article(self.sent['title'],self.sent['body'],self.author)
        elif url==self.source:
            body=header+self.article(self.title,self.body,'person')
            if self.sent:
                body+='<div class="comment" data-author="'+self.author+'"><div class="comment__body" style="white-space:pre-wrap">'+html.escape(self.sent['body'])+'</div><a href="'+self.source+'#comment-123">Permalink</a></div>'
            body+='<form onsubmit="send(event)"><textarea name="comment[body_markdown]"></textarea><button type="submit">Submit</button></form><script>async function send(e){e.preventDefault();const r=await fetch("/fixture-submit",{method:"POST",body:JSON.stringify({body:document.querySelector("textarea").value})});location.href=(await r.json()).url}</script>'
        else: route.abort(); return
        route.fulfill(status=200,content_type='text/html',body='<!doctype html>'+body)

    def job(self,campaign=False):
        if campaign:
            post=create_campaign(self.cfg['automation']['campaign_plan'][0],self.cfg)
            result=dict(decision='recommend',title='A proposed client portal starting point',draft='I work on MeDo.\n\nA proposed client portal tutorial for testing.\n\nAI disclosure: Fully Autonomous.')
        else:
            posts=self.reader.collect(); post=self.reader.enrich(posts[0])
            result=dict(decision='recommend',draft='Try MeDo for your client portal with a project list and a status screen. Start by describing the fields on each screen, then test changing status on a sample project.\n\nI work on MeDo.\nAutomated MeDo team response.')
        self.store.ingest(post); self.store.complete(post['id'],record_quality(self.store,post,result,self.cfg))
        Automation(self.store,self.flow,self.cfg,[]).queue_ready()
        return self.flow.claim_publication(self.cfg)

    def test_discovery_deduplicates_feeds_and_reads_full_context(self):
        posts=self.reader.collect(); self.assertEqual(len(posts),1)
        post=self.reader.enrich(posts[0]); self.assertEqual(post['body'],self.body)
        self.assertEqual(post['author'],'person'); self.assertTrue(post['posted_at'])
        self.assertEqual(self.submits,0)

    def test_autonomous_reply_submits_once_and_verifies_author_text_permalink(self):
        self.comment=True; job=self.job()
        url=self.reader.publish(job,self.flow); self.flow.publication_done(job,url,self.cfg)
        self.assertEqual(url,self.source+'#comment-123'); self.assertEqual(self.submits,1)
        self.assertEqual(self.flow.outbox()[0]['state'],'published')
        self.assertIsNone(self.flow.claim_publication(self.cfg))

    def test_autonomous_dev_article_selects_disclosure_and_readbacks(self):
        job=self.job(campaign=True)
        url=self.reader.publish(job,self.flow); self.flow.publication_done(job,url,self.cfg)
        self.assertEqual(self.submits,1); self.assertEqual(self.sent['ai'],'autonomous')
        self.assertEqual(url,self.published)

    def test_captcha_stops_before_submission(self):
        job=self.job(campaign=True); self.challenge=True
        with self.assertRaises(BrowserAttention): self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_wrong_account_stops_before_submission(self):
        job=self.job(campaign=True); self.identity='someone_else'
        with self.assertRaises(BrowserAttention): self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_no_disclosure_control_means_no_dev_article(self):
        job=self.job(campaign=True); self.ai_control=False
        with self.assertRaisesRegex(PilotError,'disclosure'): self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_existing_draft_is_preserved(self):
        job=self.job(campaign=True); self.draft_existing=True
        with self.assertRaisesRegex(PilotError,'existing draft'): self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_changed_source_stops_stale_reply(self):
        self.comment=True; job=self.job(); self.body+=' This issue is now solved.'
        with self.assertRaisesRegex(PilotError,'changed'): self.reader.publish(job,self.flow)
        self.assertEqual(self.submits,0)

    def test_uncertain_readback_never_repeats_submit(self):
        self.comment=True; job=self.job(); self.author='wrong_author'
        with self.assertRaisesRegex(PilotError,'reconcile'): self.reader.publish(job,self.flow)
        self.flow.publication_failed(job['item_id'],'Readback mismatch')
        self.assertEqual(self.submits,1); self.assertEqual(self.flow.outbox()[0]['state'],'uncertain')
        self.assertIsNone(self.flow.claim_publication(self.cfg))

    def test_dashboard_lists_channels_and_queues_account_control_without_browser_access(self):
        from pilot.service import server_for
        config=load_config(ROOT,'config.browser.example.json'); config['ai']='oneapi'; config['channels']=[self.ch]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); config['port']=sock.getsockname()[1]
        worker=Mock()
        server=server_for(ROOT,config,self.store,worker)
        # Windows cannot remove SQLite files while a dashboard request still
        # owns a handle. Join request handlers before the fixture directory.
        server.daemon_threads=False
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        def close_dashboard():
            self.reader.page.close()
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.addCleanup(close_dashboard)
        self.context.unroute('**/*')
        page=self.reader.page; errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_default_timeout(10000)
        page.goto('http://127.0.0.1:'+str(config['port']),wait_until='domcontentloaded',timeout=10000)
        card=page.locator('#channels article').filter(has_text='DEV Community')
        card.get_by_role('button',name='Open account login').wait_for(state='visible')
        with page.expect_response(lambda r:r.url.endswith('/api/channels/control')) as response:
            card.get_by_role('button',name='Open account login').click()
        self.assertEqual(response.value.status,200)
        self.assertEqual(self.store.setting('channel.command.dev'),'open')
        self.assertEqual(errors,[])
        worker.wake.set.assert_called_once()


if __name__=='__main__':unittest.main()
