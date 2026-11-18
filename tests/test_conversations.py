import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from pilot.core import Store, PilotError, load_config, validate_post, validate_result
from pilot.workflow import Workflow, draft_hash
from pilot.conversations import watch, target_post, verify_receipt, require_open, thread_url
from pilot.gateway import messages_for, analyze
from pilot.quality import concerns
from pilot.discovery import rank
from pilot.insights import overview

ROOT=Path(__file__).resolve().parents[1]


def setup_case(test):
    test.tmp=tempfile.TemporaryDirectory();test.addCleanup(test.tmp.cleanup)
    test.store=Store(Path(test.tmp.name)/'state.db');test.flow=Workflow(test.store)
    test.config=load_config(ROOT,'config.browser.example.json')
    test.config.update(subreddits=['pilottest'],retention_days=7)
    test.config['publishing'].update(enabled=True,permission_confirmed=True,permission_reference='LOCAL TEST',reddit_username='pilot_test')
    test.rootpost=dict(id='t3_abc',kind='post',subreddit='pilottest',url='https://www.reddit.com/r/pilottest/comments/abc/travel/',title='Need a travel app',body='How can I build a travel app with saved trips?',author='asker')
    test.previous='Try MeDo for your travel app. Start with a trip list.\n\nI work on MeDo.'
    test.store.ingest(test.rootpost);test.store.complete('t3_abc',dict(decision='recommend',draft=test.previous));test.store.review('t3_abc','approve')
    test.flow.request_publish('t3_abc',draft_hash(test.previous),test.config)
    job=test.flow.claim_publication(test.config);test.flow.mark_submitting(job['item_id'],test.config)
    test.flow.publication_done(job,test.rootpost['url']+'own/',test.config)
    test.target=dict(id='t1_incoming',parent_id='t1_own',author='asker',body='Can I refine the trip screen through chat?',url=test.rootpost['url']+'incoming/',created='')
    test.data=dict(id='t3_abc',title=test.rootpost['title'],body=test.rootpost['body'],author='asker',closed=False,comments=[dict(id='t1_own',parent_id='t3_abc',author='pilot_test',body=test.previous,url=test.rootpost['url']+'own/',created=''),test.target])
    test.browser=Mock();test.browser.read_thread.return_value=test.data


