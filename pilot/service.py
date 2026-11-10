from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import time
from urllib.parse import unquote, urlparse

from .core import PilotError, Store, load_catalog, load_config, require_live_permission, validate_post
from .gateway import Gateway, analyze, fixture_result
from .workflow import Workflow, draft_hash, shortlist, require_publishing
from .discovery import scan_delay, timestamp
from .quality import context_skip, hold
from .platform_support import WorkerLock


class Worker:
    def __init__(self, root, config, catalog, store):
        self.root, self.config, self.catalog, self.store = root, config, catalog, store
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.browser = None
        self.gateway = None
        self.fixtures = json.loads((root / config['fixtures_file']).read_text(encoding='utf-8-sig')) if config['source'] == 'demo' else []
        self.flow = Workflow(store)
        self.flow.recover()
        self.next_scan = 0
        self.idle_scans = 0

    def ensure_browser(self):
        if not self.browser:
            from .browser import BrowserReader
            self.browser = BrowserReader(self.root,self.config,self.catalog,self.flow)

    def publish_ready(self):
        if self.config['source'] != 'browser' or not self.config.get('publishing',{}).get('enabled'):
            return
        require_live_permission(self.config,self.catalog)
        job = self.flow.claim_publication(self.config)
        if not job:
            return
        try:
            self.ensure_browser()
            url = self.browser.publish(job,self.flow)
            self.flow.publication_done(job,url,self.config)
        except Exception as e:
            message = str(e) if isinstance(e,PilotError) else 'Browser publication failed; inspect the attempt before retrying'
            self.flow.publication_failed(job['item_id'],message)
            self.store.set_setting('message',message)
            from .browser import BrowserAttention
            if isinstance(e,BrowserAttention) or any(x['item_id']==job['item_id'] and x['state']=='uncertain' for x in self.flow.outbox()):
                self.store.set_setting('paused','1')

    def cycle(self):
        if self.store.setting('paused') == '1':
            return
        self.store.expire(self.config['retention_days'])
        self.flow.expire(self.config['retention_days'])
        self.publish_ready()
        if self.store.setting('paused') == '1':
            return
        if self.config['source'] == 'demo':
            posts = self.fixtures
        else:
            require_live_permission(self.config, self.catalog)
            self.ensure_browser()
            posts=[]
            if time.monotonic() >= self.next_scan:
                posts=self.browser.collect()
                self.idle_scans=0 if posts else self.idle_scans+1
                delay=scan_delay(self.config,self.idle_scans)
                self.next_scan=time.monotonic()+delay
                self.store.set_setting('next_scan_at',time.time()+delay)
        for post in posts:
            self.store.ingest(validate_post(post, self.config))
        if self.config['ai'] == 'oneapi' and not self.gateway:
            self.gateway = Gateway(self.root / '.env')
        for _ in range(self.config['max_items_per_cycle']):
            if self.stop.is_set() or self.store.setting('paused') == '1':
                break
            post = self.store.claim(self.config['max_attempts'])
            if not post:
                break
            try:
                if self.config['ai'] == 'fixture':
                    result = fixture_result(post, self.catalog, self.fixtures)
                else:
                    if self.config['source'] == 'browser':
                        self.ensure_browser()
                        enriched = self.browser.enrich(post)
                        if enriched.pop('closed',False):
                            self.store.complete(post['id'],{'decision':'skip','reason':'Thread is locked or archived','template_id':'','evidence':'','claim_ids':[],'draft':''})
                            continue
                        post = validate_post(enriched,self.config)
                        self.flow.enrich(post)
                        skip=context_skip(post,self.config.get('publishing',{}).get('reddit_username',''))
                        posted=timestamp(post.get('posted_at'))
                        if posted and time.time()-posted > max(self.config.get('lookback_hours',24),self.config.get('search_lookback_hours',72))*3600:
                            skip='Discussion is older than the discovery window'
                        if skip:
                            self.store.complete(post['id'],{'decision':'skip','reason':skip,'template_id':'','evidence':'','claim_ids':[],'draft':''})
                            continue
                    result = analyze(self.gateway, post, shortlist(post,self.catalog), self.config, self.store)
                if result is None:
                    self.store.defer_budget(post['id'])
                    self.store.set_setting('message', 'Daily model budget reached; new calls are paused until budget is available.')
                    break
                if result['decision']=='recommend' and self.flow.duplicate_draft(post['id'],result['draft']):
                    result=hold(result,'Draft is too similar to another recent reply; needs a more specific response')
                self.store.complete(post['id'], result)
                self.store.set_setting('gateway_failures', '0')
            except PilotError as e:
                self.store.fail(post['id'], str(e), self.config['max_attempts'])
                from .browser import BrowserAttention
                if isinstance(e,BrowserAttention):
                    raise
                if str(e).startswith('Gateway'):
                    failures = int(self.store.setting('gateway_failures', '0')) + 1
                    self.store.set_setting('gateway_failures', str(failures))
                    if str(e) in ('Gateway HTTP 401', 'Gateway HTTP 403', 'Gateway HTTP 429') or failures >= 3:
                        self.store.set_setting('paused', '1')
                        self.store.set_setting('message', 'Gateway access, quota, or repeated connection failure. Check connectivity and credentials before resuming.')
                        break
        self.store.set_setting('last_cycle', str(int(time.time())))

    def run(self):
        failures = 0
        try:
            while not self.stop.is_set():
                try:
                    self.cycle()
                    failures = 0
                except PilotError as e:
                    self.store.set_setting('message', str(e))
                    from .browser import BrowserAttention
                    if isinstance(e,BrowserAttention) or str(e).startswith(('Live collection','Live browser','Select approved','Configure ONEAPI','Record the publishing','Browser publishing')):
                        self.store.set_setting('paused', '1')
                    else:
                        failures += 1
                        if self.browser:
                            try:
                                self.browser.close()
                            except Exception:
                                pass
                            self.browser = None
                except Exception:
                    failures += 1
                    self.store.set_setting('message', 'Transient browser/worker error; retrying with backoff.')
                    if self.browser:
                        try:
                            self.browser.close()
                        except Exception:
                            pass
                        self.browser = None
                if failures >= 5:
                    self.store.set_setting('paused','1')
                    self.store.set_setting('message','Repeated browser failure; inspect before resuming.')
                delay = min(900,30*2**failures) if failures else self.config['poll_seconds']
                self.wake.wait(delay)
                self.wake.clear()
        finally:
            if self.browser:
                self.browser.close()


