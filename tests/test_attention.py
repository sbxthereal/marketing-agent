"""Human handoff persistence, worker recovery and authenticated dashboard control."""
import json
from pathlib import Path
import re
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock

from pilot.attention import Attention
from pilot.browser import BrowserAttention, public_page_url
from pilot.core import PilotError, Store, load_catalog, load_config
from pilot.service import Worker, server_for
from pilot.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]
DETAILS = {'kind':'captcha','backend':'chrome','profile_id':'','profile_label':'Dedicated Chrome profile',
           'expected_account':'','url':'https://www.reddit.com/r/nocode/new/'}

class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'state.db')
        self.flow = Workflow(self.store)
        self.attention = Attention(self.store)
        self.config = load_config(ROOT, 'config.browser.example.json')
        self.config['permissions'] = {k:True for k in ('browser_collection','ai_processing','community_rules_reviewed')}
        self.config['permissions']['reference'] = 'LOCAL FIXTURE TEST ONLY'
        self.catalog = load_catalog(ROOT, self.config)

    def blocked(self):
        return self.attention.record(DETAILS, 'Human verification required')

    def checking(self):
        a = self.blocked(); self.attention.request_check(a['id'])
        return self.attention.current()

    def test_persists_pause_and_does_not_resume_after_restart(self):
        a = self.checking()
        restarted = Worker(ROOT, self.config, self.catalog, Store(self.store.path))
        restarted.browser = Mock()
        restarted.cycle()
        self.assertEqual(restarted.attention.current()['id'], a['id'])
        self.assertEqual(restarted.attention.current()['state'], 'blocked')
        self.assertEqual(self.store.setting('paused'), '1')
        restarted.browser.verify_attention.assert_not_called()

    def test_failed_check_stays_paused_without_model_or_collection(self):
        worker = Worker(ROOT, self.config, self.catalog, self.store)
        a = self.checking(); worker.browser = Mock()
        worker.browser.verify_attention.side_effect = BrowserAttention('Still challenged', DETAILS)
        worker.cycle(); worker.cycle()
        self.assertEqual(self.attention.current()['state'], 'blocked')
        self.assertEqual(self.attention.current()['message'], 'Still challenged')
        worker.browser.verify_attention.assert_called_once()
        worker.browser.collect.assert_not_called()
        self.assertEqual(self.store.summary()['usage']['calls'], 0)
        self.assertEqual(self.store.setting('paused'), '1')

    def test_successful_check_resumes_but_does_not_requeue_blocked_publication(self):
        worker = Worker(ROOT, self.config, self.catalog, self.store)
        a = self.checking(); worker.browser = Mock()
        with self.store.db() as c:
            c.execute("INSERT INTO outbox(item_id,draft_hash,draft,state,requested,updated) VALUES('x','h','draft','blocked',0,0)")
        worker.cycle()
        self.assertIsNone(self.attention.current())
        self.assertEqual(self.store.setting('paused'), '0')
        self.assertEqual(self.flow.outbox()[0]['state'], 'blocked')
        worker.browser.publish.assert_not_called()
        worker.browser.collect.assert_not_called()

    def test_pause_and_new_check_invalidates_old_result(self):
        first = self.checking()
        self.attention.pause()
        self.attention.request_check(first['id'])
        second = self.attention.current()
        self.assertFalse(self.attention.finish(first['id'], first['check_id']))
        self.assertEqual(self.store.setting('paused'), '1')
        self.assertTrue(self.attention.finish(second['id'], second['check_id']))

    def test_uncertain_reply_blocks_both_resume_paths(self):
        a = self.checking()
        with self.store.db() as c:
            c.execute("INSERT INTO outbox(item_id,draft_hash,draft,state,requested,updated) VALUES('x','h','draft','uncertain',0,0)")
        self.assertFalse(self.attention.finish(a['id'], a['check_id']))
        self.assertIn('uncertain', self.attention.current()['message'])
        with self.assertRaises(PilotError): self.attention.resume()
        self.assertEqual(self.store.setting('paused'), '1')

    def test_permission_gate_is_rechecked(self):
        self.config['permissions']['ai_processing'] = False
        worker = Worker(ROOT, self.config, self.catalog, self.store)
        self.checking(); worker.browser = Mock()
        worker.cycle()
        worker.browser.verify_attention.assert_not_called()
        self.assertEqual(self.attention.current()['state'], 'blocked')
        self.assertEqual(self.store.setting('paused'), '1')

    def test_browser_challenge_releases_item_without_consuming_an_attempt(self):
        worker = Worker(ROOT, self.config, self.catalog, self.store)
        worker.browser = Mock(); worker.gateway = Mock()
        post = {'id':'t3_abc','kind':'post','subreddit':'nocode','url':'https://www.reddit.com/r/nocode/comments/abc/help/',
                'title':'Need a portfolio website','body':'I need a portfolio for my design projects.'}
        worker.browser.collect.return_value = [post]
        worker.browser.enrich.side_effect = BrowserAttention('Human verification required', DETAILS)
        worker.cycle()
        row = self.store.items()[0]
        self.assertEqual((row['status'], row['attempts']), ('queued', 0))
        self.assertIsNotNone(self.attention.current())
        worker.gateway.chat.assert_not_called()

    def test_alert_url_excludes_tokens_and_non_reddit_destinations(self):
        self.assertEqual(public_page_url(DETAILS['url']+'?token=secret#secret'), DETAILS['url'])
        self.assertEqual(public_page_url('https://secret:password@reddit.com/r/nocode/'), '')
        self.assertEqual(public_page_url('https://attacker.invalid/r/nocode/'), '')

    def start_server(self, read_only=False):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        worker = Mock()
        server = server_for(ROOT, dict(self.config,port=port), self.store, worker, read_only=read_only)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        origin = 'http://127.0.0.1:'+str(port)
        with urllib.request.urlopen(origin) as r: html = r.read().decode()
        token = re.search("const token='([^']*)'",html).group(1)
        return origin, token, worker

    def post(self, origin, token, path, payload):
        request = urllib.request.Request(origin+path, data=json.dumps(payload).encode(),
            headers={'Content-Type':'application/json','Origin':origin,'X-Pilot-Token':token})
        with urllib.request.urlopen(request) as r: return json.load(r)

    def test_local_request_is_queued_and_ordinary_resume_cannot_bypass(self):
        a = self.blocked(); origin, token, worker = self.start_server()
        for bad_token in ('', 'wrong'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(origin,bad_token,'/api/attention/check',{'id':a['id']})
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(origin,token,'/api/control',{'action':'resume'})
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError):
            self.post(origin,token,'/api/attention/check',{'id':'stale'})
        self.post(origin,token,'/api/attention/check',{'id':a['id']})
        self.assertEqual(self.attention.current()['state'],'checking')
        worker.wake.set.assert_called_once()
        worker.browser.verify_attention.assert_not_called()
        self.assertEqual(self.store.setting('paused'),'1')

    def test_company_dashboard_displays_alert_but_rejects_resume(self):
        a = self.blocked(); origin, token, worker = self.start_server(True)
        self.assertEqual(token,'')
        with urllib.request.urlopen(origin+'/api/status') as r:
            self.assertEqual(json.load(r)['browser_attention']['id'],a['id'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post(origin,token,'/api/attention/check',{'id':a['id']})
        self.assertEqual(error.exception.code,403)
        self.assertEqual(self.attention.current()['state'],'blocked')

if __name__ == '__main__': unittest.main()
