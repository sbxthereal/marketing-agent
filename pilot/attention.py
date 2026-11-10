"""Persistent human handoff; browser checks run only on the worker thread."""
import json
import time
import uuid

from .core import PilotError


class Attention:
    def __init__(self, store):
        self.store = store

    def current(self):
        raw = self.store.setting('browser_attention')
        return json.loads(raw) if raw else None

    @staticmethod
    def _read(c):
        row = c.execute("SELECT value FROM settings WHERE key='browser_attention'").fetchone()
        return json.loads(row[0]) if row and row[0] else None

    @staticmethod
    def _write(c, value):
        c.execute("INSERT OR REPLACE INTO settings VALUES ('browser_attention',?)",
                  (json.dumps(value) if value else '',))

    def record(self, details, message):
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            previous = self._read(c)
            value = dict(details, id=uuid.uuid4().hex, state='blocked',
                         detected_at=time.time(), last_checked_at=None, message=message)
            if previous:
                # Preserve the first blocked session until it is explicitly resolved.
                value = dict(previous, state='blocked', message=message)
            self._write(c, value)
            c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','1')")
            c.execute("INSERT OR REPLACE INTO settings VALUES ('message',?)", (message,))
        return value

    def request_check(self, ident):
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            value = self._read(c)
            if not value or value['id'] != ident:
                raise PilotError('This alert changed; refresh before checking it')
            if value['state'] == 'checking':
                return
            value.update(state='checking', check_id=uuid.uuid4().hex, message='Checking the same browser session…')
            self._write(c, value)
            c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','1')")

    def pause(self):
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            value = self._read(c)
            if value:
                value.update(state='blocked', message='Paused by reviewer; check again when ready')
                self._write(c, value)
            c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','1')")
            c.execute("INSERT OR REPLACE INTO settings VALUES ('message','Paused by reviewer')")

    def resume(self):
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if self._read(c):
                raise PilotError('Browser verification is pending; use Check and resume on the alert')
            if c.execute("SELECT 1 FROM outbox WHERE state='uncertain' LIMIT 1").fetchone():
                raise PilotError('An uncertain reply needs reconciliation before resuming')
            c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','0')")

    def finish(self, ident, check_id, error=None):
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            value = self._read(c)
            # A reviewer pause or a stale response cannot resume the worker.
            if not value or value['id'] != ident or value['state'] != 'checking' or value.get('check_id') != check_id:
                return False
            if error:
                value.update(state='blocked', last_checked_at=time.time(), message=error)
                self._write(c, value)
                c.execute("INSERT OR REPLACE INTO settings VALUES ('message',?)", (error,))
                return False
            if c.execute("SELECT 1 FROM outbox WHERE state='uncertain' LIMIT 1").fetchone():
                value.update(state='blocked', last_checked_at=time.time(),
                             message='Browser access works. An uncertain reply still needs reconciliation before resuming.')
                self._write(c, value)
                return False
            self._write(c, None)
            c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','0')")
            c.execute("INSERT OR REPLACE INTO settings VALUES ('gateway_failures','0')")
            c.execute("INSERT OR REPLACE INTO settings VALUES ('message','Browser access verified; discovery can continue. Blocked publication attempts still require review.')")
            return True

    def recover(self):
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            value = self._read(c)
            if value:
                value.update(state='blocked', message='Worker restarted. Solve in the original profile, then check access again.')
                self._write(c, value)
                c.execute("INSERT OR REPLACE INTO settings VALUES ('paused','1')")
