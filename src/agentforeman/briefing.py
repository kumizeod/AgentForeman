"""Chair/human clarification with version-bound, crash-resumable execution launch."""
from __future__ import annotations

import math
from pathlib import Path
import secrets
import time

from .council import obj, choice, BOUNDARY
from .debate import txt, seq
from .debate_evidence import prepare, assert_frozen, fingerprint, portable_name
from .files import atomic_json, atomic_text, read_json
from .platforms import file_lock
from .pool import Pool, dumps, name, validate

DELIVERABLE=obj(id=txt(64),description=txt(2000))
ACCEPTANCE=obj(criterion=txt(2000),verification=choice('automated','manual'),check_ids=seq(txt(64),maximum=12))
CONTRACT=obj(goal=txt(6000),inputs=seq(txt(2000),maximum=12),deliverables=seq(DELIVERABLE,1,8),
             acceptance=seq(ACCEPTANCE,1,12),constraints=seq(txt(2000),1,12),
             priorities=seq(txt(1000),1,8),exclusions=seq(txt(2000),maximum=12),
             autonomy=obj(allowed_decisions=seq(txt(2000),1,12),pause_conditions=seq(txt(2000),1,12),
                          defaults=seq(txt(2000),maximum=12)))
REPLY=obj(reply=txt(8000),questions=seq(txt(2000),maximum=8),contract=CONTRACT,
          open_questions=seq(txt(2000),maximum=12),ready={'type':'boolean'})


