"""Read-only operational metrics. No inferred registrations, leads or revenue."""
import time


def overview(store):
    now=time.time(); cutoff=now-86400
    platform="COALESCE(json_extract(i.payload,'$.platform'),'reddit')"
    queue="CASE WHEN json_extract(i.payload,'$.kind')='campaign' THEN 'posts' WHEN json_extract(i.payload,'$.conversation_parent') IS NOT NULL THEN 'conversations' ELSE 'opportunities' END"
    with store.db() as c:
        inventory=[dict(r) for r in c.execute(f"SELECT {queue} queue,i.status,COUNT(*) count FROM items i GROUP BY queue,i.status")]
        totals=dict(c.execute("""SELECT COUNT(*) stored,
          COALESCE(SUM(created>? AND json_extract(payload,'$.kind')!='campaign'),0) discovered_24h,
          COALESCE(SUM(created>? AND json_extract(payload,'$.conversation_parent') IS NOT NULL),0) incoming_24h,
          COALESCE(SUM(status='error'),0) errors,
          COALESCE(SUM(status='review' AND json_extract(result,'$.decision')='review'),0) held
          FROM items""",(cutoff,cutoff)).fetchone())
        totals['checked_24h']=c.execute("SELECT COUNT(*) FROM quality_receipts q JOIN items i ON i.id=q.item_id WHERE q.checked_at>? AND json_extract(i.result,'$.decision')='recommend'",(cutoff,)).fetchone()[0]
        totals['published_24h']=c.execute("SELECT COUNT(*) FROM outbox WHERE state='published' AND updated>?",(cutoff,)).fetchone()[0]
        totals['waiting']=c.execute("SELECT COUNT(*) FROM outbox WHERE state='ready'").fetchone()[0]
        totals['blocked']=c.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('blocked','uncertain')").fetchone()[0]
        sources=[dict(r) for r in c.execute("SELECT source,subreddit,COUNT(*) scans,SUM(found) seen,SUM(new_items) new_items,SUM(duplicates) duplicates,SUM(state IN ('error','coverage_capped','partial','blocked')) limited,MAX(started) last_scan FROM scans WHERE started>? GROUP BY source,subreddit ORDER BY last_scan DESC",(cutoff,))]
        scans=[dict(r) for r in c.execute('SELECT * FROM scans ORDER BY started DESC LIMIT 40')]
        models=[dict(r) for r in c.execute("SELECT model,COUNT(*) calls,SUM(status='failed') failed,SUM(input_tokens IS NULL OR output_tokens IS NULL) unknown_usage,COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens,ROUND(AVG(latency_ms)) latency_ms FROM calls WHERE created>? GROUP BY model",(cutoff,))]
        platforms=[dict(r) for r in c.execute(f"SELECT {platform} platform,COUNT(*) stored,SUM(i.created>? AND json_extract(i.payload,'$.kind')!='campaign') discovered_24h,SUM(o.state='published' AND o.updated>?) published_24h,SUM(i.created>? AND json_extract(i.payload,'$.conversation_parent') IS NOT NULL) incoming_24h FROM items i LEFT JOIN outbox o ON o.item_id=i.id GROUP BY platform",(cutoff,cutoff,cutoff))]
        watches=[dict(r) for r in c.execute(f"SELECT w.*,json_extract(i.payload,'$.title') title,{platform} platform,o.permalink FROM conversation_checks w JOIN items i ON i.id=w.item_id LEFT JOIN outbox o ON o.item_id=w.item_id ORDER BY checked DESC LIMIT 40")]
        publications=[dict(r) for r in c.execute(f"SELECT o.item_id,o.state,o.updated,o.error,o.permalink,o.reddit_username,{platform} platform,json_extract(i.payload,'$.title') title FROM outbox o JOIN items i ON i.id=o.item_id ORDER BY o.updated DESC LIMIT 30")]
    return dict(generated_at=now,window_hours=24,totals=totals,inventory=inventory,sources=sources,scans=scans,
                models=models,platforms=platforms,conversations=watches,publications=publications,
                item_list_limit=200,attribution_connected=False,
                notes=['Discovered discussions are not qualified leads.', 'Counts use the stored database; item lists show the newest 200 records.',
                       'Sources and conversation checks cover bounded samples, not every post or reply.', 'Registrations, activation, revenue and currency costs are not connected.'])
