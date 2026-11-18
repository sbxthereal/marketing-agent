"""Comment-targeted readers/composers shared by discovery and conversations."""
import re
import time
from urllib.parse import urljoin, urlparse

from .core import PilotError
from .conversations import target_post, thread_url, require_open


FORUM_COMMENTS = r'''({selector,kind}) => {
 const own=(e,q)=>[...e.querySelectorAll(q)].filter(x=>x.closest(selector)===e);
 const id=e=>kind==='discourse'?(e.querySelector('article[id^="post_"]')?.id || e.id):
   (own(e,'a[href]').map(a=>new URL(a.href).hash.slice(1)).find(x=>/^comment[-_][a-zA-Z0-9]+$/.test(x)) || '');
 return [...document.querySelectorAll(selector)].slice(0,100).map(e=>{
  const parent=e.parentElement?.closest(selector);
  const explicit=e.getAttribute('data-parent-id')||e.getAttribute('data-comment-parent-id');
  const number=e.getAttribute('data-reply-to-post-number')||e.querySelector('[data-reply-to-post-number]')?.getAttribute('data-reply-to-post-number');
  const explicitNode=explicit?document.getElementById(explicit)||document.querySelector('[data-comment-id="'+CSS.escape(explicit)+'"]'):null;
  return {id:id(e),dom_id:e.querySelector('article[id^="post_"]')?.id||e.id,
   parent_id:kind==='discourse'?(number?'post_'+number:''):(explicit?(explicitNode?id(explicitNode):''):(parent?id(parent):'article')),
   body:(own(e,'.comment__body,.cooked,.comment__content')[0]?.innerText||'').slice(0,10000),
   author:e.getAttribute('data-author')||own(e,'.username a,.comment__header a[href]')[0]?.getAttribute('href')||'',
   created:own(e,'time')[0]?.getAttribute('datetime')||'',links:own(e,'a[href]').map(a=>a.href)};
 }).filter(x=>x.id&&x.body);
}'''


def read_forum(reader, post):
    from .channels import published_url
    current=reader.enrich(dict(post,kind='post'))
    kind=reader.channel['type']; data=dict(id='post_1' if kind=='discourse' else 'article',title=current['title'],
        body=current['body'],author=current['author'],closed=False,comments=[])
    records=reader.page.evaluate(FORUM_COMMENTS,dict(selector=reader.selectors['comments'],kind=kind))
    for row in records:
        if row['id']==data['id']: continue
        author=row['author'].strip('/').split('/')[-1]
        urls=[u for u in row['links'] if published_url(u,dict(post,kind='comment'),reader.channel)]
        # A DEV fragment must identify this exact node, not a nested child's link.
        if kind!='discourse': urls=[u for u in urls if urlparse(u).fragment==row['id']]
        else: urls=[u for u in urls if urlparse(u).path.rstrip('/').split('/')[-1]==row['id'].removeprefix('post_')]
        if len(set(urls))!=1: continue
        data['comments'].append(dict(id=row['id'],dom_id=row['dom_id'],parent_id=row['parent_id'],body=row['body'],author=author,url=urls[0],created=row['created']))
    return data


def enrich_comment(reader, post):
    data=reader.read_thread(post)
    target=next((c for c in data['comments'] if c['id']==post['reply_to']['id']),None)
    if not target: raise PilotError('Incoming comment is no longer visible')
    if post.get('conversation_parent') and target['parent_id']!=post['reply_to']['parent_id']:
        raise PilotError('Incoming comment changed its conversation parent')
    return target_post(post,data,target)


def discover_comments(reader, posts):
    """A rotating, bounded sample of selected threads; never claims full coverage."""
    flow=reader.workflow
    if not flow or not reader.config.get('discover_comments',True): return []
    store=flow.store; suffix=reader.config.get('search_state_suffix','')
    now=time.time(); key='comments.last'+suffix
    if now-float(store.setting(key,'0'))<reader.config.get('comment_scan_seconds',1800): return []
    # Use persisted candidates even when the new-feed scan found no new threads.
    candidates=[r['payload'] for r in store.items() if r['payload'].get('platform','reddit')=='reddit' and
                r['payload']['kind']=='post' and r['payload']['subreddit'] in reader.config['subreddits'] and r['created']>now-86400]
    if not candidates: return []
    cursor=int(store.setting('comments.cursor'+suffix,'0')); post=candidates[cursor%len(candidates)]
    started=time.time(); new=0; output=[]
    # Advance even on a layout failure; one unavailable thread must not stall feeds.
    store.set_setting(key,now); store.set_setting('comments.cursor'+suffix,cursor+1)
    try: data=reader.read_thread(post)
    except Exception as exc:
        from .browser import BrowserAttention
        if isinstance(exc,BrowserAttention): raise
        flow.record_scan(post['subreddit'],started,1,0,0,0,'error',
                         str(exc) if isinstance(exc,PilotError) else 'Selected thread could not be read',source='selected-thread comments')
        return []
    from .discovery import timestamp
    for target in data['comments'][:100]:
        at=timestamp(target.get('created'))
        if not at or not 0<=now-at<=reader.config.get('lookback_hours',24)*3600 or flow.seen(target['id']): continue
        if target.get('author','').casefold()==reader.config.get('publishing',{}).get('reddit_username','').casefold(): continue
        item=target_post(dict(post,id=target['id'],discovery_source='Comments in a selected thread'),data,target)
        # Model qualification remains authoritative; low lexical scores are not dropped.
        if data.get('closed'): continue
        from .core import validate_post
        item=validate_post(item,reader.config); new+=int(store.ingest(item)); output.append(item)
    flow.record_scan(post['subreddit'],started,1,len(data['comments']),new,0,'partial',source='selected-thread comments')
    store.set_setting(key,now); store.set_setting('comments.cursor'+suffix,cursor+1)
    return output