class Briefing:
    def __init__(self,project):
        self.project=Path(project).resolve();self.root=self.project/'.foreman'/'briefings';self.pool=Pool(self.project)

    def path(self,bid):
        if not isinstance(bid,str) or len(bid)!=12 or any(c not in '0123456789abcdef' for c in bid):
            raise ValueError('Invalid briefing ID')
        return self.root/bid

    def get(self,bid):return read_json(self.path(bid)/'state.json')

    def list(self):
        return sorted([{k:s.get(k) for k in ('id','idea','status','version','turn','created_at','launch')}
                       for p in self.root.glob('*/state.json') for s in [read_json(p)]],
                      key=lambda s:s['created_at'],reverse=True)

    def execution(self,value):
        if not isinstance(value,dict) or set(value)-{'mode','members','reviewers','chair','max_rounds','budget_seconds','stage_seconds'}:
            raise ValueError('Unknown execution setting')
        if value.get('mode') not in {'adversarial','cooperative'}:raise ValueError('Choose adversarial or cooperative execution')
        value={'max_rounds':3,'budget_seconds':3600,'stage_seconds':600,**value}
        members=value.get('members');reviewers=value.get('reviewers',[])
        if not isinstance(members,list) or not 2<=len(members)<=5:raise ValueError('Choose 2..5 members')
        if not isinstance(reviewers,list):raise ValueError('Reviewers must be a list')
        if value['mode']=='cooperative' and not 1<=len(reviewers)<=3:raise ValueError('Choose 1..3 independent reviewers')
        if value['mode']=='adversarial' and reviewers:raise ValueError('Adversarial reviewers belong in members after the proposer')
        identities=[*members,*reviewers,value.get('chair')]
        for member in identities:name(member)
        if len(set(identities))!=len(identities):raise ValueError('All roles must use distinct identities')
        enabled={m['id'] for m in self.pool.members() if m['enabled']}
        if set(identities)-enabled:raise ValueError('All participants must be registered and enabled')
        if type(value['max_rounds']) is not int or not 1<=value['max_rounds']<=5:raise ValueError('Choose 1..5 review rounds')
        for key in ('budget_seconds','stage_seconds'):
            v=value[key]
            if type(v) not in (int,float) or not math.isfinite(v) or not 10<=v<=86400:raise ValueError('Execution budgets must be 10..86400 seconds')
        return value

    def validate_contract(self,state,contract):
        validate(contract,CONTRACT)
        if len(dumps(contract).encode())>20000:raise ValueError('Task contract exceeds 20 KB')
        ids=[portable_name(d['id']) for d in contract['deliverables']]
        if len({i.casefold() for i in ids})!=len(ids) or 'answer' not in ids:
            raise ValueError('Unique deliverable IDs including answer required')
        checks={c['id'] for c in state['checks']}
        for item in contract['acceptance']:
            refs=item['check_ids']
            if len(set(refs))!=len(refs) or set(refs)-checks:raise ValueError('Acceptance refers to unknown/duplicate frozen checks')
            if item['verification']=='automated' and not refs:raise ValueError('Automated acceptance requires configured check IDs')
            if item['verification']=='manual' and refs:raise ValueError('Manual acceptance cannot claim automated check coverage')

    def create(self,idea,execution,*,evidence=None,checks=None,turn_seconds=600,max_turns=8,auto_candidates=None):
        if not isinstance(idea,str) or not idea.strip() or len(idea)>8000:raise ValueError('Provide a bounded task idea')
        execution=self.execution(execution)
        if type(turn_seconds) not in (int,float) or not math.isfinite(turn_seconds) or not 10<=turn_seconds<=3600:
            raise ValueError('Clarification turn budget must be 10..3600 seconds')
        if type(max_turns) is not int or not 1<=max_turns<=12:raise ValueError('Choose 1..12 clarification turns')
        if auto_candidates is not None:
            if not isinstance(auto_candidates,list) or not 3<=len(auto_candidates)<=12:raise ValueError('Choose 3..12 candidate connections')
            ids=[c['member'] for c in auto_candidates]
            if len(set(ids))!=len(ids):raise ValueError('Duplicate candidates')
            for member in ids:name(member)
            if execution['chair'] not in ids:raise ValueError('Chair must belong to the candidate set')
        bid=secrets.token_hex(6);root=self.path(bid)
        sources,checks,frozen=prepare(self.project,root,[] if evidence is None else evidence,[] if checks is None else checks)
        if len(dumps(sources).encode())>100000:raise ValueError('Combined evidence exceeds 100 KB')
        state={'id':bid,'idea':idea,'created_at':time.time(),'status':'running','stage':'clarify',
               'version':0,'turn':0,'turn_seconds':turn_seconds,'max_turns':max_turns,'execution':execution,
               'evidence':sources,'checks':checks,'frozen':frozen,'draft':None,'sha256':None,'launch':None,
               'messages':[{'role':'human','content':idea}],'errors':[]}
        if auto_candidates is not None:state['auto_candidates']=auto_candidates
        self.enter(state);self.save(state);self.advance(bid);return bid

    def enter(self,state):
        if state['turn']>=state['max_turns']:raise ValueError('Clarification turn budget exhausted; start a new briefing')
        state.update(turn=state['turn']+1,status='running',stage='clarify',deadline=time.time()+state['turn_seconds'])
        prompt=(BOUNDARY+'\nYou are the pre-execution chairman speaking with a human. No implementation or tool commands. '
                'Clarify goals, inputs, concrete deliverables (including answer), acceptance, constraints, priorities, '
                'non-goals and autonomous decision/pause/default rules. Ask at most 8 focused questions per turn. '
                'Unanswered material questions must remain in open_questions. Set ready only when both question lists are empty. '
                'Automated acceptance must cite provided check IDs; use manual for unverified criteria. '
                'Do not invent checks, permissions, evidence, compute resources or human decisions. '
                'You cannot confirm or start execution. The human must review the exact contract and execution settings.\n')
        context={k:state[k] for k in ('idea','messages','draft','execution','evidence')}
        context['checks']=[{k:c[k] for k in ('id','kind')} for c in state['checks']]
        schema=REPLY
        if state.get('auto_candidates'):
            context['available_connections']=state['auto_candidates']
            prompt+=('Choose an execution role_plan from available_connections. Keep the chairman fixed. '
                     'Use distinct connection identities. Choose adversarial for critique/revision, cooperative '
                     'for separable contributions (requires at least 4 connections). Explain role choices. '
                     'Shared resource_group means shared login/serial capacity, not independent quota. '
                     'You cannot change budgets, add resources, run tools, or claim unconfigured verification.\n')
            schema=self.reply_schema(state)
        request={'prompt':prompt+dumps(context),'schema':schema}
        if len(dumps(request).encode())>700000:raise ValueError('Clarification context budget exhausted; start a new briefing')
        state['task_spec']={'id':f'{state["id"]}_clarify_{state["turn"]}',
                            'request':request}

    def reply_schema(self,state):
        if not state.get('auto_candidates'):return REPLY
        identities=choice(*(c['member'] for c in state['auto_candidates']))
        return obj(**REPLY['properties'],role_plan=obj(mode=choice('adversarial','cooperative'),
                   members=seq(identities,2,5),reviewers=seq(identities,maximum=3),rationale=txt(2000)))

    def snapshot(self,state,contract,author):
        self.validate_contract(state,contract)
        state['version']+=1
        bundle={'contract':contract,'execution':state['execution'],
                'evidence_sha256':state['frozen']['evidence'],'checks_sha256':state['frozen']['checks']}
        state['sha256']=fingerprint(bundle)
        atomic_json(self.path(state['id'])/'versions'/f'v{state["version"]}.json',
                    {'version':state['version'],'sha256':state['sha256'],'bundle':bundle,'author':author})

    def current_bundle(self,state):
        record=read_json(self.path(state['id'])/'versions'/f'v{state["version"]}.json')
        expected={'contract':state['draft']['contract'],'execution':state['execution'],
                  'evidence_sha256':state['frozen']['evidence'],'checks_sha256':state['frozen']['checks']}
        if record['bundle']!=expected or record['sha256']!=state['sha256'] or fingerprint(expected)!=state['sha256']:
            raise ValueError('Briefing snapshot changed; confirmation rejected')
        return expected

    def message(self,bid,version,content):
        with file_lock(self.path(bid)/'controller.lock'):
            state=self.get(bid);assert_frozen(self.path(bid),state)
            if version!=state['version'] or type(version) is not int:raise ValueError('Stale briefing version; reload before replying')
            if state['status'] not in {'waiting_human','ready'}:raise ValueError('Wait for the chairman; launched contracts cannot be edited')
            if not isinstance(content,str) or not content.strip() or len(content)>8000:raise ValueError('Reply must contain 1..8000 characters')
            if state['turn']>=state['max_turns']:raise ValueError('Clarification turn budget exhausted')
            state['messages'].append({'role':'human','content':content});self.enter(state);self.save(state)
        return self.advance(bid)

    def edit(self,bid,version,contract,execution=None):
        with file_lock(self.path(bid)/'controller.lock'):
            state=self.get(bid);assert_frozen(self.path(bid),state)
            if type(version) is not int or version!=state['version'] or state['status']!='ready':
                raise ValueError('Only the current ready contract may be edited')
            if execution is not None:state['execution']=self.execution(execution)
            self.validate_contract(state,contract)
            state['draft']['contract']=contract;self.snapshot(state,contract,'human')
            state['messages'].append({'role':'human','content':'Edited task contract; explicit confirmation is still required.'})
            self.save(state);return state

    def confirm(self,bid,version,sha256):
        with file_lock(self.path(bid)/'controller.lock'):
            state=self.get(bid);assert_frozen(self.path(bid),state)
            if type(version) is not int or version!=state['version'] or sha256!=state['sha256']:
                raise ValueError('Stale confirmation; review the latest version and fingerprint')
            if state['status']=='launched':return state
            if state['status']=='running' and state['stage']=='launch':return self.launch(state)
            if state['status']!='ready':raise ValueError('Chairman clarification is not ready for confirmation')
            bundle=self.current_bundle(state);self.execution(bundle['execution'])
            state.update(status='running',stage='launch',confirmed_at=time.time())
            state['launch']={'id':secrets.token_hex(6),'mode':state['execution']['mode'],'version':version,'sha256':sha256}
            self.save(state)  # Durable launch intent precedes creation/enqueue.
            return self.launch(state)

    def stop(self,bid):
        with file_lock(self.path(bid)/'controller.lock'):
            state=self.get(bid)
            if state['status']=='launched' or state['stage']=='launch':
                raise ValueError('Execution launch already committed; stop the execution task instead')
            state.update(status='stopped',stage='done')
            with self.pool.connect() as c:c.execute("UPDATE tasks SET status='cancelled' WHERE group_id=? AND status='queued'",(bid,))
            self.save(state);return state

    def launch(self,state):
        bundle=self.current_bundle(state);root=self.path(state['id']);assert_frozen(root,state)
        from .debate import Debate
        from .cooperate import Cooperation
        settings=dict(bundle['execution']);mode=settings.pop('mode')
        if mode=='adversarial':settings.pop('reviewers',None)
        checks=[]
        for item in state['checks']:
            item=dict(item)
            if item['kind']=='python':
                item['script']=(root/item['script']).relative_to(self.project).as_posix();item.pop('script_sha256')
            checks.append(item)
        evidence=[{k:v for k,v in s.items() if k in {'id','title','content','url'}} for s in state['evidence']]
        binding={'briefing_id':state['id'],'version':state['version'],'sha256':state['sha256'],'bundle':bundle}
        question='Execute the attached human-confirmed task contract. Stay within its goal, constraints and autonomous decision boundaries.\n'+bundle['contract']['goal']
        controller=(Debate if mode=='adversarial' else Cooperation)(self.project)
        controller.create(question,**settings,evidence=evidence,checks=checks,
                          run_id=state['launch']['id'],task_contract=binding)
        state.update(status='launched',stage='done');self.save(state);return state

    def advance(self,bid):
        root=self.path(bid)
        try:
            with file_lock(root/'controller.lock'):
                state=self.get(bid);assert_frozen(root,state)
                if state['status']!='running':return state
                if state['stage']=='launch':return self.launch(state)
                spec=state['task_spec']
                if time.time()<state['deadline']:
                    self.pool.enqueue(bid,f'clarify_{state["turn"]}',[state['execution']['chair']],
                                      spec['request'],state['deadline'],task_id=spec['id'])
                row=next((t for t in self.pool.tasks(bid) if t['id']==spec['id']),None)
                if row and row['status']=='completed':
                    try:
                        draft=validate(row['result'],self.reply_schema(state));self.validate_contract(state,draft['contract'])
                        if state.get('auto_candidates'):
                            plan=draft['role_plan']
                            execution={**state['execution'],**{k:plan[k] for k in ('mode','members','reviewers')}}
                            candidates={c['member'] for c in state['auto_candidates']}
                            if set(execution['members']+execution['reviewers'])-candidates:raise ValueError('Chair invented a connection')
                            state['execution']=self.execution(execution)
                        state['draft']=draft;self.snapshot(state,draft['contract'],'chairman')
                        state['messages'].append({'role':'chairman','content':draft['reply'],
                                                  'questions':draft['questions'],'open_questions':draft['open_questions']})
                        state['status']='ready' if draft['ready'] and not draft['questions'] and not draft['open_questions'] else 'waiting_human'
                    except ValueError as exc:
                        state['errors'].append(str(exc));state['status']='waiting_human'
                    state['stage']='await_human';self.save(state)
                elif time.time()>=state['deadline'] or (row and row['status'] in {'failed','expired','cancelled'}):
                    state.update(status='waiting_human',stage='await_human')
                    state['errors'].append('Chairman result missing or failed; no execution was started.')
                    with self.pool.connect() as c:c.execute("UPDATE tasks SET status='cancelled' WHERE group_id=? AND status='queued'",(bid,))
                    self.save(state)
                return state
        except BlockingIOError:return self.get(bid)

    def fail(self,bid):
        # Keep a committed launch intent retryable with the same child ID.
        with file_lock(self.path(bid)/'controller.lock'):
            state=self.get(bid);state['errors'].append('Controller/input error; inspect configuration and saved state.')
            if state['stage']!='launch':state.update(status='waiting_human',stage='await_human')
            self.save(state)

    def save(self,state):
        state['errors']=list(dict.fromkeys(state['errors']))[-12:]
        root=self.path(state['id']);atomic_json(root/'state.json',state)
        atomic_text(root/'report.md','# Task clarification '+state['id']+'\n\n'+dumps(state)+'\n')
