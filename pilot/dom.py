"""Read rendered Reddit DOM; no private endpoints, cookies or page internals."""
FEED = r'''() => {
 const nodes = [...document.querySelectorAll('shreddit-post, .thing.link')];
 return nodes.filter(e => !e.hasAttribute('is-ad') && !e.classList.contains('promoted'))
 .map(e => {
  const a=e.querySelector('a[slot="title"],a[id^="post-title"],a.title');
  const body=e.querySelector('[slot="text-body"], .md, [id$="-post-rtjson-content"]');
  return {href:e.getAttribute('permalink') || e.getAttribute('data-permalink') || a?.href || '',
   title:e.getAttribute('post-title') || a?.innerText || '', body:body?.innerText || '',
   created:e.getAttribute('created-timestamp') || e.querySelector('time')?.getAttribute('datetime') || '',
   author:e.getAttribute('author') || e.querySelector('a.author')?.innerText || '',
   sticky:e.hasAttribute('is-stickied') || e.classList.contains('stickied')};
 });
}'''

SEARCH = r'''() => [...document.querySelectorAll('a[id^="search-post-title-"]')].map(a=>{
 const unit=a.closest('[data-testid="sdui-post-unit"]') || a.parentElement;
 return {href:a.getAttribute('href'),title:a.innerText,body:'',
 created:unit.querySelector('time')?.getAttribute('datetime') || unit.querySelector('faceplate-timeago')?.getAttribute('ts') || ''};
})'''

THREAD = r'''() => {
 const post=document.querySelector('shreddit-post,.thing.link');
 const body=post?.querySelector('[slot="text-body"],.usertext-body .md,[id$="-post-rtjson-content"]');
 return {id:post?.getAttribute('id') || post?.getAttribute('data-fullname') || '',title:document.querySelector('h1')?.innerText || post?.getAttribute('post-title') || '',
 body:body?.innerText || '',
 author:post?.getAttribute('author') || post?.querySelector('a.author')?.innerText || '',
 created:post?.getAttribute('created-timestamp') || post?.querySelector('time')?.getAttribute('datetime') || '',
 locked:post?.hasAttribute('is-locked') || document.querySelector('.locked-infobar')!==null,
 archived:post?.hasAttribute('is-archived') || document.querySelector('.archived-infobar')!==null,
 comments:[...document.querySelectorAll('shreddit-comment,.thing.comment')].slice(0,250).map(e=>{
   const b=e.querySelector('[slot="comment"],.usertext-body .md');
   return {body:(b?.innerText || '').slice(0,5000),
     author:e.getAttribute('author') || e.querySelector('a.author')?.innerText || '',
     id:e.getAttribute('thingid') || e.getAttribute('data-fullname') || '',
     href:e.getAttribute('permalink') || e.querySelector('a.bylink')?.href || ''};
 }).filter(c=>c.body)};
}'''

ACCOUNT = r'''() => {
 const roots=[document];
 for(let i=0;i<roots.length;i++) for(const e of roots[i].querySelectorAll('*')) if(e.shadowRoot) roots.push(e.shadowRoot);
 const links=roots.flatMap(r=>[...r.querySelectorAll('#user-drawer-content a[href*="/user/"],shreddit-user-dropdown a[href*="/user/"],.user a[href*="/user/"],a[data-testid="profile-link"]')]);
 return [...new Set(links.map(a=>a.getAttribute('href')?.match(/\/user\/([^/]+)/)?.[1]).filter(Boolean))];
}'''
