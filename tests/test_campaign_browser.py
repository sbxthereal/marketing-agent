"""Intercepted browser tests: no network traffic or real Reddit publications."""
import html
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from pilot.browser import BrowserReader, BrowserAttention
from pilot.campaigns import create_campaign, publication_text
from pilot.core import Store, PilotError
from pilot.workflow import Workflow, draft_hash

@unittest.skipUnless(os.environ.get('PILOT_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Enable local Chrome fixtures')
class CampaignBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(channel='chrome',headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.db');self.flow=Workflow(self.store)
        self.config={'source':'browser','subreddits':['pilottest'],'publishing':{'enabled':True,'permission_confirmed':True,'permission_reference':'LOCAL FIXTURE ONLY','reddit_username':'pilot_test'},'community_policies':{'pilottest':{'standalone_posts':True,'reference':'LOCAL FIXTURE ONLY','notes':'Fixture tutorial permitted','reviewed_at':time.time()}}}
        self.post=create_campaign({'subreddit':'pilottest','format':'tutorial','brief':'A specific tutorial about planning a client portal with a project status screen.'},self.config)
        self.result={'decision':'recommend','title':'A useful portal planning tutorial','draft':'I work on MeDo.\n\nA specifically reviewed local fixture body. This is not a live Reddit post.'}
        self.store.ingest(self.post);self.store.complete(self.post['id'],self.result);self.store.review(self.post['id'],'approve')
        self.flow.request_publish(self.post['id'],draft_hash(publication_text(self.result)),self.config)
        self.job=self.flow.claim_publication(self.config)
        self.context=self.browser.new_context();self.addCleanup(self.context.close);self.context.route('**/*',self.route)
        self.page=self.context.new_page();self.reader=BrowserReader.__new__(BrowserReader)
        self.reader.page=self.page;self.reader.config=self.config;self.reader.context=self.context;self.reader.root=Path(self.tmp.name)
        self.submits=0;self.submitted=None;self.community='pilottest';self.author='pilot_test';self.challenge=False
        self.url='https://www.reddit.com/r/pilottest/comments/post123/tutorial/'
    def route(self,route):
        if route.request.url.endswith('/fixture-submit'):
            self.submits+=1;self.submitted=json.loads(route.request.post_data)
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'url':self.url}))
        elif route.request.url==self.post['url']:
            if self.challenge:
                body='<title>Prove your humanity</title><h1>Prove your humanity</h1>'
            else:
                body='<div id="user-drawer-content"><a href="/user/pilot_test/">Profile</a></div><input name="sr" value="'+self.community+'"><textarea name="title"></textarea><textarea name="text"></textarea><button onclick="send()">Post</button><script>async function send(){const r=await fetch("/fixture-submit",{method:"POST",body:JSON.stringify({title:document.querySelector("[name=title]").value,body:document.querySelector("[name=text]").value})});location.href=(await r.json()).url}</script>'
            route.fulfill(status=200,content_type='text/html',body='<!doctype html>'+body)
        elif route.request.url==self.url and self.submitted:
            content='<shreddit-post id="t3_post123" author="'+self.author+'"><h1>'+html.escape(self.submitted['title'])+'</h1><div slot="text-body" style="white-space:pre-wrap">'+html.escape(self.submitted['body'])+'</div></shreddit-post>'
            route.fulfill(status=200,content_type='text/html',body='<!doctype html>'+content)
        else:route.abort()
    def test_approved_post_sent_once_and_reloaded_to_verify(self):
        url=self.reader.publish(self.job,self.flow);self.flow.publication_done(self.job,url,self.config)
        self.assertEqual(self.submits,1);self.assertEqual(self.submitted['title'],self.result['title'])
        self.assertEqual(self.store.items()[0]['status'],'published');self.assertIsNone(self.flow.claim_publication(self.config))
    def test_unverified_community_prevents_submission(self):
        self.community='different'
        with self.assertRaisesRegex(PilotError,'community'):self.reader.publish(self.job,self.flow)
        self.assertEqual(self.submits,0)
    def test_captcha_before_editor_preserves_zero_submissions(self):
        self.challenge=True
        with self.assertRaises(BrowserAttention):self.reader.publish(self.job,self.flow)
        self.assertEqual(self.submits,0);self.assertTrue(self.reader.handoff_pending)
    def test_wrong_author_readback_stays_uncertain_without_retry(self):
        self.author='wrong_user'
        with self.assertRaisesRegex(PilotError,'expected author'):self.reader.publish(self.job,self.flow)
        self.flow.publication_failed(self.job['item_id'],'Readback mismatch')
        self.assertEqual(self.submits,1);self.assertEqual(self.flow.outbox()[0]['state'],'uncertain')
        self.assertIsNone(self.flow.claim_publication(self.config))
    def test_submit_composer_can_be_checked_after_manual_verification(self):
        self.page.goto(self.post['url']);self.reader.handoff_pending=True
        self.reader.verify_attention({'backend':'chrome','profile_id':'','expected_account':'pilot_test','url':self.post['url']})
        self.assertFalse(self.reader.handoff_pending);self.assertEqual(self.submits,0)

if __name__=='__main__':unittest.main()
