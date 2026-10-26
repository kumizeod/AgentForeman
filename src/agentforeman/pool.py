"""Credential-free durable work pool. One authenticated member, one active lease."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time

NAME = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Invalid member/task/group name')
    return value


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def validate(value, schema):
    """Strict subset used by our council protocol, validated at both endpoints."""
    kind = schema['type']
    ok = {'object': isinstance(value, dict), 'array': isinstance(value, list),
          'string': isinstance(value, str), 'integer': type(value) is int,
          'boolean': type(value) is bool}.get(kind, False)
    if not ok:
        raise ValueError('Output has invalid type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Output is outside enum')
    if kind == 'object':
        props = schema['properties']
        if set(value) != set(schema['required']) or set(value) - set(props):
            raise ValueError('Output keys do not match schema')
        for k, v in value.items():
            validate(v, props[k])
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100):
            raise ValueError('Output list length out of bounds')
        for v in value:
            validate(v, schema['items'])
    elif kind == 'string' and (not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 32000)
                               or (schema.get('minLength', 0) and not value.strip())):
        raise ValueError('Output string length out of bounds')
    elif kind == 'integer' and not schema.get('minimum', -10**9) <= value <= schema.get('maximum', 10**9):
        raise ValueError('Output number out of bounds')
    return value


class Pool:
    def __init__(self, project: Path):
        self.root = Path(project).resolve() / '.foreman'
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'pool.sqlite3'
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS members (
              id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL DEFAULT 1, not_before REAL NOT NULL DEFAULT 0,
              last_seen REAL, description TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, group_id TEXT NOT NULL, stage TEXT NOT NULL,
              eligible TEXT NOT NULL, request TEXT NOT NULL, deadline REAL NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued', member TEXT, session TEXT, lease TEXT,
              lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0, result TEXT,
              created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS settlements (lease TEXT PRIMARY KEY, member TEXT NOT NULL, task TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS pool_ready ON tasks(status, created);
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def register(self, member, token, description=''):
        name(member)
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError('Worker token must have at least 32 characters')
        if not isinstance(description, str) or len(description) > 200:
            raise ValueError('Description must be bounded')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as c:
            # Rotation must not change ownership of existing tasks.
            c.execute('INSERT INTO members(id,token_hash,description) VALUES(?,?,?) '
                      'ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash, description=excluded.description',
                      (member, digest, description))

    def authenticate(self, token):
        if not isinstance(token, str):
            return None
        with self.connect() as c:
            row = c.execute('SELECT id FROM members WHERE token_hash=? AND enabled=1',
                            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return row['id'] if row else None

    def members(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,enabled,not_before,last_seen,description FROM members ORDER BY id')]

    def enqueue(self, group, stage, eligible, request, deadline, task_id=None):
        name(group); name(stage)
        if not eligible or len(eligible) > 32 or len(set(eligible)) != len(eligible):
            raise ValueError('Eligible members must be distinct and nonempty')
        for member in eligible:
            name(member)
        if not math.isfinite(deadline) or deadline <= time.time():
            raise ValueError('Task deadline must be in the future')
        if set(request) != {'prompt', 'schema'} or not isinstance(request['prompt'], str) or len(dumps(request)) > 800000:
            raise ValueError('Invalid bounded request')
        tid = name(task_id or secrets.token_hex(12))
        with self.connect() as c:
            known = {r[0] for r in c.execute('SELECT id FROM members WHERE enabled=1')}
            if set(eligible) - known:
                raise ValueError('Unknown/disabled member')
            old = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if old:
                if (old['group_id'], old['stage'], old['eligible'], old['request'], old['deadline']) != (group, stage, dumps(eligible), dumps(request), deadline):
                    raise ValueError('Task id already has a different immutable request')
                return tid
            c.execute('INSERT INTO tasks(id,group_id,stage,eligible,request,deadline,created) VALUES(?,?,?,?,?,?,?)',
                      (tid, group, stage, dumps(eligible), dumps(request), deadline, time.time()))
        return tid

    def claim(self, member, session, lease_seconds=90):
        name(session)
        now = time.time()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            m = c.execute('SELECT * FROM members WHERE id=? AND enabled=1', (member,)).fetchone()
            if not m:
                raise ValueError('Unknown/disabled member')
            c.execute('UPDATE members SET last_seen=? WHERE id=?', (now, member))
            c.execute("UPDATE tasks SET status='expired' WHERE deadline<=? AND status IN ('queued','leased','uncertain')", (now,))
            c.execute("UPDATE tasks SET status='uncertain' WHERE status='leased' AND lease_until<?", (now,))
            # A lost heartbeat never grants another machine permission to replay.
            active = c.execute("SELECT * FROM tasks WHERE member=? AND status IN ('leased','uncertain')", (member,)).fetchone()
            if active:
                return self._wire(active) if active['session'] == session else None
            if m['not_before'] > now:
                return None
            for row in c.execute("SELECT * FROM tasks WHERE status='queued' AND deadline>? ORDER BY created,id", (now,)):
                if member not in json.loads(row['eligible']):
                    continue
                used = c.execute("SELECT 1 FROM tasks WHERE group_id=? AND stage=? AND member=? AND status IN ('leased','uncertain','completed','failed')",
                                 (row['group_id'], row['stage'], member)).fetchone()
                if used:
                    continue
                c.execute("UPDATE tasks SET status='leased',member=?,session=?,lease=?,lease_until=?,attempts=attempts+1 WHERE id=?",
                          (member, session, secrets.token_hex(24), now + lease_seconds, row['id']))
                return self._wire(c.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())
        return None

    @staticmethod
    def _wire(row):
        return {**{k: row[k] for k in ('id','group_id','stage','status','lease','attempts','deadline')},
                'request': json.loads(row['request'])}

    def heartbeat(self, member, task, lease):
        with self.connect() as c:
            changed = c.execute("UPDATE tasks SET status='leased',lease_until=? WHERE id=? AND member=? AND lease=? AND status IN ('leased','uncertain') AND deadline>?",
                                (time.time()+90, task, member, lease, time.time())).rowcount
            if changed:
                c.execute('UPDATE members SET last_seen=? WHERE id=?', (time.time(),member))
        return bool(changed)

    def complete(self, member, task, lease, outcome, result=None, retry_at=None):
        if outcome not in {'success','quota','failed','uncertain'}:
            raise ValueError('Unknown completion outcome')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            payload = dumps({'outcome':outcome,'result':result,'retry_at':retry_at})
            settled = c.execute('SELECT * FROM settlements WHERE lease=?', (lease,)).fetchone()
            if settled:
                if (settled['member'],settled['task'],settled['payload']) != (member,task,payload):
                    raise ValueError('Conflicting completion receipt')
                return True
            if outcome == 'quota' and (type(retry_at) not in (int,float) or not math.isfinite(retry_at) or retry_at <= time.time()):
                raise ValueError('Quota completion needs a future reset')
            row = c.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
            if not row or row['member'] != member or not hmac.compare_digest(row['lease'] or '', lease):
                raise ValueError('Completion does not own the lease')
            if row['status'] == 'completed' and outcome == 'success' and row['result'] == dumps(result):
                return True  # An interrupted HTTP acknowledgment is safe to resend.
            if row['status']=='expired':
                c.execute('INSERT INTO settlements VALUES(?,?,?,?)', (lease,member,task,payload))
                return True  # Acknowledge late delivery without accepting its output.
            if row['status'] not in {'leased','uncertain'}:
                raise ValueError('Task is already settled')
            if outcome == 'success':
                validate(result, json.loads(row['request'])['schema'])
            if row['deadline'] <= time.time():
                c.execute("UPDATE tasks SET status='expired' WHERE id=?", (task,))
            elif outcome == 'quota':
                c.execute('UPDATE members SET not_before=MAX(not_before,?) WHERE id=?', (retry_at, member))
                c.execute("UPDATE tasks SET status=?,member=NULL,session=NULL,lease=NULL,lease_until=NULL WHERE id=?",
                          ('queued' if row['attempts'] < 5 else 'failed', task))
            else:
                status = {'success':'completed','failed':'failed','uncertain':'uncertain'}[outcome]
                c.execute('UPDATE tasks SET status=?,result=? WHERE id=?', (status, dumps(result) if outcome=='success' else None, task))
            if outcome != 'uncertain':
                c.execute('INSERT INTO settlements VALUES(?,?,?,?)', (lease,member,task,payload))
        return True

    def tasks(self, group=None):
        with self.connect() as c:
            rows = c.execute('SELECT * FROM tasks'+(' WHERE group_id=?' if group else '')+' ORDER BY created,id', (group,) if group else ())
            return [{k: (json.loads(r[k]) if k in {'eligible','request','result'} and r[k] is not None else r[k])
                     for k in r.keys() if k not in {'session','lease'}} for r in rows]
