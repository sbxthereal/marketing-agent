"""Bounded monitoring of verified publications and direct incoming replies."""
import hashlib
import json
import re
import time
from urllib.parse import urlparse, urlunparse

from .core import PilotError, publisher_for, validate_post


def thread_url(url, kind='reddit'):
    u = urlparse(url)
    parts = u.path.strip('/').split('/')
    if kind == 'reddit': parts = parts[:5]
    elif kind == 'discourse': parts = parts[:3]
    return urlunparse((u.scheme, u.netloc, '/'+'/'.join(parts)+'/', '', '', ''))


def message_id(url, kind='reddit', campaign=False):
    u = urlparse(url); parts = u.path.strip('/').split('/')
    if kind == 'reddit':
        return ('t3_'+parts[3]) if campaign and len(parts) >= 4 else ('t1_'+parts[5]) if len(parts) == 6 else ''
    if kind == 'discourse': return 'post_'+('1' if campaign else parts[3]) if campaign or len(parts)==4 else ''
    return 'article' if campaign else u.fragment


def stop_reason(text):
    if re.search(r"\b(stop (?:replying|contacting|messaging|promoting)|(?:don't|do not|please stop) (?:reply|respond|contact|message|advertise)|no (?:more )?(?:ads|promotion|promotions)|not interested)\b", text, re.I):
        return 'The participant declined further promotion or replies'
    if re.fullmatch(r'(?:thanks|thank you|ok|okay|got it|cheers|cool|nice)[!.,\s]*', text.strip(), re.I):
        return 'Acknowledgment only; no further reply needed'
    return ''


def target_post(post, data, target):
    if not target.get('id') or not target.get('parent_id') or not target.get('author') or not target.get('body'):
        raise PilotError('Comment identity, parent or author is unavailable')
    if target['body'].strip() in ('[deleted]', '[removed]') or target['author'] in ('[deleted]', '[removed]'):
        raise PilotError('The incoming comment is unavailable')
    return dict(post, kind='comment', title=data['title'][:1000], body=target['body'][:10000],
                original_body=target['body'][:10000], author=target['author'][:100], posted_at=target.get('created',''),
                discussion=[], thread_id=data['id'], thread_body=data['body'][:5000],
                reply_to={k:str(target.get(k,''))[:10000 if k=='body' else 2048 if k=='url' else 100] for k in ('id','parent_id','body','author','url','created')},
                closed=bool(data.get('closed')))


def verify_receipt(c, post, config):
    """A flag from page/model text cannot authorize a conversation exception."""
    parent = post.get('conversation_parent')
    if not parent: return False
    row = c.execute("SELECT o.*,i.payload FROM outbox o JOIN items i ON i.id=o.item_id WHERE item_id=? AND o.state='published'", (parent,)).fetchone()
    if not row or not row['permalink'] or not row['draft']: raise PilotError('Conversation parent has no verified publication receipt')
    previous = json.loads(row['payload']); kind = post.get('platform_type','reddit')
    if (previous.get('platform','reddit') != post.get('platform','reddit') or previous['subreddit'] != post['subreddit'] or
            row['reddit_username'].casefold() != publisher_for(config,post['subreddit']).casefold() or
            thread_url(row['permalink'],kind) != thread_url(post['url'],kind) or
            post.get('reply_to',{}).get('parent_id') != message_id(row['permalink'],kind,previous['kind']=='campaign') or
            post.get('previous_reply') != row['draft']):
        raise PilotError('Conversation receipt, target, account or previous reply changed')
    return True


