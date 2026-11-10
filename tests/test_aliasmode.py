import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from pilot.aliasmode import local_cdp_address, open_profile, request, stop_profile
from pilot.core import PilotError, publisher_for
from pilot.ixbrowser import ProfilePool, validate_profiles
from pilot.workflow import require_publishing


def configuration():
    return {'source':'browser', 'ai':'oneapi', 'browser_backend':'aliasmode',
        'subreddits':['nocode','webdev'],
        'permissions':{'browser_collection':True,'ai_processing':True,'community_rules_reviewed':True,'reference':'fixture'},
        'publishing':{'enabled':True,'permission_confirmed':True,'permission_reference':'fixture'},
        'aliasmode':{'port':50400, 'profiles':[
            {'id':'profile-one','reddit_username':'medo_one','subreddits':['nocode']},
            {'id':'profile-two','reddit_username':'medo_two','subreddits':['webdev']}]}}


class AliasModeTests(unittest.TestCase):
    def test_account_binding_and_community_ownership(self):
        config = configuration()
        validate_profiles(config)
        require_publishing(config)
        self.assertEqual(publisher_for(config, 'WEBDEV'), 'medo_two')
        for field, value in [('id','profile-one'), ('id',42), ('id','a&user_id=b'),
                             ('reddit_username','MEDO_ONE'), ('subreddits',['nocode'])]:
            bad = copy.deepcopy(config)
            bad['aliasmode']['profiles'][1][field] = value
            with self.subTest(field=field,value=value), self.assertRaises(PilotError):
                validate_profiles(bad)

    def test_cdp_response_cannot_control_remote_browser(self):
        self.assertEqual(local_cdp_address('ws://localhost:12345/devtools/browser/abc-123'),
                         'ws://127.0.0.1:12345/devtools/browser/abc-123')
        for value in [None, 'ws://10.0.0.2:12345/devtools/browser/abc',
                      'ws://127.0.0.1:12345/devtools/browser/abc?token=secret',
                      'ws://user@127.0.0.1:12345/devtools/browser/abc',
                      'ws://127.0.0.1:12345/devtools/browser/abc#other',
                      'ws://127.0.0.1:80/devtools/browser/abc',
                      'http://127.0.0.1:12345', 'ws://127.0.0.1:12345/other']:
            with self.subTest(value=value), self.assertRaises(PilotError):
                local_cdp_address(value)

    def test_pool_uses_fixed_aliasmode_profile_and_shared_limits(self):
        flow = object()
        calls = []
        class Reader:
            def __init__(self, root, config, catalog, workflow):
                self.config, self.workflow = config, workflow
                calls.append(self)
            def collect(self):
                return [{'subreddit':self.config['subreddits'][0]}]
            def publish(self, job, workflow):
                return self.config['publishing']['reddit_username']
            def close(self):
                pass
        with patch('pilot.browser.BrowserReader', Reader):
            pool = ProfilePool(Path('.'),configuration(),[{'verified':True}],flow)
            self.assertEqual(len(pool.collect()),2)
            self.assertEqual([r.config['aliasmode_profile_id'] for r in calls],['profile-one','profile-two'])
            self.assertTrue(all(r.workflow is flow for r in calls))
            self.assertEqual(pool.publish({'post':{'subreddit':'webdev'}},flow),'medo_two')
            self.assertNotEqual(calls[0].config['search_state_suffix'],calls[1].config['search_state_suffix'])
            first, second = Mock(), Mock()
            first.publish.side_effect = PilotError('account needs attention')
            pool.readers = {'profile-one':first,'profile-two':second}
            with self.assertRaises(PilotError):
                pool.publish({'post':{'subreddit':'nocode'}},flow)
            second.publish.assert_not_called()

    def test_permissions_checked_before_browser_control(self):
        config = configuration()
        config['permissions']['browser_collection'] = False
        with self.assertRaises(PilotError):
            ProfilePool(Path('.'),config,[{'verified':True}],object())

    def test_attention_check_is_bound_to_original_profile_and_account(self):
        pool=ProfilePool(Path('.'),configuration(),[{'verified':True}],object())
        first,second=Mock(),Mock();pool.readers={'profile-one':first,'profile-two':second}
        alert={'backend':'aliasmode','profile_id':'profile-one','expected_account':'medo_one'}
        pool.verify_attention(alert)
        first.verify_attention.assert_called_once_with(alert);second.verify_attention.assert_not_called()
        for changes in ({'profile_id':'missing'},{'expected_account':'medo_two'},{'backend':'chrome'}):
            with self.assertRaises(PilotError):pool.verify_attention(dict(alert,**changes))
        second.verify_attention.assert_not_called()

    def test_documented_wire_protocol_and_http_200_errors(self):
        state = {'reply':{'code':0,'data':{'ws':{'puppeteer':'ws://127.0.0.1:54321/devtools/browser/fixture'}}},
                 'paths':[], 'redirect':False}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state['paths'].append(self.path)
                if state['redirect']:
                    self.send_response(302)
                    self.send_header('Location','http://127.0.0.1:1/never-follow')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(state['reply']).encode())
            def log_message(self,*args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        port = server.server_port
        try:
            with patch.dict('os.environ',{'HTTP_PROXY':'http://127.0.0.1:1','NO_PROXY':''}):
                self.assertEqual(open_profile(port,'profile-one'),'ws://127.0.0.1:54321/devtools/browser/fixture')
            self.assertEqual(state['paths'][-1],'/api/v1/browser/start?user_id=profile-one')
            state['reply'] = {'code':0,'data':{}}
            stop_profile(port,'profile-one')
            self.assertEqual(state['paths'][-1],'/api/v1/browser/stop?user_id=profile-one')
            for reply in ({'code':-1,'msg':'private account detail'}, {'code':False,'data':{}}, [], {}):
                state['reply'] = reply
                with self.assertRaises(PilotError) as error:
                    request(port,'/api/v1/status')
                self.assertNotIn('private account detail',str(error.exception))
            state['redirect'] = True
            with self.assertRaises(PilotError):
                request(port,'/api/v1/status')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_malformed_open_result_and_unsafe_id_stop_before_attach(self):
        with patch('pilot.aliasmode.request',return_value={}) as api:
            with self.assertRaises(PilotError):
                open_profile(50400,'profile-one')
            api.reset_mock()
            with self.assertRaises(PilotError):
                open_profile(50400,'one&user_id=two')
            api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
