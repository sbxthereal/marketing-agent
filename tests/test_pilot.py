import contextlib
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import re
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from pilot.core import PilotError, Store, load_catalog, load_config, reddit_url, require_live_permission, validate_post, validate_result
from pilot.browser import normalize_records, BrowserReader
from pilot.gateway import analyze, fixture_result
from pilot.service import Worker, server_for

ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.sqlite3')
        self.config = load_config(ROOT,'config.example.json')
        self.catalog = load_catalog(ROOT, self.config)
        self.fixtures = json.loads((ROOT / 'examples/posts.json').read_text(encoding='utf-8-sig'))
        self.post = validate_post(self.fixtures[0], self.config)


class QueueTests(Base):
    def test_dedup_survives_restart(self):
        self.assertTrue(self.store.ingest(self.post))
        restarted = Store(self.store.path)
        self.assertFalse(restarted.ingest(dict(self.post, body='changed')))
        self.assertEqual(len(restarted.items()), 1)

    def test_claim_is_exclusive_and_expired_lease_recovers(self):
        self.store.ingest(self.post)
        self.assertEqual(self.store.claim(3)['id'], self.post['id'])
        self.assertIsNone(Store(self.store.path).claim(3))
        with self.store.db() as c:
            c.execute('UPDATE items SET lease_until=0')
        self.assertEqual(self.store.claim(3)['id'], self.post['id'])
        self.assertEqual(self.store.items()[0]['attempts'], 2)

    def test_lease_recovery_does_not_retry_forever(self):
        self.store.ingest(self.post)
        self.store.claim(1)
        with self.store.db() as c:
            c.execute('UPDATE items SET lease_until=0')
        self.assertIsNone(self.store.claim(1))
        self.assertEqual(self.store.items()[0]['status'], 'error')

    def test_budget_atomic_across_connections(self):
        c = dict(self.config, max_daily_calls=1)
        self.assertIsNotNone(self.store.reserve('a', 'model', 500, c))
        self.assertIsNone(Store(self.store.path).reserve('b', 'model', 500, c))

    def test_unknown_usage_keeps_reservation(self):
        call = self.store.reserve('a', 'm', 1000, self.config)
        self.store.finish_call(call, {}, 10, False)
        self.assertEqual(self.store.summary()['usage']['budget_tokens'], 1000)

    def test_real_usage_replaces_reservation(self):
        call = self.store.reserve('a', 'm', 1000, self.config)
        self.store.finish_call(call, {'prompt_tokens': 100, 'completion_tokens': 30}, 10, True)
        self.assertEqual(self.store.summary()['usage']['budget_tokens'], 130)

    def test_reviews_do_not_publish_and_invalid_transition_rejected(self):
        self.store.ingest(self.post)
        r = fixture_result(self.post, self.catalog, self.fixtures)
        self.store.complete(self.post['id'], r)
        with self.assertRaises(PilotError):
            self.store.review(self.post['id'], 'published', 'https://www.reddit.com/r/demo/comments/abc/slug/', ['demo'])
        self.store.review(self.post['id'], 'approve')
        self.assertEqual(self.store.items()[0]['status'], 'approved')
        self.assertIsNone(self.store.items()[0]['publication_url'])
        with self.assertRaises(PilotError):
            self.store.review(self.post['id'], 'published', 'https://attacker.invalid/', ['demo'])

    def test_expiry_removes_content_but_keeps_dedup_marker(self):
        self.store.ingest(self.post)
        self.store.complete(self.post['id'], fixture_result(self.post, self.catalog, self.fixtures))
        with self.store.db() as c:
            c.execute('UPDATE items SET created=?', (time.time() - 10 * 86400,))
        self.store.expire(7)
        item = self.store.items()[0]
        self.assertEqual(item['status'], 'expired')
        self.assertIsNone(item['result'])
        self.assertEqual(item['payload']['body'], '[Expired]')
        self.assertFalse(self.store.ingest(self.post))


