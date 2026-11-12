import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from pilot.automation import Automation, validate_config
from pilot.campaigns import analyze_campaign, create_campaign
from pilot.core import Store, PilotError, load_config
from pilot.gateway import analyze
from pilot.service import Worker
from pilot.workflow import Workflow


ROOT = Path(__file__).resolve().parents[1]


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'state.db'); self.flow = Workflow(self.store)
        self.config = load_config(ROOT, 'config.example.json')
        self.config.update(source='browser', ai='oneapi', subreddits=['pilottest'], max_daily_tokens=1000000)
        self.config['publishing'].update(mode='automatic', enabled=True, permission_confirmed=True,
                                         permission_reference='LOCAL FIXTURE ONLY', reddit_username='pilot_test')
        self.config['permissions'] = dict(browser_collection=True, ai_processing=True,
                                          community_rules_reviewed=True, reference='LOCAL FIXTURE ONLY')
        self.config['community_policies'] = {'pilottest': dict(automated_replies=True, automated_posts=True,
            standalone_posts=True, links_allowed=True, reference='LOCAL FIXTURE ONLY',
            notes='Local test destination permits disclosed replies and tutorials', reviewed_at=time.time())}
        self.catalog = [dict(id='builder', name='MeDo', url='https://medo.dev/', verified=True,
            claims={'generation':'Generate app screens from descriptions', 'iteration':'Refine apps through chat'}, limitations=[])]
        self.post = dict(id='t3_need', kind='post', subreddit='pilottest', author='asker',
            url='https://www.reddit.com/r/pilottest/comments/need/travel/',
            title='Which tool for a beginner building a travel app?',
            body='I need a simple travel app with saved trips. I am a beginner.')
        self.raw = dict(decision='recommend', reason='Specific beginner app request', template_id='builder',
            evidence='simple travel app', claim_ids=['generation','iteration'],
            draft='Try MeDo for your travel app. You can describe the screens you need and refine them through chat. Start with a trip list and one itinerary screen, then test whether you can create and reopen a saved trip before adding more features.')
        self.auto = Automation(self.store, self.flow, self.config, self.catalog)
        self.gateway = Mock()
        self.gateway.chat.return_value = (json.dumps(self.raw), {'prompt_tokens':10,'completion_tokens':20}, 1)

    def draft(self):
        self.store.ingest(self.post)
        result = analyze(self.gateway, self.post, self.catalog, self.config, self.store)
        self.assertEqual(result['decision'], 'recommend', result)
        self.store.complete(self.post['id'], result)
        return result

    def test_two_machine_checks_queue_and_verify_without_human_approval(self):
        self.draft(); self.auto.queue_ready()
        self.assertEqual(self.gateway.chat.call_count, 2)
        self.assertEqual(self.flow.outbox()[0]['approval_source'], 'automatic')
        job = self.flow.claim_publication(self.config)
        self.flow.mark_submitting(job['item_id'], self.config)
        self.flow.publication_done(job, self.post['url']+'comment123/', self.config)
        self.auto.queue_ready()
        self.assertEqual(self.store.items()[0]['status'], 'published')
        self.assertIsNone(self.flow.claim_publication(self.config))

    def test_worker_generates_and_publishes_in_same_cycle(self):
        worker = Worker(ROOT, self.config, self.catalog, self.store)
        worker.browser = Mock(); worker.browser.collect.return_value = [self.post]
        worker.browser.enrich.side_effect = lambda post: dict(post)
        worker.gateway = self.gateway
        def publish(job, flow):
            flow.mark_submitting(job['item_id'], self.config)
            return self.post['url']+'comment123/'
        worker.browser.publish.side_effect = publish
        worker.cycle()
        self.assertEqual(self.store.items()[0]['status'], 'published')
        self.assertEqual(worker.browser.publish.call_count, 1)
        worker.cycle()
        self.assertEqual(worker.browser.publish.call_count, 1)

    def test_old_or_unchecked_draft_never_enters_outbox(self):
        self.store.ingest(self.post); self.store.complete(self.post['id'], self.raw)
        self.auto.queue_ready(); self.assertEqual(self.flow.outbox(), [])

    def test_invalid_editor_does_not_get_quality_receipt(self):
        self.gateway.chat.side_effect = [self.gateway.chat.return_value, ('{bad', {}, 1)]
        self.store.ingest(self.post)
        result = analyze(self.gateway, self.post, self.catalog, self.config, self.store)
        self.store.complete(self.post['id'], result); self.auto.queue_ready()
        self.assertEqual(result['decision'], 'review'); self.assertEqual(self.flow.outbox(), [])
        with self.store.db() as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM quality_receipts').fetchone()[0], 0)

    def test_budget_missing_for_editor_never_publishes(self):
        self.config['max_daily_calls'] = 1
        self.store.ingest(self.post)
        result = analyze(self.gateway, self.post, self.catalog, self.config, self.store)
        self.store.complete(self.post['id'], result); self.auto.queue_ready()
        self.assertEqual(result['decision'], 'review'); self.assertEqual(self.flow.outbox(), [])

    def test_tampered_result_source_or_policy_invalidates_receipt(self):
        result = self.draft()
        for column, value in [('result', dict(result, draft=result['draft']+' Extra claim.')),
                              ('payload', dict(self.post, body='A changed request'))]:
            with self.subTest(column=column):
                with self.store.db() as c: c.execute('UPDATE items SET '+column+'=?', (json.dumps(value),))
                self.auto.queue_ready(); self.assertEqual(self.flow.outbox(), [])
                with self.store.db() as c: c.execute('UPDATE items SET payload=?,result=?', (json.dumps(self.post),json.dumps(result)))
        self.config['community_policies']['pilottest']['notes'] = 'Changed rules'
        self.auto.queue_ready(); self.assertEqual(self.flow.outbox(), [])

    def test_receipt_expiration_blocks_and_never_redrafts(self):
        self.draft()
        with self.store.db() as c: c.execute('UPDATE quality_receipts SET checked_at=?', (time.time()-49*3600,))
        self.auto.queue_ready(); self.assertEqual(self.flow.outbox(), [])
        self.assertEqual(self.gateway.chat.call_count, 2)

    def test_pause_mode_and_disable_prevent_queueing(self):
        self.draft(); self.store.set_setting('paused','1'); self.auto.queue_ready()
        self.assertEqual(self.flow.outbox(), [])
        self.store.set_setting('paused','0'); self.config['publishing']['mode']='manual'; self.auto.queue_ready()
        self.assertEqual(self.flow.outbox(), [])
        self.config['publishing'].update(mode='automatic', enabled=False); self.auto.queue_ready()
        self.assertEqual(self.flow.outbox(), [])

    def test_missing_identity_or_permission_does_not_queue(self):
        self.draft(); self.config['publishing']['reddit_username']=''
        with self.assertRaises(PilotError): self.auto.queue_ready()
        self.assertEqual(self.flow.outbox(), [])
        self.config['publishing']['reddit_username']='pilot_test'; self.config['permissions']['browser_collection']=False
        with self.assertRaises(PilotError): self.auto.queue_ready()
        self.assertEqual(self.flow.outbox(), [])

    def test_destination_disabled_after_queue_blocks_claim_and_submit(self):
        self.draft(); self.auto.queue_ready()
        rules=self.config['community_policies']['pilottest']; rules['automated_replies']=False
        self.assertIsNone(self.flow.claim_publication(self.config))
        self.assertEqual(self.flow.outbox()[0]['state'], 'blocked')

    def test_change_after_preflight_is_checked_before_click(self):
        self.draft(); self.auto.queue_ready(); self.flow.claim_publication(self.config)
        self.config['review_recommendations']=False
        with self.assertRaises(PilotError): self.flow.mark_submitting(self.post['id'],self.config)

    def test_restarts_do_not_duplicate_or_resend_uncertain_attempt(self):
        self.draft(); self.auto.queue_ready(); self.auto.queue_ready()
        self.assertEqual(len(self.flow.outbox()), 1)
        self.flow.claim_publication(self.config); self.flow.mark_submitting(self.post['id'],self.config)
        restarted=Workflow(Store(self.store.path)); restarted.recover()
        self.assertEqual(restarted.outbox()[0]['state'], 'uncertain')
        self.assertEqual(self.store.setting('paused'), '1')
        self.auto.queue_ready(); self.assertEqual(len(self.flow.outbox()), 1)

    def test_link_policy_removes_catalog_link_but_keeps_disclosure(self):
        self.config['community_policies']['pilottest']['links_allowed']=False
        result=self.draft(); self.auto.queue_ready()
        self.assertNotIn('https://', result['draft']); self.assertIn('I work on MeDo.',result['draft'])
        self.assertEqual(len(self.flow.outbox()), 1)

    def plan(self, name='portal'):
        return dict(id=name, subreddit='pilottest', format='tutorial', template_id='builder',
                    brief='Explain how to plan a client portal with project statuses and a screen for reading updates. Describe steps to request and test, not a finished app.')

    def test_campaign_plan_is_finite_shared_and_restart_safe(self):
        self.config['automation']['campaign_plan']=[self.plan(),self.plan('second')]
        validate_config(self.config); self.auto.schedule(); self.auto.schedule()
        self.assertEqual(len(self.store.items()),1)
        first=self.store.items()[0]; self.assertEqual(first['payload']['planned_template_id'],'builder')
        self.store.complete(first['id'],dict(decision='skip',draft='',reason='Fixture'))
        Automation(self.store,self.flow,self.config,self.catalog).schedule()
        self.assertEqual(len(self.store.items()),1)
        with self.store.db() as c: c.execute('UPDATE campaign_plan_runs SET created=?',(time.time()-169*3600,))
        self.auto.schedule(); self.assertEqual(len(self.store.items()),2)
        self.assertEqual(self.auto.status()['started_plans'],['portal','second'])

    def test_plan_waits_for_time_policy_and_verified_template(self):
        plan=self.plan(); self.config['automation']['campaign_plan']=[plan]
        plan['not_before']=time.time()+3600; self.auto.schedule(); self.assertEqual(self.store.items(),[])
        plan['not_before']=0; self.config['community_policies']['pilottest']['automated_posts']=False
        self.auto.schedule(); self.assertEqual(self.store.items(),[])
        self.config['community_policies']['pilottest']['automated_posts']=True; self.catalog[0]['verified']=False
        with self.assertRaises(PilotError): self.auto.schedule()
        self.assertEqual(self.store.items(),[])

    def test_automatic_campaign_shares_reply_allowance(self):
        self.draft(); self.auto.queue_ready(); job=self.flow.claim_publication(self.config)
        self.flow.mark_submitting(self.post['id'],self.config)
        self.flow.publication_done(job,self.post['url']+'comment123/',self.config)
        from test_campaigns import COPY
        post=create_campaign(self.plan(),self.config); self.store.ingest(post)
        raw=dict(decision='recommend',reason='Specific tutorial',title='Planning a client portal with project status updates',
                 draft=COPY,template_id='builder',evidence='client portal',claim_ids=['generation','iteration'])
        self.gateway.chat.return_value=(json.dumps(raw),{'prompt_tokens':10,'completion_tokens':20},1)
        result=analyze_campaign(self.gateway,post,self.catalog,self.config,self.store)
        self.assertEqual(result['decision'],'recommend',result)
        self.store.complete(post['id'],result); self.auto.queue_ready()
        self.assertIsNone(self.flow.claim_publication(self.config))
        self.assertIn('cooldown', next(r for r in self.flow.outbox() if r['item_id']==post['id'])['error'])

    def test_doctor_reports_selected_approval_mode(self):
        import io
        from contextlib import redirect_stdout
        from pilot.__main__ import main
        for mode in ('automatic', 'manual'):
            self.config['publishing']['mode']=mode
            output=io.StringIO()
            with patch('pilot.__main__.load_config',return_value=self.config), patch('pilot.__main__.load_catalog',return_value=self.catalog), patch('pilot.gateway.read_env',return_value={}), patch('sys.argv',['pilot','doctor']), redirect_stdout(output):
                main()
            self.assertEqual(json.loads(output.getvalue())['publishing_requires_explicit_review'],mode=='manual')

    def test_invalid_automatic_settings_fail_at_startup(self):
        for settings in ({'campaign_interval_hours':1}, {'campaign_plan':[self.plan(),self.plan()]}):
            config=copy.deepcopy(self.config); config['automation'].update(settings)
            with self.assertRaises(PilotError): validate_config(config)
        self.config['review_recommendations']=False
        with self.assertRaises(PilotError): validate_config(self.config)


if __name__ == '__main__': unittest.main()
