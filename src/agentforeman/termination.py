"""Durable immediate cancellation intent with explicit worker/native receipts."""
from pathlib import Path
import re

from .files import atomic_json, atomic_text, read_json
from .platforms import alive
from .pool import Pool

KINDS={'briefings','debates','cooperations','councils','coding','campaigns'}


def root_for(project,kind,rid):
    if kind not in KINDS or not isinstance(rid,str) or not re.fullmatch('[0-9a-f]{12}',rid):
        raise ValueError('Invalid termination target')
    return Path(project).resolve()/'.foreman'/kind/rid


def request(project,kind,rid):
    project=Path(project).resolve();root=root_for(project,kind,rid)
    if kind=='campaigns':
        from .store import Store
        store=Store(project)
        try:state=store.get_campaign(rid)
        finally:store.close()
    else:state=read_json(root/'state.json')
    # Mark first. Supervisors consult ancestor intent before launch and while
    # running; retries/restarts cannot silently resume a terminated task.
    atomic_text(root/'terminate.request','terminate\n')
    atomic_text(root/'stop.request','stop\n')
    pool=Pool(project);pool.cancel_group(rid)
    children=[]
    if kind=='briefings' and state.get('launch'):
        launch=state['launch'];child_kind='debates' if launch['mode']=='adversarial' else 'cooperations'
        child_root=root_for(project,child_kind,launch['id'])
        atomic_text(child_root/'terminate.request','terminate\n');atomic_text(child_root/'stop.request','stop\n')
        pool.cancel_group(launch['id'])
        if (child_root/'state.json').exists():request(project,child_kind,launch['id'])
        children.append({'kind':child_kind,'id':launch['id']})
    if kind=='councils' and state.get('experiment',{}).get('campaign_id'):
        child=state['experiment']['campaign_id'];request(project,'campaigns',child)
        children.append({'kind':'campaigns','id':child})
    # Managed local workers get their marker without waiting for network polling.
    for task in pool.tasks(rid):
        if task['cancel_requested']:
            for worker in (project/'.foreman/connections').glob('*/worker'):
                for attempt in (worker/'tasks'/task['id']).glob('*'):
                    atomic_text(attempt/'job/cancel.request','cancel\n')
    atomic_json(root/'termination.json',{'kind':kind,'id':rid,'children':children})
    return status(project,kind,rid)


def status(project,kind,rid):
    root=root_for(project,kind,rid)
    if not (root/'terminate.request').exists():return {'requested':False}
    pool=Pool(project);workers=pool.cancellation(rid)
    native=[]
    for spec in root.rglob('spec.json'):
        job=spec.parent
        if not ('argv' in read_json(spec)):continue
        meta=read_json(job/'process.json') if (job/'process.json').exists() else {}
        receipt=read_json(job/'result.json') if (job/'result.json').exists() else None
        child=meta.get('child')
        if child is None and (job/'child.json').exists():child=read_json(job/'child.json')
        running=alive(child)
        confirmed=bool(receipt and not running and not receipt.get('error'))
        native.append({'job':job.relative_to(root).as_posix(),'confirmed_exited':confirmed,
                       'child_alive':running,'cancelled':receipt.get('cancelled') if receipt else None})
    children=[]
    if (root/'termination.json').exists():
        for c in read_json(root/'termination.json')['children']:children.append(status(project,c['kind'],c['id']))
    all_ack=workers['all_workers_acknowledged'] and all(j['confirmed_exited'] for j in native) and all(c.get('all_execution_acknowledged',False) for c in children)
    return {'requested':True,'all_execution_acknowledged':all_ack,
            'status':'confirmed_stopped' if all_ack else 'waiting_for_confirmation',
            'workers':workers['tasks'],'native_jobs':native,'children':children,
            'provider_generation_stopped':'unknown',
            'note':'Local process/request exit is confirmed separately. Provider-side generation or billing cannot be guaranteed stopped.'}