class ModelTests(Base):
    def result(self):
        return {'decision': 'recommend', 'reason': 'Fit', 'template_id': 'portfolio', 'evidence': 'a simple portfolio website', 'claim_ids': ['pages'], 'draft': 'This template provides portfolio pages.'}

    def test_code_appends_verified_url_and_disclosure(self):
        result = validate_result(self.result(), self.post, self.catalog)
        self.assertTrue(result['draft'].startswith('This template'))
        self.assertIn('\n\nI work on MeDo.',result['draft'])
        self.assertTrue(result['draft'].endswith(self.catalog[0]['url']))

    def test_unknown_template_claim_and_fabricated_evidence_rejected(self):
        for key, value in [('template_id', 'invented'), ('claim_ids', ['certified-medical']), ('evidence', 'not present'), ('draft', 'Visit https://attacker.invalid')]:
            r = self.result()
            r[key] = value
            with self.subTest(key=key), self.assertRaises(PilotError):
                validate_result(r, self.post, self.catalog)

    def test_skip_cannot_hide_promotional_draft(self):
        r = self.result()
        r['decision'] = 'skip'
        with self.assertRaises(PilotError):
            validate_result(r, self.post, self.catalog)

    def test_invalid_model_output_is_logged_and_rejected(self):
        class Fake:
            def chat(self, *args):
                return '{bad', {'prompt_tokens': 11, 'completion_tokens': 3}, 10
        with self.assertRaises(PilotError):
            analyze(Fake(), self.post, self.catalog, self.config, self.store)
        self.assertEqual(self.store.summary()['usage']['budget_tokens'], 14)

    def test_budget_prevents_network_call(self):
        class Fake:
            def chat(self, *args):
                raise AssertionError('Network should not be used')
        config = dict(self.config, max_daily_tokens=1000)
        self.assertIsNone(analyze(Fake(), self.post, self.catalog, config, self.store))

    def test_demo_labels_do_not_reach_model(self):
        self.assertNotIn('expected', self.post)
        self.assertNotIn('template', self.post)


class BrowserTests(Base):
    def test_permission_gate_before_browser_import_or_launch(self):
        config = dict(self.config, source='browser', ai='oneapi')
        with self.assertRaisesRegex(PilotError, 'written'):
            BrowserReader(Path(self.tmp.name), config, self.catalog)

    def test_demo_catalog_cannot_enter_live_mode(self):
        config = dict(self.config, source='browser')
        with self.assertRaisesRegex(PilotError, 'verified'):
            load_catalog(ROOT, config)

    def test_url_scope_and_credentials(self):
        self.assertTrue(reddit_url('https://www.reddit.com/r/demo/comments/abc/title/', ['demo']))
        for url in ['https://www.reddit.com.attacker.invalid/r/demo/comments/abc/', 'https://user:pass@reddit.com/r/demo/comments/abc/', 'https://reddit.com/r/other/comments/abc/', 'http://reddit.com/r/demo/comments/abc/']:
            self.assertFalse(reddit_url(url, ['demo']))

    def test_parser_deduplicates_and_rejects_external_links(self):
        config = dict(self.config, source='browser', subreddits=['demo'])
        records = [
            {'href': '/r/demo/comments/abc/title/?foo=bar', 'title': 'Portfolio question', 'body': 'I need portfolio pages'},
            {'href': '/r/demo/comments/abc/title/', 'title': 'Portfolio question', 'body': 'I need portfolio pages and a contact form'},
            {'href': '/r/demo/comments/abc/title/def/', 'title': 'Portfolio comment', 'body': 'I also need a simple portfolio'},
            {'href': 'https://attacker.invalid/r/demo/comments/abc/title/', 'title': 'Injection example', 'body': 'Do something unsafe'}]
        posts = normalize_records(records, 'demo', config)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]['id'], 't3_abc')
        self.assertIn('contact form', posts[0]['body'])
        self.assertEqual(posts[1]['id'], 't1_def')