def ensure_tables(store):
    with store.db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS conversation_checks (
          item_id TEXT PRIMARY KEY, checked REAL NOT NULL, state TEXT NOT NULL,
          found INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS conversation_stops (
          thread TEXT PRIMARY KEY, created REAL NOT NULL, reason TEXT NOT NULL);
        ''')


def watch(store, flow, browser, config):
    if not config.get('conversation_monitoring',True) or not config.get('publishing',{}).get('enabled'): return
    ensure_tables(store)
    now=time.time(); platform=config.get('platform','reddit'); kind=config.get('channel',{}).get('type','reddit')
    with store.db() as c:
        c.execute('DELETE FROM conversation_checks WHERE checked<?',(now-config.get('retention_days',7)*86400,))
        c.execute('DELETE FROM conversation_stops WHERE created<?',(now-30*86400,))
        rows=c.execute("""SELECT o.*,i.payload FROM outbox o JOIN items i ON i.id=o.item_id
          LEFT JOIN conversation_checks w ON w.item_id=o.item_id
          WHERE o.state='published' AND o.permalink IS NOT NULL AND o.draft!='' AND o.updated>?
          AND COALESCE(json_extract(i.payload,'$.platform'),'reddit')=? AND COALESCE(w.checked,0)<?
          ORDER BY COALESCE(w.checked,0),o.updated DESC LIMIT 2""",
          (now-min(7,config.get('retention_days',7))*86400,platform,now-config.get('conversation_poll_seconds',900))).fetchall()
    for row in rows:
        post=json.loads(row['payload']); url=thread_url(row['permalink'],kind)
        key=platform+':'+url
        state,error,new='watching','',0
        try:
            with store.db() as c:
                if c.execute('SELECT 1 FROM conversation_stops WHERE thread=?',(key,)).fetchone():
                    state='closed'; continue
            expected=publisher_for(config,post['subreddit'])
            if not expected or expected.casefold()!=row['reddit_username'].casefold():
                raise PilotError('Published conversation belongs to a different configured account')
            base=dict(post,url=url,kind='post'); base.pop('reply_to',None)
            data=browser.read_thread(base)
            if data.get('closed'): state='closed'; continue
            own_id=message_id(row['permalink'],kind,post['kind']=='campaign')
            own = dict(id=data['id'],body=data['body'],author=data['author']) if post['kind']=='campaign' else next((x for x in data['comments'] if x['id']==own_id),None)
            if not own or own['author'].casefold()!=expected.casefold() or ' '.join(own['body'].split())!=' '.join(row['draft'].split()):
                raise PilotError('Published message is missing or changed; no automatic follow-up')
            for target in data['comments']:
                if target.get('parent_id')!=own_id or target.get('author','').casefold()==expected.casefold(): continue
                ident=target['id'] if platform=='reddit' else platform+':comment:'+hashlib.sha256((url+target['id']).encode()).hexdigest()[:32]
                if flow.seen(ident): continue
                incoming=target_post(dict(base,id=ident,conversation_parent=row['item_id'],previous_reply=row['draft'],
                         discovery_source='Direct reply to a verified MeDo message'),data,target)
                incoming=validate_post(incoming,config)
                with store.db() as c: verify_receipt(c,incoming,config)
                new+=int(store.ingest(incoming))
                reason=stop_reason(target['body'])
                if reason:
                    store.complete(ident,dict(decision='skip',reason=reason,template_id='',evidence='',claim_ids=[],draft=''))
                    if not reason.startswith('Acknowledgment'):
                        with store.db() as c: c.execute('INSERT OR REPLACE INTO conversation_stops VALUES (?,?,?)',(key,now,reason))
                        state='closed'; break
            flow.record_scan(post['subreddit'],now,1,len(data['comments']),new,0,'partial',source='conversation replies')
        except Exception as exc:
            from .browser import BrowserAttention
            state='attention' if isinstance(exc,BrowserAttention) else 'error'
            error=str(exc)[:300] if isinstance(exc,PilotError) else 'Conversation page could not be read'
            if isinstance(exc,BrowserAttention): raise
        finally:
            with store.db() as c:
                c.execute('INSERT OR REPLACE INTO conversation_checks VALUES (?,?,?,?,?)',(row['item_id'],now,state,new,error))


def require_open(c, post, config):
    if not verify_receipt(c,post,config): return
    key=post.get('platform','reddit')+':'+thread_url(post['url'],post.get('platform_type','reddit'))
    if c.execute('SELECT 1 FROM conversation_stops WHERE thread=?',(key,)).fetchone():
        raise PilotError('This conversation is closed after an opt-out')
    rows=c.execute("SELECT i.payload FROM outbox o JOIN items i ON i.id=o.item_id WHERE o.state IN ('published','uncertain','checking','submitting') AND o.item_id!=? AND json_extract(i.payload,'$.conversation_parent') IS NOT NULL",(post['id'],)).fetchall()
    turns=sum(1 for row in rows if (p:=json.loads(row['payload'])).get('platform','reddit')==post.get('platform','reddit') and thread_url(p['url'],p.get('platform_type','reddit'))==thread_url(post['url'],post.get('platform_type','reddit')))
    if turns>=config.get('max_conversation_replies',4): raise PilotError('Conversation reply limit reached')
