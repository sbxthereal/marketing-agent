import json
from pathlib import Path
import tempfile
import time
import unittest

from pilot.core import Store, PilotError
from pilot.workflow import Workflow, draft_hash, comment_permalink, shortlist


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.db'); self.flow=Workflow(self.store)
        self.config={'source':'browser','subreddits':['pilottest'],'publishing':{'enabled':True,'permission_confirmed':True,'permission_reference':'LOCAL TEST ONLY','reddit_username':'pilot_test','min_interval_seconds':1,'max_per_day':3}}
        self.post={'id':'t3_abc123','kind':'post','subreddit':'pilottest','url':'https://www.reddit.com/r/pilottest/comments/abc123/help/','title':'Need help with a website','body':'Example'}
        self.draft='I work on MeDo. A reviewed test reply.'
        self.store.ingest(self.post)
        self.store.complete(self.post['id'],{'decision':'recommend','draft':self.draft})
        self.store.review(self.post['id'],'approve')

    def request(self):
        self.flow.request_publish(self.post['id'],draft_hash(self.draft),self.config)

    def test_approval_does_not_schedule_a_send(self):
        self.assertIsNone(self.flow.claim_publication(self.config))

    def test_profile_reassignment_cannot_reroute_an_existing_attempt(self):
        self.config['browser_backend']='ixbrowser'
        self.config['ixbrowser']={'profiles':[{'id':1,'reddit_username':'pilot_test','subreddits':['pilottest']}]}
        self.request()
        self.config['ixbrowser']['profiles'][0]['reddit_username']='another_account'
        self.assertIsNone(self.flow.claim_publication(self.config))
        self.assertEqual(self.flow.outbox()[0]['state'],'blocked')
        self.assertEqual(self.flow.outbox()[0]['reddit_username'],'pilot_test')
        with self.assertRaisesRegex(PilotError,'account changed'):
            self.flow.retry_preflight(self.post['id'],draft_hash(self.draft),self.config)

    def test_publishing_disabled_and_stale_draft_rejected(self):
        self.config['publishing']['enabled']=False
        with self.assertRaises(PilotError): self.request()
        self.config['publishing']['enabled']=True
        with self.assertRaises(PilotError): self.flow.request_publish(self.post['id'],'old-hash',self.config)

    def test_exclusive_claim_and_duplicate_enqueue(self):
        self.request()
        self.assertIsNotNone(self.flow.claim_publication(self.config))
        self.assertIsNone(Workflow(Store(self.store.path)).claim_publication(self.config))
        with self.assertRaises(PilotError): self.request()

    def test_crash_after_send_marker_never_retries(self):
        self.request(); self.flow.claim_publication(self.config)
        self.flow.mark_submitting(self.post['id'])
        restarted=Workflow(Store(self.store.path)); restarted.recover()
        self.assertEqual(restarted.outbox()[0]['state'],'uncertain')
        self.assertIsNone(restarted.claim_publication(self.config))

    def test_uncertain_outcome_never_automatically_retries(self):
        self.request(); self.flow.claim_publication(self.config)
        self.flow.mark_submitting(self.post['id']); self.flow.publication_failed(self.post['id'],'timeout')
        self.assertEqual(self.flow.outbox()[0]['state'],'uncertain')
        with self.assertRaises(PilotError):self.flow.retry_preflight(self.post['id'],draft_hash(self.draft),self.config)

    def test_only_pre_submit_failure_can_be_explicitly_retried(self):
        self.request();self.flow.claim_publication(self.config)
        self.flow.publication_failed(self.post['id'],'Editor unavailable')
        self.assertEqual(self.flow.outbox()[0]['state'],'blocked')
        self.flow.retry_preflight(self.post['id'],draft_hash(self.draft),self.config)
        self.assertIsNotNone(self.flow.claim_publication(self.config))

    def test_outbox_content_expires_but_dedup_remains(self):
        self.request()
        with self.store.db() as c:c.execute('UPDATE outbox SET requested=?',(time.time()-8*86400,))
        self.flow.expire(7)
        with self.store.db() as c:row=c.execute('SELECT * FROM outbox').fetchone()
        self.assertEqual(row['draft'],'');self.assertEqual(row['state'],'expired')
        with self.assertRaises(PilotError):self.request()

    def test_rejection_and_pause_checked_immediately_before_submit(self):
        self.request(); self.flow.claim_publication(self.config)
        self.store.set_setting('paused','1')
        with self.assertRaises(PilotError): self.flow.mark_submitting(self.post['id'])
        self.store.set_setting('paused','0'); self.store.review(self.post['id'],'reject')
        with self.assertRaises(PilotError): self.flow.mark_submitting(self.post['id'])

    def test_post_link_and_other_thread_cannot_count_as_publication(self):
        self.assertFalse(comment_permalink(self.post['url'],self.post,['pilottest']))
        self.assertFalse(comment_permalink('https://www.reddit.com/r/pilottest/comments/wrong/help/c123/',self.post,['pilottest']))
        self.assertTrue(comment_permalink(self.post['url']+'c123/',self.post,['pilottest']))

    def test_verified_comment_updates_queue_atomically(self):
        self.request();job=self.flow.claim_publication(self.config);self.flow.mark_submitting(self.post['id'])
        self.flow.publication_done(job,self.post['url']+'c123/',self.config)
        self.assertEqual(self.store.items()[0]['status'],'published')
        self.assertEqual(self.flow.outbox()[0]['state'],'published')

    def test_scan_metrics_separate_seen_and_new(self):
        self.assertTrue(self.flow.seen(self.post['id']))
        self.flow.record_scan('pilottest',time.time(),1,12,2,10,'complete')
        self.assertEqual(self.flow.metrics()['duplicates'],10)

    def test_catalog_shortlist_does_not_require_keyword_hit(self):
        cat=[{'id':'a','keywords':['booking']},{'id':'b','keywords':['website']}]
        self.assertEqual(shortlist(self.post,cat)[0]['id'],'b')
        self.assertEqual(len(shortlist(dict(self.post,title='Unfamiliar words',body=''),cat)),2)


if __name__=='__main__': unittest.main()
