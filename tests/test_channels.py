import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from pilot.core import Store, load_config, PilotError, validate_post
from pilot.channels import (Channels, channel_config, validate_channels, source_url,
                            published_url, paused, require_channel_ready)
from pilot.workflow import Workflow, draft_hash
from pilot.automation import Automation, record_quality
from pilot.attention import Attention
from pilot.gateway import messages_for
from pilot.campaigns import create_campaign, publication_text

ROOT = Path(__file__).resolve().parents[1]


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'state.db'); self.flow = Workflow(self.store)
        self.base = load_config(ROOT,'config.browser.example.json')
        self.base['channels'] = json.loads((ROOT/'channels.example.json').read_text())
        for ch in self.base['channels']: ch['policy']['reviewed_at'] = time.time()
        self.base['ai'] = 'oneapi'
        self.base['publishing'].update(mode='automatic',enabled=True,reddit_username='reddit_user',permission_confirmed=True,permission_reference='FIXTURE')
        self.base['permissions'] = dict(browser_collection=True,ai_processing=True,community_rules_reviewed=True,reference='FIXTURE')
        self.ch = self.base['channels'][0]
        self.ch['username'] = 'medo_team'
        self.cfg = channel_config(self.base,self.ch)
        self.post = dict(id='dev:123',platform='dev',kind='post',subreddit='dev',url='https://dev.to/person/client-portal-123',
                         title='Need a client portal for project status',body='I need a client portal with a project list and a project status screen.',
                         posted_at='2026-11-16T00:00:00Z',author='person')
        self.result = dict(decision='recommend',reason='Fits app generation',template_id='medo-app-builder',evidence='client portal',claim_ids=['generation'],
                           draft='Try MeDo for your client portal with a project list and a status screen. Start by describing those screens and the project fields, then test changing the status on a sample project.\n\nI work on MeDo.\nAutomated MeDo team response.')

    def ready(self, post=None, cfg=None):
        p, c = post or self.post, cfg or self.cfg
        self.store.ingest(p)
        result = record_quality(self.store,p,self.result,c)
        self.store.complete(p['id'],result)
        Automation(self.store,self.flow,c,[]).queue_ready()
        return self.flow.claim_publication(c)

    def test_initial_allocation_is_three_accounts_and_an_unused_forum_slot(self):
        validate_channels(self.base)
        manager = Channels(self.root,self.base,[],self.store,self.flow)
        rows = manager.status()
        self.assertEqual(sum(x['profiles'] for x in rows),3)
        self.assertEqual([x['id'] for x in rows],['reddit','dev','indiehackers','forum'])

    def test_duplicate_origins_and_profile_farms_rejected(self):
        self.ch['profile_count'] = 5
        with self.assertRaises(PilotError): validate_channels(self.base)
        self.ch['profile_count'] = 1
        dupe = copy.deepcopy(self.ch); dupe['id'] = 'dev2'; self.base['channels'].append(dupe)
        with self.assertRaises(PilotError): validate_channels(self.base)

    def test_destinations_reject_private_host_and_credentials(self):
        for origin in ('http://dev.to','https://127.0.0.1','https://user:pass@dev.to','https://dev.to.attacker.test'):
            self.ch['origin'] = origin
            with self.assertRaises(PilotError): validate_channels(self.base)

    def test_source_urls_are_fixed_to_platform(self):
        self.assertTrue(source_url(self.post['url'],self.ch))
        for url in ('https://dev.to/t/help','https://evil.test/person/post','https://dev.to/person/post?redirect=x','https://dev.to/person/post#comment-2'):
            self.assertFalse(source_url(url,self.ch))

    def test_comment_readback_must_match_thread(self):
        self.assertTrue(published_url(self.post['url']+'#comment-123',self.post,self.ch))
        self.assertFalse(published_url('https://dev.to/person/different#comment-123',self.post,self.ch))
        self.assertFalse(published_url(self.post['url'],self.post,self.ch))

    def test_platform_survives_validation_and_default_claim_does_not_take_it(self):
        post = validate_post(self.post,self.cfg)
        self.assertEqual(post['platform'],'dev'); self.assertEqual(post['platform_type'],'dev')
        self.store.ingest(post)
        self.assertIsNone(self.store.claim(3))
        self.assertEqual(self.store.claim(3,platform='dev')['id'],post['id'])

    def test_cross_platform_payload_rejected(self):
        with self.assertRaises(PilotError): validate_post(self.post,self.base)

    def test_automatic_outbox_is_scoped_but_limits_are_shared(self):
        job = self.ready()
        self.assertEqual(job['item_id'],self.post['id'])
        self.flow.mark_submitting(job['item_id'],self.cfg)
        self.flow.publication_done(job,self.post['url']+'#comment-123',self.cfg)
        second = dict(self.post,id='forum:123',platform='forum',subreddit='forum',url='https://forum.example.org/t/project/123')
        ch = dict(self.ch,id='forum',type='discourse',origin='https://forum.example.org',feeds=['https://forum.example.org/latest'],campaign_plan=[])
        cfg = channel_config(self.base,ch)
        self.store.ingest(second)
        other = dict(self.result,draft='MeDo can help outline a portfolio gallery. Try requesting a grid of photography projects and separate pages for their images. Check how portrait and landscape photographs fit before publishing any version.\n\nI work on MeDo.')
        self.store.complete(second['id'],record_quality(self.store,second,other,cfg))
        Automation(self.store,self.flow,cfg,[]).queue_ready()
        self.assertIsNone(self.flow.claim_publication(cfg))
        self.assertIn('cooldown',next(r for r in self.flow.outbox() if r['item_id']==second['id'])['error'])

    def test_reddit_worker_does_not_claim_or_block_dev_job(self):
        self.store.ingest(self.post); self.store.complete(self.post['id'],record_quality(self.store,self.post,self.result,self.cfg))
        Automation(self.store,self.flow,self.cfg,[]).queue_ready()
        self.assertIsNone(self.flow.claim_publication(self.base))
        self.assertEqual(self.flow.outbox()[0]['state'],'ready')

    def test_wrong_platform_cannot_queue_or_submit(self):
        job = self.ready()
        with self.assertRaisesRegex(PilotError,'boundaries'): self.flow.mark_submitting(job['item_id'],self.base)
        self.assertEqual(self.flow.outbox()[0]['state'],'checking')

    def test_channel_pause_does_not_pause_another_platform(self):
        self.store.set_setting('paused.dev','1')
        self.assertTrue(paused(self.store,self.cfg)); self.assertFalse(paused(self.store,self.base))
        self.store.set_setting('paused','1'); self.assertTrue(paused(self.store,self.base))

    def test_reddit_captcha_is_scoped_in_multiplatform_mode(self):
        attention = Attention(self.store,'reddit'); attention.record({'kind':'captcha'},'Solve manually')
        self.assertTrue(paused(self.store,self.base)); self.assertFalse(paused(self.store,self.cfg))
        self.assertIsNotNone(attention.current()); self.assertIsNone(Attention(self.store).current())

    def test_crash_pauses_only_affected_platform_and_never_resends(self):
        job = self.ready(); self.flow.mark_submitting(job['item_id'],self.cfg)
        self.flow.recover(isolate=True)
        self.assertTrue(paused(self.store,self.cfg)); self.assertFalse(paused(self.store,self.base))
        self.assertEqual(self.flow.outbox()[0]['state'],'uncertain')

    def test_indiehackers_is_prepared_but_requires_specific_exception(self):
        ih = self.base['channels'][1]; ih['username']='founder'
        with self.assertRaisesRegex(PilotError,'site-specific'): require_channel_ready(self.store,channel_config(self.base,ih))

    def test_stale_policy_is_visible_before_discovery(self):
        self.ch['policy']['reviewed_at'] = time.time()-31*86400
        with self.assertRaisesRegex(PilotError,'fresh review'): require_channel_ready(self.store,channel_config(self.base,self.ch))

    def test_missing_identity_is_not_ready(self):
        with self.assertRaisesRegex(PilotError,'Sign in'): require_channel_ready(self.store,channel_config(self.base,self.ch,''))

    def test_reply_prompt_uses_destination_style(self):
        message = messages_for(dict(self.post,platform_type='dev'),[])[0]['content']
        self.assertIn('Destination: dev',message)
        self.assertIn('unrelated article',message)

    def test_campaign_url_and_namespace_are_platform_specific(self):
        post = create_campaign(self.cfg['automation']['campaign_plan'][0],self.cfg)
        self.assertEqual(post['platform'],'dev'); self.assertEqual(post['url'],'https://dev.to/new')

    def test_captcha_handoff_never_switches_accounts(self):
        manager = Channels(self.root,self.base,[],self.store,self.flow)
        manager.alert(self.ch,'CAPTCHA')
        browser = Mock(); browser.identity.return_value = 'other_account'
        manager.browsers['dev'] = browser
        manager.command('dev','check')
        with self.assertRaisesRegex(PilotError,'switching'): manager.controls(self.ch,self.cfg)
        self.assertEqual(self.store.setting('paused.dev'),'1')

    def test_global_pause_prevents_any_browser_or_model_work(self):
        manager = Channels(self.root,self.base,[],self.store,self.flow)
        self.store.set_setting('paused','1')
        with patch.object(manager,'browser') as browser:
            manager.tick(); browser.assert_not_called()

    def test_bound_account_persists_without_storing_credentials(self):
        manager = Channels(self.root,self.base,[],self.store,self.flow)
        browser = Mock(); browser.identity.return_value='medo_team'; manager.browsers['dev']=browser
        manager.command('dev','connect'); manager.controls(self.ch,self.cfg)
        self.assertEqual(manager.config(self.ch)['publishing']['reddit_username'],'medo_team')
        self.assertEqual(self.store.setting('paused.dev'),'0')

    def test_unknown_platform_commands_are_rejected(self):
        manager = Channels(self.root,self.base,[],self.store,self.flow)
        with self.assertRaises(PilotError): manager.command('other','open')
        with self.assertRaises(PilotError): manager.command('dev','publish')

    def test_full_channel_cycle_uses_two_model_checks_and_auto_publishes(self):
        self.base['channels'] = [self.ch]; self.ch['campaign_plan'] = []
        self.base['max_daily_tokens'] = 200000
        catalog = json.loads((ROOT/'catalog.medo.json').read_text())
        manager = Channels(self.root,self.base,catalog,self.store,self.flow)
        post = dict(self.post,posted_at=dt.datetime.now(dt.timezone.utc).isoformat(),discussion=[])
        browser = Mock(); browser.collect.return_value=[post]; browser.enrich.side_effect=lambda p:p
        def publish(job, flow):
            flow.mark_submitting(job['item_id'],manager.config(self.ch))
            return self.post['url']+'#comment-123'
        browser.publish.side_effect=publish; manager.browsers['dev']=browser
        raw = dict(self.result,draft=self.result['draft'].split('\n\n')[0])
        gateway = Mock(); gateway.chat.return_value=(json.dumps(raw),{'prompt_tokens':100,'completion_tokens':100},10)
        with patch('pilot.gateway.Gateway',return_value=gateway): manager.tick()
        self.assertEqual(gateway.chat.call_count,2)
        self.assertEqual(self.store.items()[0]['status'],'published')
        self.assertEqual(self.flow.outbox()[0]['approval_source'],'automatic')

    def test_automation_status_counts_only_its_own_platform(self):
        self.ready()
        self.assertEqual(Automation(self.store,self.flow,self.cfg,[]).status()['automatic_pending'],1)
        self.assertEqual(Automation(self.store,self.flow,self.base,[]).status()['automatic_pending'],0)


if __name__ == '__main__': unittest.main()