class ConversationTests(unittest.TestCase):
    def setUp(self):setup_case(self)
    def incoming(self):
        return validate_post(target_post(dict(self.rootpost,id=self.target['id'],conversation_parent='t3_abc',previous_reply=self.previous),self.data,self.target),self.config)

    def test_only_direct_replies_ingested_and_restart_does_not_duplicate(self):
        self.data['comments'] += [dict(self.target,id='t1_other',parent_id='t3_abc'),dict(self.target,id='t1_self',author='pilot_test')]
        watch(self.store,self.flow,self.browser,self.config)
        self.assertEqual({x['id'] for x in self.store.items()},{'t3_abc','t1_incoming'})
        self.assertEqual(self.store.items()[0]['payload']['previous_reply'],self.previous)
        with self.store.db() as c:c.execute('UPDATE conversation_checks SET checked=0')
        watch(self.store,Workflow(self.store),self.browser,self.config)
        self.assertEqual(len(self.store.items()),2)

    def test_unknown_parent_text_or_account_does_not_trigger_reply(self):
        self.data['comments'][0]['body']='Edited old message'
        watch(self.store,self.flow,self.browser,self.config)
        self.assertEqual(len(self.store.items()),1)
        self.assertIn('changed',overview(self.store)['conversations'][0]['error'])

    def test_receipt_cannot_be_forged_by_flags(self):
        post=self.incoming()
        for change in [dict(conversation_parent='missing'),dict(previous_reply='fake'),dict(url='https://www.reddit.com/r/pilottest/comments/other/travel/'),dict(reply_to=dict(post['reply_to'],parent_id='t1_other'))]:
            with self.subTest(change=change),self.store.db() as c,self.assertRaises(PilotError):verify_receipt(c,dict(post,**change),self.config)
        with self.store.db() as c:self.assertTrue(verify_receipt(c,post,self.config))

    def test_followup_bypasses_only_author_cooldown(self):
        post=self.incoming();now=time.time()
        with self.store.db() as c:
            self.assertIn('Global',self.flow._wait_reason(c,post,self.config,now))
            c.execute('UPDATE outbox SET updated=?,submitted=?',(now-25000,now-25000))
            self.assertEqual(self.flow._wait_reason(c,post,self.config,now),'')
            first=dict(post);first.pop('conversation_parent');first.pop('previous_reply')
            self.assertIn('author',self.flow._wait_reason(c,first,self.config,now))
        limited=copy.deepcopy(self.config);limited['publishing']['max_per_day']=1
        with self.store.db() as c:self.assertIn('Daily',self.flow._wait_reason(c,post,limited,now))

    def test_optout_closes_thread_and_blocks_queued_continuation(self):
        post=self.incoming();self.target['body']='Please stop replying. Not interested.'
        watch(self.store,self.flow,self.browser,self.config)
        self.assertEqual(self.store.items()[0]['status'],'skipped')
        with self.store.db() as c,self.assertRaisesRegex(PilotError,'closed'):require_open(c,post,self.config)

    def test_acknowledgment_is_skipped_without_closing_future_questions(self):
        self.target['body']='Thanks!';watch(self.store,self.flow,self.browser,self.config)
        self.assertEqual(self.store.items()[0]['status'],'skipped')
        with self.store.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM conversation_stops').fetchone()[0],0)

    def test_followup_prompt_uses_incoming_question_and_previous_answer(self):
        post=self.incoming();messages=messages_for(post,[])
        content=json.loads(messages[1]['content'].split('\n',1)[1])['post']
        self.assertEqual(content['body'],self.target['body']);self.assertEqual(content['previous_team_reply'],self.previous)
        self.assertIn('Do not repeat',messages[0]['content'])
        cat=[dict(id='builder',name='MeDo',url='https://medo.dev/',claims={'chat':'Refine generated apps through chat'},limitations=[])]
        draft='Yes, you can refine the trip screen through chat. Describe the change you want, then test that screen.'
        raw=dict(decision='recommend',reason='Answers the chat question',template_id='builder',evidence='refine the trip screen',claim_ids=['chat'],draft=draft)
        self.assertEqual(concerns(draft,post),[])
        result=validate_result(raw,post,cat)
        self.assertNotIn('https://',result['draft']);self.assertIn('I work on MeDo.',result['draft'])
        gateway=Mock();gateway.chat.return_value=(json.dumps(raw),{'prompt_tokens':1,'completion_tokens':1},1)
        self.assertEqual(analyze(gateway,post,cat,self.config,self.store)['decision'],'recommend')
        self.assertEqual(gateway.chat.call_count,2)

    def test_reply_limit_excludes_current_inflight_attempt(self):
        post=self.incoming();self.store.ingest(post)
        with self.store.db() as c:
            c.execute("INSERT INTO outbox(item_id,draft_hash,draft,state,requested,updated) VALUES(?,?,?,'checking',?,?)",(post['id'],'x','x',time.time(),time.time()))
            require_open(c,post,dict(self.config,max_conversation_replies=1))
            c.execute("UPDATE outbox SET state='published' WHERE item_id=?",(post['id'],))
            with self.assertRaisesRegex(PilotError,'limit'):require_open(c,dict(post,id='another'),dict(self.config,max_conversation_replies=1))

    def test_expiry_erases_conversation_text(self):
        post=self.incoming();self.store.ingest(post)
        with self.store.db() as c:c.execute('UPDATE items SET created=?',(time.time()-8*86400,))
        self.store.expire(7)
        for row in self.store.items():
            self.assertNotIn('reply_to',row['payload']);self.assertNotIn('previous_reply',row['payload'])

    def test_resolved_requests_and_future_timestamps_do_not_get_priority(self):
        self.assertGreater(rank(self.rootpost)[0],rank(dict(self.rootpost,body='Solved, no longer need help'))[0])
        self.assertEqual(rank(self.rootpost)[0],rank(dict(self.rootpost,posted_at=time.time()+86400))[0])

    def test_overview_counts_whole_database_not_200_item_window(self):
        for i in range(240):self.store.ingest(dict(self.rootpost,id='t3_'+str(i)))
        result=overview(self.store)
        self.assertEqual(len(self.store.items()),200)
        self.assertEqual(result['totals']['stored'],241)
        self.assertEqual(result['totals']['discovered_24h'],241)
        self.assertEqual(result['totals']['published_24h'],1)
        self.assertFalse(result['attribution_connected'])

if __name__=='__main__':unittest.main()
