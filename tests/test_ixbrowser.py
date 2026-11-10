import copy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from pilot.core import PilotError
from pilot.browser import BrowserReader
from pilot.ixbrowser import ProfilePool, local_debug_address, validate_profiles
from pilot.workflow import require_publishing


def configuration():
    return {'source':'browser','ai':'oneapi','browser_backend':'ixbrowser',
        'subreddits':['nocode','webdev'],
        'permissions':{'browser_collection':True,'ai_processing':True,'community_rules_reviewed':True,'reference':'test permission'},
        'publishing':{'enabled':True,'permission_confirmed':True,'permission_reference':'test'},
        'ixbrowser':{'port':53200,'profiles':[
            {'id':1,'reddit_username':'medo_one','subreddits':['nocode']},
            {'id':2,'reddit_username':'medo_two','subreddits':['webdev']}]}}


class ProfileTests(unittest.TestCase):
    def test_each_community_has_exactly_one_fixed_profile(self):
        config=configuration();validate_profiles(config);require_publishing(config)
        for change in ('duplicate_community','missing_community','duplicate_account','duplicate_id'):
            bad=copy.deepcopy(config)
            second=bad['ixbrowser']['profiles'][1]
            if change=='duplicate_community': second['subreddits']=['nocode']
            if change=='missing_community': second['subreddits']=['other']
            if change=='duplicate_account': second['reddit_username']='MEDO_ONE'
            if change=='duplicate_id': second['id']=1
            with self.subTest(change=change), self.assertRaises(PilotError): validate_profiles(bad)

    def test_remote_debugging_cannot_escape_loopback(self):
        self.assertEqual(local_debug_address('localhost:4567'),'http://127.0.0.1:4567')
        self.assertEqual(local_debug_address('http://127.0.0.1:4567/'),'http://127.0.0.1:4567')
        for address in ('http://10.0.0.1:4567','http://127.0.0.1:4567/other','http://user@127.0.0.1:4567',
                        'http://127.0.0.1:4567?x=1','https://127.0.0.1:4567','127.0.0.1:bad',None):
            with self.subTest(address=address), self.assertRaises(PilotError): local_debug_address(address)

    def test_profiles_share_workflow_and_route_publication_to_expected_account(self):
        config=configuration();flow=object();calls=[]
        class Reader:
            def __init__(self, root, child, catalog, workflow):
                self.config=child;self.workflow=workflow;calls.append(self)
            def collect(self):return [{'subreddit':self.config['subreddits'][0]}]
            def publish(self,job,workflow):
                assert workflow is flow
                return self.config['publishing']['reddit_username']
            def close(self):pass
        with patch('pilot.browser.BrowserReader',Reader):
            pool=ProfilePool(Path('.'),config,[{'verified':True}],flow)
            self.assertEqual(len(pool.collect()),2)
            self.assertTrue(all(reader.workflow is flow for reader in calls))
            self.assertEqual(pool.publish({'post':{'subreddit':'WEBDEV'}},flow),'medo_two')
            self.assertEqual(len(calls),2)
            self.assertNotEqual(calls[0].config['search_state_suffix'],calls[1].config['search_state_suffix'])
            self.assertNotIn('reddit_username',config['publishing'])
            pool.close()

    def test_profile_failure_does_not_fall_back_to_other_account(self):
        pool=ProfilePool(Path('.'),configuration(),[{'verified':True}],object())
        first,second=Mock(),Mock()
        first.publish.side_effect=PilotError('account needs attention')
        pool.readers={1:first,2:second}
        with self.assertRaises(PilotError): pool.publish({'post':{'subreddit':'nocode'}},pool.workflow)
        second.publish.assert_not_called()

    def test_closing_attached_reader_keeps_user_profile_open(self):
        reader=BrowserReader.__new__(BrowserReader)
        reader.external=True;reader.context=Mock();reader.page=Mock();reader.pw=Mock()
        reader.page.is_closed.return_value=False
        context,page,pw=reader.context,reader.page,reader.pw
        reader.close()
        page.close.assert_called_once();context.close.assert_not_called();pw.stop.assert_called_once()

    def test_challenge_tab_is_preserved_when_worker_disconnects(self):
        reader=BrowserReader.__new__(BrowserReader)
        reader.external=True;reader.context=Mock();reader.page=Mock();reader.pw=Mock()
        reader.page.is_closed.return_value=False;reader.handoff_pending=True
        context,page,pw=reader.context,reader.page,reader.pw
        reader.close()
        page.close.assert_not_called();context.close.assert_not_called();pw.stop.assert_called_once()
        page.unroute.assert_called_once()

    def test_profile_pool_checks_permission_before_local_api_access(self):
        config=configuration();config['permissions']['browser_collection']=False
        with self.assertRaises(PilotError): ProfilePool(Path('.'),config,[{'verified':True}],object())


if __name__=='__main__':unittest.main()
