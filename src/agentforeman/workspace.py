"""Read-only task inbox, connection usage and versioned dispatch preferences."""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .files import atomic_json
from .observe import documents, model_jobs, job_usage, optional_json
from .platforms import file_lock

KINDS = ('briefings', 'debates', 'cooperations', 'councils', 'coding', 'campaigns')
ID = re.compile(r'^[0-9a-f]{12}$')


def connections(project, *, include_deleted=False):
    return [{**{k: s.get(k) for k in ('id', 'label', 'provider', 'model')}, 'member': s.get('member') or 'local_'+s['id']}
            for p in (Path(project)/'.foreman/connections').glob('*/connection.json')
            if not p.is_symlink() for s in [optional_json(p)] if ID.fullmatch(str(s.get('id', ''))) and (include_deleted or not s.get('deleted_at'))]


def preferences(project):
    saved = optional_json(Path(project)/'.foreman/dispatch-preferences.json')
    rows = connections(project)
    weights = saved.get('weights', {}); configured = bool(weights)
    for i, row in enumerate(rows):
        row['target_share'] = weights.get(row['id'], 0) if configured else (100//len(rows) + int(i < 100 % len(rows)))
    return {'version': saved.get('version', 0), 'connections': rows, 'configured': configured}


def save_preferences(project, *, version, weights):
    if type(version) is not int or not isinstance(weights, dict) or not weights:
        raise ValueError('Provide preference version and connection shares')
    if any(not isinstance(k, str) or not ID.fullmatch(k) or type(v) is not int or not 0 <= v <= 100 for k, v in weights.items()) or sum(weights.values()) != 100:
        raise ValueError('Connection shares must be integers totaling 100%')
    path = Path(project)/'.foreman/dispatch-preferences.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix('.lock')):
        current = preferences(project)
        if version != current['version']:
            raise ValueError('Preferences changed; refresh before saving')
        if set(weights) != {r['id'] for r in current['connections']}:
            raise ValueError('Connections changed; refresh before saving preferences')
        atomic_json(path, {'version': version+1, 'weights': weights})
    return preferences(project)


def remove_connection_preference(project, cid):
    path=Path(project)/'.foreman/dispatch-preferences.json'
    with file_lock(path.with_suffix('.lock')):
        saved=optional_json(path)
        if not saved:return
        weights={k:v for k,v in saved.get('weights',{}).items() if k!=cid}
        total=sum(weights.values())
        if total:
            # Preserve explicit zeroes. Largest remainders make integer shares
            # sum to 100 without turning a disabled model into an active one.
            normalized={k:v*100//total for k,v in weights.items()}
            remaining=100-sum(normalized.values())
            for key in sorted(weights,key=lambda k:-(weights[k]*100 % total))[:remaining]:normalized[key]+=1
            weights=normalized
        atomic_json(path,{'version':saved.get('version',0)+1,'weights':weights})


def inbox(project):
    project = Path(project); root = project/'.foreman'
    rows = {}; states = {}
    for kind in KINDS:
        values = documents(project, 'campaigns') if kind == 'campaigns' else [optional_json(p) for p in (root/kind).glob('*/state.json') if not p.is_symlink()]
        for s in values:
            cid = str(s.get('id', ''))
            if not ID.fullmatch(cid):
                continue
            key = kind+':'+cid; states[key] = s
            rows[key] = {'key': key, 'kind': kind, 'id': cid, 'title': s.get('idea') or s.get('question') or s.get('goal') or cid,
                         'status': s.get('status'), 'stage': s.get('stage') or s.get('phase'),
                         'created_at': s.get('created_at', 0), 'chair': s.get('chair') or s.get('execution', {}).get('chair')}
    parents = {}
    for key, s in states.items():
        launch = s.get('launch')
        if key.startswith('briefings:') and launch:
            child = ('debates' if launch['mode'] == 'adversarial' else 'cooperations')+':'+launch['id']
            parents[child] = key
            if child in rows:
                rows[key].update(execution=rows[child].copy(), status=rows[child]['status'], stage=rows[child]['stage'])
    # A council's generated coding/experiment task remains a separate selectable
    # deliverable; only the confirmed briefing and its execution form one inbox item.
    mapping = {row['id']: parents.get(key, key) for key, row in rows.items()}
    return {'tasks': sorted([row for key, row in rows.items() if key not in parents], key=lambda r: r['created_at'], reverse=True),
            'group_tasks': mapping}


def usage(project, days=7, *, now=None):
    if type(days) is not int or days not in {1, 7, 30}:
        raise ValueError('Choose 1, 7 or 30 rolling days')
    import json
    project = Path(project); now = time.time() if now is None else now; since = now-days*86400
    box = inbox(project); task_names = {r['key']: r['title'] for r in box['tasks']}
    conns = connections(project, include_deleted=True); by_member = {r['member']: r for r in conns}
    records = []; db = project/'.foreman/pool.sqlite3'
    if db.exists():
        conn = sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            has_usage = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='token_usage'").fetchone()
            if has_usage:
                for r in conn.execute('SELECT * FROM token_usage WHERE recorded_at>=? AND recorded_at<=?', (since, now)):
                    meta = json.loads(r['receipt']); member = r['member']; c = by_member.get(member, {})
                    records.append({**meta, 'connection': c.get('id') or 'member:'+member,
                                    'label': c.get('label') or member, 'task': box['group_tasks'].get(r['group_id'], 'group:'+r['group_id'])})
            # Old/offline clients and calls still running have unknown consumption.
            query = 'SELECT * FROM tasks WHERE attempts>0 AND created>=? AND created<=?'
            if has_usage:
                query += ' AND NOT EXISTS (SELECT 1 FROM token_usage u WHERE u.lease=tasks.lease)'
            for r in conn.execute(query, (since, now)):
                member = r['member'] or 'unreported'; c = by_member.get(member, {})
                records.append({'connection': c.get('id') or 'member:'+member, 'label': c.get('label') or member,
                                'provider': c.get('provider') or 'unknown', 'model': c.get('model') or '',
                                'task': box['group_tasks'].get(r['group_id'], 'group:'+r['group_id']),
                                'input_tokens': None, 'output_tokens': None, 'complete': False})
        finally:
            conn.close()
    from .accounting import receipt
    for path in (project/'.foreman/smoke').glob('*/report.json'):
        report = optional_json(path); stamp = report.get('started_at')
        if type(stamp) not in {int, float} or not since <= stamp <= now:
            continue
        cid = report.get('connection_id'); c = next((r for r in conns if r['id'] == cid), {})
        for folder in path.parent.glob('worker/tasks/*/*'):
            if not (folder/'job/spec.json').exists():
                continue
            meta = receipt(folder, report.get('provider'), report.get('model') or '')
            records.append({**meta, 'connection': cid or 'smoke:'+str(report.get('provider')),
                            'label': c.get('label') or report.get('connection_label') or '连接测试（账号未知）', 'task': 'connection-tests'})
    task_names['connection-tests'] = '连接测试'
    for job in model_jobs(project):
        if job.is_symlink():
            continue
        stamp = optional_json(job/'result.json').get('started_at') or optional_json(job/'process.json').get('started_at')
        if type(stamp) not in {int, float} or not since <= stamp <= now:
            continue
        parts = job.relative_to(project/'.foreman').parts
        group = parts[1]; kind = 'campaigns' if parts[0] == 'campaigns' else 'runs'
        u = job_usage(job/'stdout.log')
        records.append({'connection': 'legacy-codex', 'label': 'Codex · 历史实验连接（账号未知）', 'provider': 'codex', 'model': '',
                        'task': box['group_tasks'].get(group, kind+':'+group),
                        'input_tokens': u['input_tokens'] if u['reported_turns'] else None,
                        'output_tokens': u['output_tokens'] if u['reported_turns'] else None, 'complete': u['complete']})
    def empty():
        return {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'attempts': 0, 'unknown_attempts': 0, 'partial_attempts': 0}
    def add(target, r):
        target['attempts'] += 1
        if r['input_tokens'] is None:
            target['unknown_attempts'] += 1
        else:
            target['input_tokens'] += r['input_tokens']; target['output_tokens'] += r['output_tokens']
            target['total_tokens'] += r['input_tokens']+r['output_tokens']
            target['partial_attempts'] += int(not r['complete'])
    total = empty(); per_connection = {}; per_task = {}
    for r in records:
        cid = r['connection']; key = r['task']
        c = per_connection.setdefault(cid, {**empty(), 'id': cid, 'label': r['label'], 'provider': r['provider'], 'model': r['model']})
        t = per_task.setdefault(key, {**empty(), 'key': key, 'title': task_names.get(key, '未关联任务 · '+key.split(':',1)[-1]), 'selectable': key in task_names and key != 'connection-tests', 'connections': {}})
        tc = t['connections'].setdefault(cid, {**empty(), 'id': cid, 'label': r['label']})
        for target in (total, c, t, tc):
            add(target, r)
    denominator = total['total_tokens']
    for c in per_connection.values():
        c['share_percent'] = c['total_tokens']/denominator*100 if denominator and c['attempts'] > c['unknown_attempts'] else None
    for t in per_task.values():
        for c in t['connections'].values():
            c['share_percent'] = c['total_tokens']/t['total_tokens']*100 if t['total_tokens'] and c['attempts'] > c['unknown_attempts'] else None
        t['connections'] = list(t['connections'].values())
    return {'generated_at': now, 'since': since, 'days': days, 'totals': total,
            'connections': sorted(per_connection.values(), key=lambda r: r['total_tokens'], reverse=True),
            'tasks': sorted(per_task.values(), key=lambda r: r['total_tokens'], reverse=True),
            'share_basis': 'reported_tokens_only', 'billing_estimate': None,
            'note': 'Client-reported tokens only. Unknown calls are excluded from shares, not counted as zero. Retries are per lease, replay uploads are deduplicated. No subscription quota or billing inference.'}