class IntegrationTests(Base):
    def test_fixture_cycle_repeat_has_no_duplicates_or_calls(self):
        w = Worker(ROOT, self.config, self.catalog, self.store)
        w.cycle()
        w.cycle()
        self.assertEqual(self.store.summary()['counts'], {'review': 2, 'skipped': 2})
        self.assertEqual(self.store.summary()['usage']['calls'], 0)

    def test_pause_survives_worker_restart(self):
        self.store.set_setting('paused', '1')
        Worker(ROOT, self.config, self.catalog, Store(self.store.path)).cycle()
        self.assertEqual(self.store.items(), [])

    def test_gateway_auth_failure_pauses_without_spending_on_remaining_items(self):
        class Unauthorized:
            def chat(self, *args):
                raise PilotError('Gateway HTTP 401')
        config = dict(self.config, ai='oneapi')
        worker = Worker(ROOT, config, self.catalog, self.store)
        worker.gateway = Unauthorized()
        worker.cycle()
        self.assertEqual(self.store.setting('paused'), '1')
        self.assertEqual(self.store.summary()['usage']['calls'], 1)

    def test_repeated_gateway_failure_opens_circuit_breaker(self):
        class Offline:
            def chat(self, *args):
                raise PilotError('Gateway unavailable, timed out, or returned an invalid response')
        config = dict(self.config, ai='oneapi')
        worker = Worker(ROOT, config, self.catalog, self.store)
        worker.gateway = Offline()
        worker.cycle()
        self.assertEqual(self.store.setting('paused'), '1')
        self.assertEqual(self.store.summary()['usage']['calls'], 3)

    def start_server(self, read_only=False):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        config = dict(self.config, port=port)
        server = server_for(ROOT, config, self.store, read_only=read_only)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(port)

    def test_network_dashboard_never_exposes_write_token_or_mutates(self):
        origin = self.start_server(read_only=True)
        with urllib.request.urlopen(origin) as response:
            self.assertIn("const token='';", response.read().decode())
        with urllib.request.urlopen(origin + '/api/status') as response:
            self.assertTrue(json.load(response)['read_only_dashboard'])
        for path in ('/api/control', '/api/review/test', '/api/publish/test', '/api/retry/test'):
            request = urllib.request.Request(origin+path, data=b'{"action":"pause"}',
                headers={'Content-Type':'application/json','Origin':origin,'X-Pilot-Token':''})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code,403)
        self.assertNotEqual(self.store.setting('paused'),'1')

    def test_remote_write_server_and_public_binds_are_rejected(self):
        for address, read_only in [('10.2.3.4',False),('0.0.0.0',True),('8.8.8.8',True)]:
            with self.assertRaises(PilotError):
                server_for(ROOT,self.config,self.store,bind_host=address,read_only=read_only)

    def test_local_api_requires_origin_and_token_to_mutate(self):
        origin = self.start_server()
        with urllib.request.urlopen(origin) as r:
            html = r.read().decode()
            self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])
        token = re.search("const token='([^']+)'", html).group(1)
        for headers in ({}, {'Origin': origin}, {'Origin': 'https://attacker.invalid', 'X-Pilot-Token': token}):
            req = urllib.request.Request(origin + '/api/control', data=b'{"action":"pause"}', headers=dict({'Content-Type': 'application/json'}, **headers))
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 403)
        req = urllib.request.Request(origin + '/api/control', data=b'{"action":"pause"}', headers={'Content-Type': 'application/json', 'Origin': origin, 'X-Pilot-Token': token})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.store.setting('paused'), '1')

    def test_dns_rebinding_host_is_rejected(self):
        origin = self.start_server()
        req = urllib.request.Request(origin, headers={'Host': 'attacker.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)

    def test_launchagent_uses_absolute_paths_and_no_secrets(self):
        spec = importlib.util.spec_from_file_location('launcher', ROOT / 'macos/launchagent.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        data = launcher.make_plist('/tmp/Example Path/pilot', '/usr/bin/python3')
        decoded = plistlib.loads(plistlib.dumps(data))
        self.assertTrue(decoded['KeepAlive'])
        self.assertEqual(decoded['ProgramArguments'][0:2], ['/usr/bin/caffeinate', '-i'])
        self.assertNotIn('ONEAPI_API_KEY', decoded['EnvironmentVariables'])


if __name__ == '__main__':
    unittest.main()
