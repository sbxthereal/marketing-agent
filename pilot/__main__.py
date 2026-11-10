import argparse
import importlib.util
import json
from pathlib import Path
import sys

from .core import PilotError, Store, load_catalog, load_config
from .platform_support import WorkerLock, chrome_executable, chrome_options


def main():
    p = argparse.ArgumentParser(description='MeDo Reddit pilot — incremental discovery and verified, reviewed browser replies')
    p.add_argument('command', choices=['serve', 'once', 'doctor', 'login', 'reconcile'], nargs='?', default='serve')
    p.add_argument('--item')
    p.add_argument('--url')
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        config = load_config(root)
        catalog = load_catalog(root, config)
        if args.command == 'doctor':
            from .gateway import read_env
            env = read_env(root / '.env')
            from .core import require_live_permission
            try:
                require_live_permission(config,catalog)
                readiness='configured'
            except PilotError as e:
                readiness=str(e)
            print(json.dumps({'python': sys.version.split()[0], 'platform':sys.platform,'source': config['source'], 'ai': config['ai'], 'model': config['model'], 'catalog_entries': len(catalog), 'gateway_key_configured': bool(env.get('ONEAPI_API_KEY')), 'playwright_installed': importlib.util.find_spec('playwright') is not None, 'browser_use_installed': importlib.util.find_spec('browser_use') is not None, 'chrome_installed': bool(chrome_executable()),'browser_headless':config.get('browser_headless',False), 'browser_readiness':readiness,'publishing_enabled':config.get('publishing',{}).get('enabled',False),'publishing_requires_explicit_review':True}, indent=2))
        elif args.command == 'login':
            from playwright.sync_api import sync_playwright
            (root/'runtime').mkdir(exist_ok=True,mode=0o700)
            with WorkerLock(root/'runtime'/'worker.lock'):
                with sync_playwright() as pw:
                    context=pw.chromium.launch_persistent_context(str(root/'runtime'/'chrome-profile'),**chrome_options())
                    page=context.pages[0] if context.pages else context.new_page()
                    page.goto('https://www.reddit.com/login/',wait_until='domcontentloaded')
                    input('Sign into the intended Reddit account in Chrome, then press Enter here to close the setup browser. ')
                    context.close()
        elif args.command in ('once','reconcile'):
            from .service import Worker
            (root / 'runtime').mkdir(exist_ok=True, mode=0o700)
            lock = WorkerLock(root / 'runtime' / 'worker.lock')
            store = Store(root / 'runtime' / 'pilot.sqlite3')
            worker = Worker(root, config, catalog, store)
            try:
                if args.command=='once':
                    worker.cycle()
                else:
                    from .workflow import require_publishing
                    from .core import require_live_permission
                    require_live_permission(config,catalog);require_publishing(config)
                    if not args.item or not args.url:
                        raise PilotError('Reconcile requires --item ID and --url COMMENT_PERMALINK')
                    with store.db() as c:
                        row=c.execute('SELECT o.*,i.payload FROM outbox o JOIN items i ON i.id=o.item_id WHERE item_id=?',(args.item,)).fetchone()
                    if not row or row['state'] not in ('uncertain','blocked') or not row['draft']:
                        raise PilotError('No unresolved publication attempt for this item')
                    job=dict(row,post=json.loads(row['payload']))
                    worker.ensure_browser()
                    url=worker.browser.verify_publication(job,args.url)
                    worker.flow.publication_done(job,url,config)
            finally:
                if worker.browser:
                    worker.browser.close()
                lock.close()
            print(json.dumps(store.summary(), indent=2))
        else:
            from .service import serve
            serve(root)
    except PilotError as e:
        print('Setup/action needed: ' + str(e), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
