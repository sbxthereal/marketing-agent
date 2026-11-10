import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock

from pilot.campaigns import (create_campaign, analyze_campaign, validate_campaign_result,
                             publication_text, require_post_policy, thread_permalink)
from pilot.core import Store, PilotError, load_config
from pilot.service import server_for, Worker
from pilot.workflow import Workflow, draft_hash

ROOT=Path(__file__).resolve().parents[1]
BRIEF='Explain how to plan a client portal with project statuses and a screen for reading updates. Describe steps to request and test, not a finished app.'
COPY=('Start a client portal in MeDo by describing the project list and the status screen you want. '
      'Keep the first request specific: each project needs a name, a current status and a short update. '
      'Ask for those screens before adding anything else.\n\n'
      'Then use chat to refine the wording and layout around how your clients actually read updates. '
      'Try it with sample project data, check that the statuses make sense, and test the navigation from the list to each project. '
      'Treat this as a proposed workflow to test, not a completed portal or a promise that every requirement is already covered.')

class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.db');self.flow=Workflow(self.store)
        self.config=load_config(ROOT,'config.example.json')
        self.config.update(source='browser',ai='oneapi',subreddits=['pilottest'],model='oneapi-test',max_daily_tokens=150000)
        self.config['publishing'].update(enabled=True,permission_confirmed=True,permission_reference='LOCAL TEST ONLY',reddit_username='pilot_test')
        self.config['community_policies']={'pilottest':{'standalone_posts':True,'links_allowed':True,'reference':'LOCAL FIXTURE RULES','notes':'Useful disclosed tutorials permitted in this fixture only','reviewed_at':time.time()}}
        self.catalog=[{'id':'medo','name':'MeDo','url':'https://medo.dev/','verified':True,'claims':{'iteration':'Apps can be refined through chat'},'limitations':[]}]
        self.post=create_campaign({'subreddit':'pilottest','format':'tutorial','brief':BRIEF},self.config)
        self.raw={'decision':'recommend','reason':'Specific portal planning tutorial','title':'Planning a client portal: start with the project status screen','draft':COPY,'template_id':'medo','evidence':'client portal','claim_ids':['iteration']}
        self.result=validate_campaign_result(self.raw,self.post,self.catalog,self.config)

    def approved(self):
        self.store.ingest(self.post);self.store.complete(self.post['id'],self.result);self.store.review(self.post['id'],'approve')

    def request(self):
        self.flow.request_publish(self.post['id'],draft_hash(publication_text(self.result)),self.config)

    def test_title_and_body_are_both_bound_to_approval(self):
        self.approved();old=draft_hash(publication_text(self.result))
        changed=dict(self.result,title='A different title, with different implications')
        with self.store.db() as c:c.execute('UPDATE items SET result=?',(json.dumps(changed),))
        with self.assertRaisesRegex(PilotError,'draft changed'):self.flow.request_publish(self.post['id'],old,self.config)
        self.assertEqual(self.flow.outbox(),[])

    def test_post_approval_never_sends_and_claim_keeps_title(self):
        self.approved();self.assertIsNone(self.flow.claim_publication(self.config))
        self.request();job=self.flow.claim_publication(self.config)
        self.assertEqual(job['title'],self.raw['title']);self.assertEqual(job['draft'],self.result['draft'])
        self.assertEqual(job['reddit_username'],'pilot_test')

    def test_no_policy_means_drafts_possible_but_no_publication(self):
        self.config['community_policies']={}
        self.post=create_campaign({'subreddit':'pilottest','format':'tutorial','brief':BRIEF},self.config)
        self.approved()
        with self.assertRaisesRegex(PilotError,'not approved'):self.request()

    def test_policy_change_blocks_queued_job(self):
        self.approved();self.request();self.config['community_policies']['pilottest']['notes']='Rules changed'
        self.assertIsNone(self.flow.claim_publication(self.config));self.assertEqual(self.flow.outbox()[0]['state'],'blocked')

    def test_policy_rechecked_immediately_before_external_write(self):
        self.approved();self.request();self.flow.claim_publication(self.config)
        self.config['community_policies']['pilottest']['standalone_posts']=False
        with self.assertRaises(PilotError):self.flow.mark_submitting(self.post['id'],self.config)
        self.assertEqual(self.flow.outbox()[0]['state'],'checking')

    def test_expired_rules_require_review(self):
        self.config['community_policies']['pilottest']['reviewed_at']=time.time()-31*86400
        with self.assertRaisesRegex(PilotError,'fresh review'):require_post_policy(self.post,self.config)

    def test_reply_and_post_share_the_same_cooldown(self):
        reply={'id':'t3_test123','kind':'post','subreddit':'pilottest','title':'Question about portfolio design','body':'Question','url':'https://www.reddit.com/r/pilottest/comments/test123/question/'}
        self.store.ingest(reply);self.store.complete(reply['id'],{'decision':'recommend','draft':'A distinct portfolio reply for this test.'});self.store.review(reply['id'],'approve')
        self.store.review(reply['id'],'published',reply['url']+'c123/',self.config['subreddits'])
        self.approved();self.request();self.assertIsNone(self.flow.claim_publication(self.config))
        self.assertIn('cooldown',self.flow.outbox()[0]['error'])

    def test_post_submission_counts_toward_global_limit(self):
        self.approved();self.request();job=self.flow.claim_publication(self.config);self.flow.mark_submitting(self.post['id'],self.config)
        self.flow.publication_done(job,'https://www.reddit.com/r/pilottest/comments/def456/tutorial/',self.config)
        self.assertEqual(self.flow.pacing(self.config)['last_24h'],1)
        self.assertEqual(self.store.items()[0]['status'],'published')

    def test_same_post_cannot_be_queued_twice_and_crash_is_uncertain(self):
        self.approved();self.request()
        with self.assertRaises(PilotError):self.request()
        self.flow.claim_publication(self.config);self.flow.mark_submitting(self.post['id'],self.config)
        Workflow(self.store).recover();self.assertEqual(self.flow.outbox()[0]['state'],'uncertain')
        self.assertEqual(self.store.setting('paused'),'1')

    def test_permalink_scope_rejects_comments_and_other_communities(self):
        url='https://www.reddit.com/r/pilottest/comments/def456/tutorial/'
        self.assertTrue(thread_permalink(url,self.post,['pilottest']))
        for bad in (url+'c123/',url.replace('pilottest','other'),url+'?query=1',url.replace('https://','http://')):
            self.assertFalse(thread_permalink(bad,self.post,['pilottest']))

    def test_claims_links_fabrication_and_hidden_draft_rejected(self):
        for key,value in [('claim_ids',['invented']),('evidence','not in brief'),('draft',COPY+' https://example.com'),('draft',COPY+' We built this for our customers.'),('draft',COPY+' MeDo lists a mobile product.'),('decision','skip')]:
            with self.subTest(key=key),self.assertRaises(PilotError):
                validate_campaign_result(dict(self.raw,**{key:value}),self.post,self.catalog,self.config)

    def test_link_added_only_when_policy_allows_and_disclosure_always_present(self):
        self.assertTrue(self.result['draft'].startswith('I work on MeDo.'))
        self.assertTrue(self.result['draft'].endswith('https://medo.dev/'))
        self.config['community_policies']['pilottest']['links_allowed']=False
        result=validate_campaign_result(self.raw,self.post,self.catalog,self.config)
        self.assertNotIn('https://',result['draft'])

    def test_two_oneapi_checks_are_metered_and_bad_editor_output_is_held(self):
        gateway=Mock();gateway.chat.side_effect=[(json.dumps(self.raw),{'prompt_tokens':10,'completion_tokens':20},1),('{bad',{'prompt_tokens':3,'completion_tokens':4},1)]
        result=analyze_campaign(gateway,self.post,self.catalog,self.config,self.store)
        self.assertEqual(result['decision'],'review');self.assertEqual(result['draft'],'');self.assertEqual(result['title'],'')
        self.assertEqual(self.store.summary()['usage']['calls'],2);self.assertEqual(self.store.summary()['usage']['budget_tokens'],37)

    def test_no_budget_for_editor_never_releases_unchecked_copy(self):
        self.config['max_daily_calls']=1
        gateway=Mock();gateway.chat.return_value=(json.dumps(self.raw),{'prompt_tokens':10,'completion_tokens':20},1)
        result=analyze_campaign(gateway,self.post,self.catalog,self.config,self.store)
        self.assertEqual(result['decision'],'review');self.assertEqual(result['draft'],'');self.assertEqual(gateway.chat.call_count,1)

    def test_validated_campaign_uses_oneapi_without_thread_enrichment(self):
        self.config['permissions']={k:True for k in ('browser_collection','ai_processing','community_rules_reviewed')};self.config['permissions']['reference']='LOCAL FIXTURE ONLY'
        self.config['publishing']['enabled']=False
        worker=Worker(ROOT,self.config,self.catalog,self.store)
        worker.browser=Mock();worker.browser.collect.return_value=[]
        worker.gateway=Mock();worker.gateway.chat.return_value=(json.dumps(self.raw),{'prompt_tokens':10,'completion_tokens':20},1)
        self.store.ingest(self.post);worker.cycle()
        worker.browser.enrich.assert_not_called();self.assertEqual(self.store.items()[0]['status'],'review')
        self.assertEqual(worker.gateway.chat.call_count,2)

    def test_explicit_post_brief_can_draft_during_discovery_pause_without_browser(self):
        self.store.set_setting('paused','1')
        worker=Worker(ROOT,self.config,self.catalog,self.store)
        worker.gateway=Mock();worker.gateway.chat.return_value=(json.dumps(self.raw),{'prompt_tokens':10,'completion_tokens':20},1)
        self.store.ingest(self.post);worker.cycle()
        self.assertIsNone(worker.browser)
        self.assertEqual(self.store.items()[0]['status'],'review')
        self.assertEqual(self.store.setting('paused'),'1');self.assertEqual(self.flow.outbox(),[])

    def test_gateway_failure_keeps_browser_status_and_does_not_retry_brief(self):
        self.store.set_setting('paused','1');self.store.set_setting('message','Browser needs human verification')
        worker=Worker(ROOT,self.config,self.catalog,self.store)
        worker.gateway=Mock();worker.gateway.chat.side_effect=PilotError('Gateway HTTP 401')
        self.store.ingest(self.post);worker.cycle();worker.cycle()
        self.assertEqual(self.store.items()[0]['status'],'error')
        self.assertEqual(worker.gateway.chat.call_count,1)
        self.assertEqual(self.store.setting('message'),'Browser needs human verification')

    def test_create_api_is_authenticated_and_company_dashboard_is_read_only(self):
        import socket
        for read_only in (False,True):
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            config=dict(self.config,port=port);server=server_for(ROOT,config,self.store,read_only=read_only)
            threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            origin='http://127.0.0.1:'+str(port)
            with urllib.request.urlopen(origin) as response:page=response.read().decode()
            token=re.search("const token='([^']*)'",page).group(1)
            req=urllib.request.Request(origin+'/api/campaigns',data=json.dumps({'subreddit':'pilottest','format':'tutorial','brief':BRIEF}).encode(),headers={'Content-Type':'application/json','Origin':origin,'X-Pilot-Token':token})
            if read_only:
                with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req)
                self.assertEqual(err.exception.code,403)
            else:
                with urllib.request.urlopen(req) as response:self.assertEqual(response.status,200)
            with urllib.request.urlopen(origin+'/api/items') as response:items=json.load(response)
            self.assertEqual(len(items),1);self.assertEqual(items[0]['payload']['kind'],'campaign');self.assertEqual(items[0]['status'],'queued')

if __name__=='__main__':unittest.main()