def server_for(root, config, store, worker=None):
    flow = Workflow(store)
    token = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    port = config['port']
    hosts = {'127.0.0.1:' + str(port), 'localhost:' + str(port)}
    origins = {'http://' + h for h in hosts}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # No request bodies, queries, credentials or post text in server logs.
            pass

        def respond(self, status, data, content_type='application/json'):
            body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'nonce-" + nonce + "'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get('Host') in hosts

        def do_GET(self):
            if not self.host_ok():
                return self.respond(403, {'error': 'Invalid host'})
            if self.path == '/':
                html = (root / 'pilot' / 'dashboard.html').read_text(encoding='utf-8-sig').replace('__TOKEN__', token).replace('__NONCE__', nonce)
                return self.respond(200, html, 'text/html')
            if self.path == '/api/status':
                return self.respond(200, dict(store.summary(), source=config['source'], ai=config['ai'], model=config['model'], poll_seconds=config['poll_seconds'], max_daily_calls=config['max_daily_calls'], max_daily_tokens=config['max_daily_tokens'], publishing='reviewed browser' if config.get('publishing',{}).get('enabled') else 'disabled', validation_snapshot=config.get('validation_snapshot',False), catalog=config['catalog_file'], efficiency=flow.metrics(), pacing=flow.pacing(config),next_scan_at=store.setting('next_scan_at')))
            if self.path == '/api/items':
                outbox = {r['item_id']:r for r in flow.outbox()}
                return self.respond(200, [dict(r,draft_hash=draft_hash(r['result']['draft']) if r['result'] else '',publication=outbox.get(r['id'])) for r in store.items()])
            return self.respond(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.host_ok() or self.headers.get('Origin') not in origins or not secrets.compare_digest(self.headers.get('X-Pilot-Token', ''), token):
                return self.respond(403, {'error': 'Unauthorized local request'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.respond(415, {'error': 'JSON required'})
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 4096:
                    raise PilotError('Invalid body size')
                payload = json.loads(self.rfile.read(n))
                if not isinstance(payload, dict):
                    raise PilotError('Invalid JSON object')
                if self.path == '/api/control':
                    if payload.get('action') not in ('pause', 'resume'):
                        raise PilotError('Unknown action')
                    if payload['action'] == 'resume' and config['source'] == 'browser':
                        require_live_permission(config, load_catalog(root, config))
                    store.set_setting('paused', '1' if payload['action'] == 'pause' else '0')
                    if payload['action'] == 'resume':
                        store.set_setting('gateway_failures', '0')
                    store.set_setting('message', 'Paused by reviewer' if payload['action'] == 'pause' else 'Resumes on the next scheduled cycle')
                elif self.path.startswith('/api/review/'):
                    item_id = unquote(self.path[len('/api/review/'):])
                    store.review(item_id, payload.get('action'), payload.get('publication_url', ''), config['subreddits'])
                elif self.path.startswith('/api/publish/'):
                    require_live_permission(config,load_catalog(root,config))
                    flow.request_publish(unquote(self.path[len('/api/publish/'):]),payload.get('draft_hash'),config)
                elif self.path.startswith('/api/retry/'):
                    require_live_permission(config,load_catalog(root,config))
                    flow.retry_preflight(unquote(self.path[len('/api/retry/'):]),payload.get('draft_hash'),config)
                else:
                    return self.respond(404, {'error': 'Not found'})
                if worker:
                    worker.wake.set()
                return self.respond(200, {'ok': True})
            except (ValueError, PilotError, TypeError) as e:
                return self.respond(400, {'error': str(e) if isinstance(e, PilotError) else 'Invalid request'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(root):
    os.umask(0o077)
    root = Path(root).resolve()
    config = load_config(root)
    catalog = load_catalog(root, config)
    runtime = root / 'runtime'
    runtime.mkdir(exist_ok=True, mode=0o700)
    lock = WorkerLock(runtime / 'worker.lock')
    store = Store(runtime / 'pilot.sqlite3')
    worker = Worker(root, config, catalog, store)
    server = server_for(root, config, store, worker)
    thread = threading.Thread(target=worker.run, name='pilot-worker', daemon=True)
    thread.start()

    def shutdown(*_):
        worker.stop.set()
        worker.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal,'SIGBREAK'):signal.signal(signal.SIGBREAK,shutdown)
    publishing='reviewed publication enabled' if config.get('publishing',{}).get('enabled') else 'publishing disabled'
    print('MeDo pilot ready: http://127.0.0.1:' + str(config['port']) + ' (' + config['source'] + '/' + config['ai'] + '; '+publishing+')', flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        worker.stop.set()
        worker.wake.set()
        thread.join(timeout=70)
        server.server_close()
        lock.close()
