import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from pilot.core import Store, PilotError, validate_post
from pilot.discovery import rank, search_target, timestamp, scan_delay
from pilot.quality import concerns, context_skip, similar_reply
from pilot.workflow import Workflow, draft_hash
from pilot.gateway import analyze


class DiscoveryQualityTests(unittest.TestCase):
    def setUp(self):
        self.post={'id':'t3_need','kind':'post','subreddit':'nocode','url':'https://www.reddit.com/r/nocode/comments/need/travel/',
                   'title':'Which tool for a beginner building a travel app?', 'body':'I need a simple travel app with saved trips. I am a beginner.', 'author':'asker'}
        self.draft='Try MeDo for your travel app. You can describe the screens you need and refine them through chat. Start with a trip list and one itinerary screen, then test whether you can create and reopen a saved trip before adding more features.'

    def test_help_request_ranks_above_showcase_but_no_hard_filter(self):
        showcase=dict(self.post,title='I launched my new travel app',body='Check it out')
        self.assertGreater(rank(self.post)[0],rank(showcase)[0])
        self.assertEqual(rank(dict(self.post,title='Something unusual',body=''))[0],0)

    def test_search_is_scoped_new_sorted_and_rotates_communities(self):
        c={'subreddits':['nocode','webdev'],'search_queries':['client portal','waitlist']}
        self.assertEqual(search_target(c,0)[:2],('nocode','client portal'))
        self.assertEqual(search_target(c,1)[:2],('webdev','client portal'))
        self.assertEqual(search_target(c,2)[:2],('nocode','waitlist'))
        self.assertIn('restrict_sr=on&sort=new',search_target(c,2)[2])
        self.assertIn('q=client+portal',search_target(c,0)[2])

    def test_idle_backoff_is_bounded_and_resets(self):
        c={'poll_seconds':300,'max_idle_poll_seconds':1800}
        self.assertEqual([scan_delay(c,n) for n in [0,1,2,3,99,0]],[300,600,1200,1800,1800,300])

    def test_timestamp_rejects_ambiguous_local_date(self):
        self.assertEqual(timestamp('2026-11-09T10:00:00'),0)
        self.assertGreater(timestamp('2026-11-09T10:00:00+00:00'),0)

    def test_metadata_survives_post_validation(self):
        p=validate_post(dict(self.post,posted_at='2026-11-09T10:00:00Z',discovery_source='search: travel',expected='recommend'),{'source':'browser','subreddits':['nocode']})
        self.assertEqual(p['author'],'asker');self.assertNotIn('expected',p)

    def test_direct_specific_reply_passes(self):
        self.assertEqual(concerns(self.draft,self.post),[])

    def test_generic_replies_and_unsupported_claims_are_held(self):
        for text in ['Great question! '+self.draft,self.draft.replace('MeDo','a powerful platform'),self.draft+' It is guaranteed to work.', 'MeDo is a tool for software. '*9]:
            with self.subTest(text=text):self.assertTrue(concerns(text,self.post))

    def test_cosmetic_rewording_is_still_duplicate(self):
        self.assertTrue(similar_reply(self.draft,self.draft.replace('Try','Consider')+'\nI work on MeDo.\nhttps://medo.dev/'))
        self.assertFalse(similar_reply(self.draft,'MeDo has a portfolio starting point. Put three design projects on the page, with a short explanation of your role on each.'))

    def test_existing_recommendation_and_own_thread_skip(self):
        self.assertTrue(context_skip(self.post,'asker'))
        self.assertTrue(context_skip(dict(self.post,body=self.post['body']+'\n[Existing replies, context only]\nTry MeDo.'),'marketer'))
        self.assertFalse(context_skip(self.post,'marketer'))

    def test_model_quality_failure_does_not_trigger_rewrite_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db')
            class Fake:
                calls=0
                def chat(inner,*args):
                    inner.calls+=1
                    return json.dumps(dict(decision='recommend',reason='Fit',template_id='builder',evidence='simple travel app',claim_ids=['generation'],draft='A plain-language app builder can help.')),{'prompt_tokens':100,'completion_tokens':40},10
            gateway=Fake();cat=[{'id':'builder','name':'MeDo','url':'https://medo.dev/','claims':{'generation':'Build apps'},'limitations':[]}]
            c={'model':'test','max_daily_calls':5,'max_daily_tokens':100000,'max_output_tokens':1000,'review_recommendations':False}
            result=analyze(gateway,self.post,cat,c,store)
            self.assertEqual(result['decision'],'review');self.assertEqual(result['draft'],'');self.assertEqual(gateway.calls,1)

    def test_recommendation_gets_one_metered_editor_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db');draft=self.draft
            class Fake:
                calls=0
                def chat(inner,*args):
                    inner.calls+=1
                    return json.dumps(dict(decision='recommend',reason='Fit',template_id='builder',evidence='simple travel app',claim_ids=['generation'],draft=draft)),{'prompt_tokens':100,'completion_tokens':40},10
            gateway=Fake();cat=[{'id':'builder','name':'MeDo','url':'https://medo.dev/','claims':{'generation':'Build apps'},'limitations':[]}]
            c={'model':'test','max_daily_calls':5,'max_daily_tokens':100000,'max_output_tokens':1000}
            result=analyze(gateway,self.post,cat,c,store)
            self.assertEqual(result['decision'],'recommend');self.assertEqual(gateway.calls,2)
            self.assertEqual(store.summary()['usage']['calls'],2)

    def test_editor_budget_failure_cannot_release_unchecked_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db');draft=self.draft
            class Fake:
                def chat(inner,*args):
                    return json.dumps(dict(decision='recommend',reason='Fit',template_id='builder',evidence='simple travel app',claim_ids=['generation'],draft=draft)),{'prompt_tokens':100,'completion_tokens':40},10
            cat=[{'id':'builder','name':'MeDo','url':'https://medo.dev/','claims':{'generation':'Build apps'},'limitations':[]}]
            c={'model':'test','max_daily_calls':1,'max_daily_tokens':100000,'max_output_tokens':1000}
            result=analyze(Fake(),self.post,cat,c,store)
            self.assertEqual(result['decision'],'review');self.assertEqual(result['draft'],'')
            self.assertEqual(store.summary()['usage']['calls'],1)


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.db');self.flow=Workflow(self.store);self.now=time.time()
        self.config={'source':'browser','publishing':{'enabled':True,'reddit_username':'marketer','permission_confirmed':True,'permission_reference':'LOCAL TEST',
            'max_per_day':3,'min_interval_seconds':7200,'max_per_subreddit_day':2,'subreddit_interval_seconds':21600,'author_cooldown_hours':168}}
        self.post={'id':'t3_new','kind':'post','subreddit':'nocode','url':'https://www.reddit.com/r/nocode/comments/new/help/','title':'Need a travel app','body':'Saved trips','author':'newasker'}
        self.draft='MeDo could work for your travel app. Start with a saved trip list and one itinerary screen.'
        self.store.ingest(self.post);self.store.complete(self.post['id'],{'decision':'recommend','draft':self.draft});self.store.review(self.post['id'],'approve')

    def seed(self,age,sub='different',author='other',ident='old',draft='A genuinely different reviewed response about a portfolio.'):
        post=dict(self.post,id='t3_'+ident,subreddit=sub,author=author)
        self.store.ingest(post)
        with self.store.db() as c:
            c.execute("INSERT INTO outbox(item_id,draft_hash,draft,state,requested,updated,submitted,subreddit,author_hash) VALUES(?,?,?,'published',?,?,?,?,?)",(post['id'],draft_hash(draft),draft,self.now-age,self.now-age,self.now-age,sub,self.flow.author_key(post)))

    def queue(self):self.flow.request_publish(self.post['id'],draft_hash(self.draft),self.config)
    def claim(self,seconds=0):
        with patch('pilot.workflow.time.time',return_value=self.now+seconds):return self.flow.claim_publication(self.config)

    def test_global_cooldown_survives_restart_and_opens_after_two_hours(self):
        self.seed(3600);self.queue();self.flow=Workflow(Store(self.store.path))
        self.assertIsNone(self.claim());self.assertIsNotNone(self.claim(3601))

    def test_rolling_daily_cap_not_calendar_midnight(self):
        for i,age in enumerate([8000,17000,26000]):self.seed(age,ident=str(i))
        self.queue();self.assertIsNone(self.claim())
        self.assertIn('Daily',self.flow.outbox()[0]['error'])

    def test_community_cooldown(self):
        self.seed(3*3600,sub='nocode');self.queue()
        self.assertIsNone(self.claim());self.assertIsNotNone(self.claim(3*3600+1))

    def test_author_cooldown_across_communities(self):
        self.seed(2*86400,author='newasker');self.queue();self.assertIsNone(self.claim())
        self.assertIn('author',self.flow.outbox()[0]['error'])

    def test_duplicate_reply_blocked_even_after_daily_window(self):
        self.seed(2*86400,draft=self.draft.replace('could work','can work'))
        with self.assertRaisesRegex(PilotError,'similar'):self.queue()

    def test_manual_publication_consumes_allowance(self):
        self.seed(3600)
        with self.store.db() as c:
            c.execute("DELETE FROM outbox WHERE item_id='t3_old'")
            c.execute("UPDATE items SET status='published',updated=? WHERE id='t3_old'",(self.now-3600,))
        self.queue();self.assertIsNone(self.claim());self.assertEqual(self.flow.pacing(self.config)['last_24h'],1)

    def test_aging_keeps_unusual_requests_in_queue(self):
        low=dict(self.post,id='t3_weird',title='Something unusual',body='Unrecognized vocabulary')
        self.store.ingest(low)
        self.store.ingest(dict(self.post,id='t3_high',title='Which app should I build as a beginner?'))
        with self.store.db() as c:c.execute('UPDATE items SET created=? WHERE id=?',(time.time()-22*3600,low['id']))
        self.assertEqual(self.store.claim(3)['id'],low['id'])

    def test_content_expiry_does_not_reset_publication_cooldown(self):
        self.seed(3600)
        with self.store.db() as c:c.execute("UPDATE outbox SET requested=? WHERE item_id='t3_old'",(self.now-8*86400,))
        self.flow.expire(7);self.queue();self.assertIsNone(self.claim())
        with self.store.db() as c:row=c.execute("SELECT draft,state FROM outbox WHERE item_id='t3_old'").fetchone()
        self.assertEqual(row['draft'],'');self.assertEqual(row['state'],'published')


if __name__=='__main__':unittest.main()