def publish_comment(reader, job, flow, forum=False):
    post=job['post']; target=post['reply_to']; config=reader.config
    with flow.store.db() as c: require_open(c,post,config)
    current=reader.read_thread(post)
    if current['id']!=post.get('thread_id') or current.get('closed'):
        raise PilotError('Conversation thread changed or is closed')
    incoming=next((c for c in current['comments'] if c['id']==target['id']),None)
    if not incoming or any(incoming.get(k,'')!=target.get(k,'') for k in ('id','parent_id','body','author')):
        raise PilotError('Incoming comment changed since drafting; no stale response sent')
    identity=reader.identity() if forum else reader._account()
    if identity.casefold()!=job['reddit_username'].casefold():
        from .browser import BrowserAttention
        raise BrowserAttention('Wrong account for this conversation',{'kind':'account'})
    if identity.casefold()==target['author'].casefold(): raise PilotError('Do not reply to the connected account')
    # Recheck the verified parent text and account, not merely a DOM parent ID.
    if post.get('conversation_parent'):
        previous=next((c for c in current['comments'] if c['id']==target['parent_id']),None)
        if target['parent_id']==current['id']: previous=current
        if not previous or previous['author'].casefold()!=identity.casefold() or ' '.join(previous['body'].split())!=' '.join(post['previous_reply'].split()):
            raise PilotError('Previous team response changed or is missing')
    elif current['author'].casefold()==identity.casefold() or any(c['author'].casefold()==identity.casefold() or re.search(r'\bmedo\b',c['body'],re.I) for c in current['comments']):
        raise PilotError('Already participated in this thread; only verified direct follow-ups are allowed')
    def readback():
        data=reader.read_thread(post)
        for comment in data['comments']:
            if (comment['parent_id']==target['id'] and comment['author'].casefold()==identity.casefold()
                    and ' '.join(comment['body'].split())==' '.join(job['draft'].split())):
                return comment['url']
        return ''
    existing=next((c['url'] for c in current['comments'] if c['parent_id']==target['id'] and c['author'].casefold()==identity.casefold() and ' '.join(c['body'].split())==' '.join(job['draft'].split())),None)
    if existing: return existing
    ident=target['id']
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',ident): raise PilotError('Invalid comment target ID')
    if forum:
        node_id=incoming.get('dom_id','')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',node_id): raise PilotError('Stable comment DOM identity unavailable')
        scope=reader.page.locator('[id="'+node_id+'"]')
    else:
        scope=reader.page.locator('shreddit-comment[thingid="'+ident+'"],.thing.comment[data-fullname="'+ident+'"]')
    if scope.count()!=1: raise PilotError('Comment target is missing or ambiguous')
    trigger=scope.get_by_role('button',name=re.compile(r'^Reply$',re.I))
    if not trigger.count(): trigger=scope.locator('.reply-button a')
    if trigger.count()!=1: raise PilotError('Scoped reply control is unavailable or ambiguous')
    trigger.click()
    kind=reader.channel['type'] if forum else 'reddit'
    if kind=='discourse':
        # Discourse's global editor must explicitly expose the selected reply target.
        editor=reader.page.locator('#reply-control')
        number=ident.removeprefix('post_')
        if editor.count()!=1: raise PilotError('Conversation editor is unavailable')
        explicit=editor.get_attribute('data-reply-to-post-number')==number
        links=editor.locator('.reply-to a[href],.reply-details a[href]').evaluate_all('(xs)=>xs.map(x=>x.href)')
        if not explicit and not any(thread_url(u,'discourse')==thread_url(post['url'],'discourse') and urlparse(u).path.rstrip('/').split('/')[-1]==number for u in links):
            raise PilotError('Conversation editor does not expose the selected comment target')
    else: editor=scope
    selector=reader.selectors['reply'] if forum else 'shreddit-composer [contenteditable="true"],form.usertext textarea[name="text"]'
    box=editor.locator(selector).filter(visible=True)
    if box.count()!=1: raise PilotError('Exact comment reply editor is unavailable')
    get_text=lambda:box.input_value() if box.evaluate('(e)=>["TEXTAREA","INPUT"].includes(e.tagName)') else box.inner_text()
    if get_text().strip(): raise PilotError('Reply editor contains an existing draft; preserving it')
    box.fill(job['draft'])
    if ' '.join(get_text().split())!=' '.join(job['draft'].split()): raise PilotError('Reply editor changed the checked text')
    submit=editor.get_by_role('button',name=re.compile(r'^(Reply|Comment|Save|Submit)$',re.I)).filter(visible=True)
    # Restrict to the editor's form/composer so the original Reply trigger is excluded.
    form=box.locator('xpath=ancestor::*[self::form or self::shreddit-composer][1]')
    if form.count(): submit=form.locator('button[type="submit"],input[type="submit"]') if forum else form.get_by_role('button',name=re.compile(r'^(Comment|Save|Reply)$',re.I))
    if submit.count()!=1 or not submit.is_enabled(): raise PilotError('Comment submit control is ambiguous')
    reader.guard() if forum else reader._guard()
    if (reader.identity() if forum else reader._account()).casefold()!=identity.casefold(): raise PilotError('Account changed before reply')
    flow.mark_submitting(job['item_id'],config)
    submit.click()
    # Read from the source again; optimistic insertion alone is not success.
    for _ in range(6):
        url=readback()
        if url:
            reader.goto(url) if forum else reader._goto(url)
            if readback()==url: return url
            break
        reader.page.wait_for_timeout(500)
    raise PilotError('Conversation reply outcome is uncertain; no automatic resend')
