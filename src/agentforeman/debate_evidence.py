"""Frozen, scoped verification for text, data and other bounded task artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

from .files import atomic_json, atomic_text, digest, inventory, read_json
from .job import wait_for_job
from .pool import dumps, name


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def normalize(value):
    return ' '.join(value.split())


def portable_name(value):
    name(value)
    if value.upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        raise ValueError('Artifact/check ID is a reserved Windows filename')
    return value


def prepare(project,root,evidence,checks):
    if not isinstance(evidence,list) or len(evidence)>30:
        raise ValueError('Evidence must be a list of at most 30 supplied records')
    sources=[]
    for item in evidence:
        if not isinstance(item,dict) or set(item)-{'id','title','content','url'} or not {'id','title','content'}<=set(item):
            raise ValueError('Evidence requires id/title/content and optional url')
        name(item['id'])
        if any(not isinstance(v,str) or not v.strip() or len(v)>100000 for v in item.values()):
            raise ValueError('Evidence fields must be nonempty bounded strings')
        sources.append({**item,'sha256':fingerprint(item),'verification':'supplied_content_not_independently_retrieved'})
    if len({s['id'] for s in sources})!=len(sources):raise ValueError('Duplicate evidence ID')
    if not isinstance(checks,list) or len(checks)>12:raise ValueError('At most 12 frozen checks')
    frozen=[]
    for check in checks:
        if not isinstance(check,dict):raise ValueError('Invalid check')
        portable_name(check['id']);kind=check['kind']
        expected={'json_value':{'id','kind','artifact_id','pointer','expected'},
                  'contains':{'id','kind','artifact_id','expected'},
                  'python':{'id','kind','script','timeout_seconds'}}.get(kind)
        if expected is None or set(check)!=expected:raise ValueError('Check must match its frozen protocol')
        entry=dict(check)
        if kind=='python':
            rel=Path(check['script'])
            script=(project/rel).resolve()
            if rel.is_absolute() or '..' in rel.parts or not script.is_relative_to(project.resolve()) or not script.is_file() or (project/rel).is_symlink():
                raise ValueError('Verifier must be an existing project-local script')
            if type(check['timeout_seconds']) not in (int,float) or not 1<=check['timeout_seconds']<=600:
                raise ValueError('Verifier timeout must be 1..600 seconds')
            target=root/'verifiers'/(check['id']+'.py')
            atomic_text(target,script.read_text(encoding='utf-8'))
            entry['script']=target.relative_to(root).as_posix();entry['script_sha256']=digest(target)
        else:
            portable_name(check['artifact_id'])
            if kind=='contains' and (not isinstance(check['expected'],str) or not check['expected']):
                raise ValueError('Contains check needs a nonempty literal')
            if kind=='json_value' and (not isinstance(check['pointer'],str) or (check['pointer'] and not check['pointer'].startswith('/'))):
                raise ValueError('Expected a JSON pointer')
        fingerprint(entry)  # Reject non-JSON/NaN configuration before scheduling.
        frozen.append(entry)
    if len({c['id'].casefold() for c in frozen})!=len(frozen):raise ValueError('Duplicate check ID')
    atomic_json(root/'evidence.json',sources)
    atomic_json(root/'checks.json',frozen)
    return sources,frozen,{'evidence':digest(root/'evidence.json'),'checks':digest(root/'checks.json')}


def assert_frozen(root,state):
    if {'evidence':digest(root/'evidence.json'),'checks':digest(root/'checks.json')}!=state['frozen']:
        raise ValueError('Frozen verification inputs changed')
    for check in state['checks']:
        if check['kind']=='python' and digest(root/check['script'])!=check['script_sha256']:
            raise ValueError('Frozen verifier script changed')


def materialize(root,proposal):
    root.mkdir(parents=True,exist_ok=True)
    ids=[]
    for artifact in proposal['artifacts']:
        aid=portable_name(artifact['id']);ids.append(aid.casefold())
        atomic_text(root/aid,artifact['content'])
    if len(ids)!=len(set(ids)) or 'answer' not in ids:raise ValueError('Unique artifact IDs including answer are required')
    atomic_json(root/'manifest.json',[{'id':a['id'],'media_type':a['media_type']} for a in proposal['artifacts']])


def pointer(value,path):
    if not path:return value
    for raw in path[1:].split('/'):
        key=raw.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            if not key.isdigit() or (key.startswith('0') and key!='0'):raise ValueError('Invalid JSON array pointer')
            value=value[int(key)]
        else:value=value[key]
    return value


def verify(root,state,version):
    assert_frozen(root,state)
    artifacts=root/'versions'/version['id']/'artifacts'
    expected=version['artifact_hashes']
    if inventory(artifacts)!=expected:raise ValueError('Proposal artifacts changed before verification')
    receipts=[]
    for check in state['checks']:
        if time.time()>=state['deadline']:break
        key=fingerprint({'proposal':version['check_input_sha256'],'check':check})
        folder=root/'verification'/key
        cached=folder/'receipt.json'
        if cached.exists():
            receipt=read_json(cached)
            if receipt['input_sha256']!=version['check_input_sha256'] or receipt['check_sha256']!=fingerprint(check):
                raise ValueError('Verification receipt does not match frozen inputs')
            receipts.append(receipt);continue
        folder.mkdir(parents=True,exist_ok=True)
        passed=False;summary='Configured check failed';findings=[]
        if check['kind']=='python':
            job=folder/'job';output=folder/'result.json'
            spec=read_json(job/'spec.json') if (job/'spec.json').exists() else {
                'argv':[sys.executable,str(root/check['script']),'--artifacts',str(artifacts),
                        '--evidence',str(root/'evidence.json'),'--output',str(output)],
                'cwd':str(folder),'env':{'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'},
                'deadline_at':state['deadline'],'timeout_seconds':min(check['timeout_seconds'],state['deadline']-time.time())}
            try:
                result=wait_for_job(job,spec)
                if result['exit_code']==0 and not any(result[k] for k in ('error','cancelled','timed_out')):
                    if not output.is_file() or output.stat().st_size>64000:raise ValueError('Missing/bounded verifier result required')
                    answer=read_json(output)
                    if not isinstance(answer,dict) or set(answer)!={'passed','summary','findings'} or type(answer['passed']) is not bool:
                        raise ValueError('Invalid verifier output')
                    if not isinstance(answer['summary'],str) or not answer['summary'].strip() or len(answer['summary'])>8000:
                        raise ValueError('Invalid verifier summary')
                    if not isinstance(answer['findings'],list) or len(answer['findings'])>30 or any(not isinstance(s,str) or not s.strip() or len(s)>2000 for s in answer['findings']):
                        raise ValueError('Invalid verifier findings')
                    passed=answer['passed'];summary=answer['summary'];findings=answer['findings']
            except (ValueError,OSError,RuntimeError):
                summary='Verifier did not produce an acceptable independent receipt; inspect its local job logs'
        else:
            try:
                content=(artifacts/check['artifact_id']).read_text(encoding='utf-8')
                if check['kind']=='contains':passed=check['expected'] in content
                else:
                    observed=pointer(json.loads(content),check['pointer'])
                    passed=fingerprint(observed)==fingerprint(check['expected'])
                summary='Frozen '+check['kind']+' check '+('passed' if passed else 'failed')
            except (ValueError,OSError,KeyError,TypeError,IndexError):
                summary='Required artifact or value is missing/invalid'
        assert_frozen(root,state)
        if inventory(artifacts)!=expected:raise ValueError('Verifier mutated its proposal inputs')
        receipt={'check_id':check['id'],'passed':bool(passed),'summary':summary,'findings':findings,
                 'scope':'Only this frozen check; not general factual correctness',
                 'input_sha256':version['check_input_sha256'],'check_sha256':fingerprint(check)}
        atomic_json(cached,receipt);receipts.append(receipt)
    return receipts


def anchor_valid(anchor,state,version):
    kind=anchor['kind'];ref=anchor['ref']
    if kind=='artifact':content=next((a['content'] for a in version['proposal']['artifacts'] if a['id']==ref),None)
    elif kind=='source':content=next((s['content'] for s in state['evidence'] if s['id']==ref),None)
    else:
        receipt=next((r for r in version['checks'] if r['check_id']==ref),None)
        content=json.dumps(receipt,ensure_ascii=False,indent=2,sort_keys=True) if receipt is not None else None
    if content is None:return False
    lines=content.splitlines();start=anchor['line_start'];end=anchor['line_end']
    return 1<=start<=end<=len(lines) and bool(anchor['quote'].strip()) and anchor['quote'] in '\n'.join(lines[start-1:end])
