"""Real Chrome with intercepted pages; never publishes to external services."""
import html
import json
import os
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import Mock
from test_conversations import setup_case, ROOT
from pilot.browser import BrowserReader
from pilot.core import PilotError, validate_post
from pilot.conversations import target_post
from pilot.workflow import draft_hash
from pilot.service import server_for


@unittest.skipUnless(os.environ.get('PILOT_BROWSER_TESTS')=='1','Enable intercepted Chrome tests')
class GrowthBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw=sync_playwright().start();cls.chrome=cls.pw.chromium.launch(channel='chrome',headless=True)
    @classmethod
    def tearDownClass(cls):cls.chrome.close();cls.pw.stop()
    def setUp(self):
        setup_case(self)
        self.ctx=self.chrome.new_context();self.addCleanup(self.ctx.close)
        self.page=self.ctx.new_page();self.page.set_default_timeout(10000)
        self.reader=BrowserReader.__new__(BrowserReader);self.reader.page=self.page;self.reader.config=self.config;self.reader.workflow=self.flow
        self.reader.root=Path(self.tmp.name);self.sent='';self.clicks=0;self.trap=0;self.identity='pilot_test';self.persist=True
        self.ctx.route('**/*',self.route)

    def route(self,route):
        if route.request.url.endswith('/fixture-send'):
            self.clicks+=1
            if self.persist:self.sent=json.loads(route.request.post_data)['draft']
            route.fulfill(status=200,content_type='application/json',body='{}');return
        if route.request.url.endswith('/trap'):
            self.trap+=1;route.fulfill(status=200,body='');return
        comments=[]
        for c in self.data['comments']:
            composer=''
            if c['id']==self.target['id']:
                composer='''<button onclick="this.nextElementSibling.hidden=false;this.hidden=true">Reply</button><form class="usertext" hidden onsubmit="send(event)"><textarea name="text"></textarea><button type="submit">Reply</button></form>'''
            comments.append('<shreddit-comment thingid="'+c['id']+'" created-timestamp="'+c.get('created','')+'" parentid="'+c['parent_id']+'" author="'+c['author']+'" permalink="'+c['url']+'"><div slot="comment" style="white-space:pre-wrap">'+html.escape(c['body'])+'</div>'+composer+'</shreddit-comment>')
        if self.sent:comments.append('<shreddit-comment thingid="t1_sent" parentid="t1_incoming" author="pilot_test" permalink="'+self.rootpost['url']+'sent/"><div slot="comment" style="white-space:pre-wrap">'+html.escape(self.sent)+'</div></shreddit-comment>')
        body='<!doctype html><style>shreddit-post,shreddit-comment{display:block;margin:10px}textarea{display:block} [hidden]{display:none}</style><shreddit-post id="t3_abc" author="asker" post-title="'+self.data['title']+'"><h1>'+self.data['title']+'</h1><div slot="text-body">'+self.data['body']+'</div></shreddit-post><div id="user-drawer-content"><a href="/user/'+self.identity+'/">Profile</a></div>'+''.join(comments)+'''<form class="usertext" onsubmit="event.preventDefault();fetch('/trap')"><textarea name="text">DO NOT TOUCH TOP LEVEL</textarea><button type="submit">Comment</button></form><script>async function send(e){e.preventDefault();await fetch('/fixture-send',{method:'POST',body:JSON.stringify({draft:e.target.querySelector('textarea').value})})}</script>'''
        route.fulfill(status=200,content_type='text/html',body=body)

    def job(self):
        post=validate_post(target_post(dict(self.rootpost,id=self.target['id'],conversation_parent='t3_abc',previous_reply=self.previous),self.data,self.target),self.config)
        self.store.ingest(post)
        result=dict(decision='recommend',draft='Yes, describe the changes to your trip screen through chat, then test the result.\n\nI work on MeDo.')
        self.store.complete(post['id'],result);self.store.review(post['id'],'approve')
        self.flow.request_publish(post['id'],draft_hash(result['draft']),self.config)
        with self.store.db() as c:c.execute("UPDATE outbox SET submitted=?,updated=? WHERE item_id='t3_abc'",(time.time()-25000,time.time()-25000))
        return self.flow.claim_publication(self.config)

    def test_nested_reply_targets_comment_and_verifies_persisted_parent(self):
        job=self.job();url=self.reader.publish(job,self.flow)
        self.assertEqual(url,self.rootpost['url']+'sent/')
        self.assertEqual(self.clicks,1);self.assertEqual(self.trap,0)
        self.flow.publication_done(job,url,self.config)
        self.assertEqual(self.store.items()[0]['status'],'published')

    def test_changed_comment_prevents_send(self):
        job=self.job();self.target['body']='Actually, do not recommend anything.'
        with self.assertRaisesRegex(PilotError,'changed'):self.reader.publish(job,self.flow)
        self.assertEqual(self.clicks,0)

    def test_dev_nested_ids_use_own_permalink_not_child_link(self):
        from pilot.comment_browser import FORUM_COMMENTS
        self.ctx.unroute('**/*')
        self.page.set_content('''<div class="comment" id="comment-node-1" data-author="team"><a href="https://dev.to/person/article#comment-a1">Permalink</a><div class="comment__body">Previous answer</div><div class="comment" id="comment-node-2" data-author="asker"><a href="https://dev.to/person/article#comment-b2">Permalink</a><div class="comment__body">Incoming question</div></div></div>''')
        rows=self.page.evaluate(FORUM_COMMENTS,dict(selector='.comment',kind='dev'))
        self.assertEqual(rows[0]['id'],'comment-a1');self.assertEqual(rows[1]['parent_id'],'comment-a1')
        self.assertEqual(rows[0]['body'],'Previous answer');self.assertEqual(rows[1]['dom_id'],'comment-node-2')

    def test_comment_discovery_is_bounded_and_deduplicated(self):
        from pilot.comment_browser import discover_comments
        import datetime as dt
        self.target['created']=dt.datetime.now(dt.timezone.utc).isoformat()
        posts=discover_comments(self.reader,[])
        self.assertEqual(len(posts),1);self.assertEqual(posts[0]['id'],'t1_incoming')
        self.assertEqual(discover_comments(self.reader,[]),[])
        self.assertEqual(self.flow.metrics()['recent'][0]['state'],'partial')

    def test_uncertain_send_is_never_repeated_after_recovery(self):
        job=self.job();self.persist=False
        with self.assertRaisesRegex(PilotError,'uncertain'):self.reader.publish(job,self.flow)
        self.flow.publication_failed(job['item_id'],'Uncertain test');self.flow.recover()
        self.assertEqual(self.clicks,1);self.assertIsNone(self.flow.claim_publication(self.config))
        self.assertEqual(self.store.setting('paused'),'1')

    def dashboard(self,read_only=False):
        self.ctx.unroute('**/*')
        config=dict(self.config)
        with socket.socket() as s:s.bind(('127.0.0.1',0));config['port']=s.getsockname()[1]
        server=server_for(ROOT,config,self.store,Mock(),read_only=read_only)
        server.daemon_threads=False
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():self.page.close();server.shutdown();server.server_close();thread.join(5)
        self.addCleanup(close)
        self.page.goto('http://127.0.0.1:'+str(config['port']))
        self.page.locator('#last-sync').filter(has_text='Updated').wait_for()

    def test_dashboard_search_details_and_mobile_layout(self):
        self.job();self.dashboard()
        self.page.locator('nav button[data-view="conversations"]').click()
        self.page.locator('#search').fill('trip screen')
        self.assertEqual(self.page.locator('#items tbody tr').count(),1)
        self.page.locator('#items td.title button').click()
        self.assertTrue(self.page.get_by_role('heading',name='Previous MeDo response').is_visible())
        self.assertTrue(self.page.get_by_role('heading',name='Exact reply / post').is_visible())
        self.page.locator('#close-detail').click()
        self.page.set_viewport_size({'width':390,'height':844})
        self.page.locator('nav button[data-view="overview"]').click()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        out=os.environ.get('PILOT_SCREENSHOT_DIR')
        if out:
            Path(out).mkdir(parents=True,exist_ok=True)
            self.page.screenshot(path=str(Path(out)/'dashboard-mobile.png'),full_page=True)
            self.page.set_viewport_size({'width':1440,'height':1050})
            self.page.screenshot(path=str(Path(out)/'dashboard-overview.png'),full_page=True)

    def test_readonly_dashboard_no_mutations_and_error_leaves_stale_warning(self):
        self.dashboard(True)
        self.assertFalse(self.page.locator('#pause').is_visible())
        self.page.locator('nav button[data-view="platforms"]').click()
        self.assertEqual(self.page.locator('#channels button').count(),0)
        self.page.route('**/api/overview',lambda r:r.fulfill(status=500,content_type='application/json',body='{"error":"Unavailable"}'))
        self.page.locator('#refresh').click()
        self.page.locator('#error').wait_for(state='visible')
        self.assertIn('stale',self.page.locator('#error').inner_text())

if __name__=='__main__':unittest.main()
