"""Bounded OneAPI + Browser Use test on a local browser fixture; no Reddit traffic."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pilot.agent_browser import agent_discover
from pilot.gateway import read_env
from pilot.platform_support import chrome_options
from pilot.core import Store,load_config


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    env=read_env(args.env_file)
    for key in ('ONEAPI_BASE_URL','ONEAPI_API_KEY'):
        if env.get(key):os.environ[key]=env[key]
    os.environ['ANONYMIZED_TELEMETRY']='false'
    from playwright.sync_api import sync_playwright
    config=load_config(ROOT);config.update(browser_max_steps=3,max_daily_calls=4,max_daily_tokens=150000)
    url='https://www.reddit.com/r/pilottest/new/'
    page_html='''<!doctype html><title>LOCAL TEST: Reddit-like feed</title><h1>Local browser test feed</h1><article><h2><a href="https://www.reddit.com/r/pilottest/comments/abc123/help/">Need a portfolio website for my design projects</a></h2><p>I need a site to showcase my work and let clients contact me.</p></article>'''
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        root=Path(tmp);(root/'runtime').mkdir()
        store=Store(root/'runtime'/'probe.db')
        context=pw.chromium.launch_persistent_context(str(root/'profile'),**chrome_options(True),args=['--remote-debugging-port=0','--remote-debugging-address=127.0.0.1'])
        context.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body=page_html) if r.request.url==url else r.abort())
        page=context.pages[0];page.goto(url,wait_until='load')
        port=(root/'profile'/'DevToolsActivePort').read_text(encoding='utf-8-sig').splitlines()[0]
        started=time.monotonic()
        report={'kind':'local fixture plus real OneAPI; no Reddit network access'}
        try:
            posts=agent_discover('http://127.0.0.1:'+port,url,root,config,store,pump=lambda:page.wait_for_timeout(100))
            report.update(ok=any(p['href'].endswith('/abc123/help/') for p in posts),posts=posts)
        except Exception as e:
            report.update(ok=False,error=type(e).__name__+': '+str(e)[:300])
        finally:
            report.update(seconds=round(time.monotonic()-started,2),usage=store.summary()['usage'])
            context.close()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('ok','seconds','usage')}))
        return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
