"""Evidence-bound adversarial revision for general task artifacts, not just code."""
from __future__ import annotations

import math
import secrets
import time
from pathlib import Path

from .council import obj, choice, items, strings, BOUNDARY
from .debate_evidence import (fingerprint, normalize, prepare, assert_frozen,
                              materialize, verify, anchor_valid)
from .files import atomic_json, atomic_text, inventory, read_json
from .platforms import file_lock
from .pool import Pool, dumps, name, validate


def txt(limit=2000):return {'type':'string','minLength':1,'maxLength':limit}
def seq(schema,n=0,maximum=12):return {'type':'array','items':schema,'minItems':n,'maxItems':maximum}
LINE={'type':'integer','minimum':1,'maximum':1000000}
ANCHOR=obj(kind=choice('artifact','source','check'),ref=txt(64),quote=txt(1000),line_start=LINE,line_end=LINE)
ARTIFACT=obj(id=txt(64),media_type=choice('text/plain','text/markdown','text/csv','application/json'),content=txt(40000))
SUPPORT=obj(source_id=txt(64),quote=txt(1000))
CLAIM=obj(id=txt(64),statement=txt(),source_refs=seq(SUPPORT,maximum=5))
RESPONSE=obj(issue_id=txt(32),action=choice('change','evidence','unresolved'),explanation=txt())
PROPOSAL=obj(summary=txt(),artifacts=seq(ARTIFACT,1,8),claims=seq(CLAIM,maximum=12),responses=seq(RESPONSE,maximum=120))
ISSUE=obj(severity=choice('blocking','major','minor'),problem=txt(),anchor=ANCHOR,suggested_change=txt())
CRITIQUE=obj(verdict=choice('accept','revise','reject'),summary=txt(),issues=seq(ISSUE,maximum=6))
CHAIR=obj(candidate_version=txt(32),summary=txt(8000),open_questions=strings(),proposed_checks=strings())
RULES=BOUNDARY+'''\nThis is a general task, not necessarily coding. Return bounded artifacts, including
an artifact with id=answer. Tools are not authorized. Frozen checks, their receipts
and exact source quotations are the only controller evidence. Matching an input
quotation does not verify its authenticity or truth. Never claim a proposed test ran.
The chair is advisory and cannot edit artifacts, choose check commands, mark issues
verified or override check results. Only the controller computes scoped status.
'''


class Debate:
    def __init__(self,project):
        self.project=Path(project).resolve();self.root=self.project/'.foreman'/'debates'
        self.pool=Pool(self.project)

    def path(self,did):
        if not isinstance(did,str) or len(did)!=12 or any(c not in '0123456789abcdef' for c in did):raise ValueError('Invalid debate ID')
        return self.root/did

    def get(self,did):return read_json(self.path(did)/'state.json')

    def list(self):
        return sorted([{k:s.get(k) for k in ('id','question','status','stage','round','max_rounds','created_at','deadline','stop_reason','members','chair')}
                       for p in self.root.glob('*/state.json') for s in [read_json(p)]],key=lambda s:s['created_at'],reverse=True)

    def create(self,question,members,chair,*,evidence=None,checks=None,max_rounds=3,budget_seconds=3600,stage_seconds=600):
        if not isinstance(question,str) or not question.strip() or len(question)>16000:raise ValueError('Provide a bounded question')
        if not isinstance(members,list) or not 2<=len(members)<=5 or any(not isinstance(m,str) for m in members) or len(set(members))!=len(members):raise ValueError('Choose 2..5 distinct members; first proposes, others critique')
        for m in [*members,chair]:name(m)
        if chair in members:raise ValueError('Chair must be a separate identity')
        known={m['id'] for m in self.pool.members() if m['enabled']}
        if set([*members,chair])-known:raise ValueError('Register each member and chair first')
        if type(max_rounds) is not int or not 1<=max_rounds<=5:raise ValueError('max_rounds must be 1..5')
        for v in (budget_seconds,stage_seconds):
            if type(v) not in (int,float) or not math.isfinite(v) or not 10<=v<=86400:raise ValueError('Finite budgets 10..86400 seconds required')
        did=secrets.token_hex(6);root=self.path(did)
        evidence,checks,frozen=prepare(self.project,root,[] if evidence is None else evidence,[] if checks is None else checks)
        if len(dumps(evidence).encode())>100000:raise ValueError('Combined evidence exceeds 100 KB')
        now=time.time()
        state={'id':did,'question':question,'mode':'adversarial','members':members,'chair':chair,
               'created_at':now,'deadline':now+budget_seconds,'stage_seconds':stage_seconds,
               'max_rounds':max_rounds,'round':1,'status':'running','stage':'propose',
               'evidence':evidence,'checks':checks,'frozen':frozen,'versions':[],'issues':[],
               'reviews':[],'protocol_errors':[],'progress':[],
               'quality':{'factual_correctness_verified':False,'chair_is_advisory':True}}
        self.enter(state,'propose');self.save(state);self.advance(did)
        return did

    def current(self,state):return state['versions'][-1]

    def open_issues(self,state):return [i for i in state['issues'] if i['status']!='check_passed_scoped']

    def selected(self,state):
        def score(v):
            passed=sum(r['passed'] for r in v['checks']);all_passed=bool(state['checks']) and len(v['checks'])==len(state['checks']) and all(r['passed'] for r in v['checks'])
            return (all_passed,passed,len(state['versions'][:state['versions'].index(v)+1]))
        return max(state['versions'],key=score) if state['versions'] else None

    def context(self,state):
        return {'question':state['question'],'evidence':state['evidence'],
                'checks':[{'id':c['id'],'kind':c['kind'],'artifact_id':c.get('artifact_id'),
                           'scope':'Frozen check only; not general factual truth'} for c in state['checks']],
                'current':self.current(state) if state['versions'] else None,
                'open_issues':self.open_issues(state),'round':state['round'],'max_rounds':state['max_rounds']}

    def enter(self,state,stage):
        state.update(stage=stage,stage_deadline=min(time.time()+state['stage_seconds'],state['deadline']))
        if stage=='verify':state['task_specs']=[];return
        context=self.context(state)
        if stage in {'propose','revise'}:
            extra='Produce an initial answer and concrete task artifacts. responses must be empty.' if stage=='propose' else 'Revise the artifacts and respond to EVERY open issue ID exactly once. Cite any added evidence with an exact supplied quotation. Unchanged or cycling artifacts without new anchored support stop automatically.'
            jobs=[([state['members'][0]],PROPOSAL,extra)]
        elif stage=='critique':
            extra=('Independently criticize the current artifacts. Each issue needs an exact quote and valid line range '
                   'from an artifact, supplied source or check receipt. Point to the check that actually failed when possible. '
                   'Do not invent bugs to meet a quota. accept requires no major/blocking issues; revise requires a concrete issue. '
                   'Check all prior open issues. You cannot mark them factually resolved.')
            jobs=[([m],CRITIQUE,extra) for m in state['members'][1:]]
        else:
            chosen=self.selected(state)
            context.update(selected_candidate=chosen,stop_reason=state.get('pending_stop_reason'))
            extra=('Summarize the record and preserved disagreements. candidate_version MUST equal the controller-selected '
                   'version ID. This summary is advisory, not a new answer or verification verdict. '
                   'Any additional proposed checks remain suggestions; you cannot execute or authorize commands.')
            jobs=[([state['chair']],CHAIR,extra)]
        specs=[]
        for i,(eligible,schema,extra) in enumerate(jobs):
            request={'prompt':RULES+'\n'+extra+'\n'+dumps(context),'schema':schema}
            if len(dumps(request).encode())>700000:raise ValueError('Debate context exceeds bounded payload')
            specs.append({'id':f'{state["id"]}_r{state["round"]}_{stage}_{i}','eligible':eligible,
                          'request':request,'deadline':state['stage_deadline']})
        state['task_specs']=specs

    def validate_proposal(self,state,answer):
        validate(answer,PROPOSAL)
        aids=[name(a['id']) for a in answer['artifacts']]
        if len(set(aids))!=len(aids) or 'answer' not in aids:raise ValueError('Unique artifacts including answer required')
        if sum(len(a['content'].encode()) for a in answer['artifacts'])>100000:raise ValueError('Artifact content exceeds 100 KB')
        claims=[name(c['id']) for c in answer['claims']]
        if len(set(claims))!=len(claims):raise ValueError('Duplicate claim ID')
        source_map={s['id']:s['content'] for s in state['evidence']}
        support=set()
        for claim in answer['claims']:
            for ref in claim['source_refs']:
                if not ref['quote'].strip() or ref['quote'] not in source_map.get(ref['source_id'],''):raise ValueError('Claim cites unknown source or unmatched quotation')
                support.add(fingerprint(ref))
        expected={i['id'] for i in self.open_issues(state)} if state['versions'] else set()
        responses=[r['issue_id'] for r in answer['responses']]
        if set(responses)!=expected or len(responses)!=len(expected):raise ValueError('Every open issue needs one response')
        normalized=[{'id':a['id'],'media_type':a['media_type'],'content':normalize(a['content'])} for a in sorted(answer['artifacts'],key=lambda a:a['id'])]
        return fingerprint(normalized),support

    def accept_proposal(self,state,answer):
        artifact_digest,support=self.validate_proposal(state,answer)
        seen={v['artifact_digest'] for v in state['versions']}
        prior_support={s for v in state['versions'] for s in v['source_support']}
        progress={'round':state['round'],'artifact_changed':artifact_digest not in seen,
                  'new_anchored_support':sorted(support-prior_support),
                  'scope':'Structural content changes and matched input quotes, not a semantic quality proof'}
        state['progress'].append(progress)
        if state['versions'] and artifact_digest in seen and not support-prior_support:
            state['stalled_response']=answer
            self.conclude(state,'no_progress');return
        vid=f'v{len(state["versions"])+1}'
        directory=self.path(state['id'])/'versions'/vid/'artifacts'
        materialize(directory,answer)
        version={'id':vid,'round':state['round'],'proposal':answer,'digest':fingerprint(answer),
                 'artifact_digest':artifact_digest,'source_support':sorted(support),
                 'artifact_hashes':inventory(directory),'checks':[]}
        version['check_input_sha256']=fingerprint({'artifacts':version['artifact_hashes'],'evidence':state['frozen']['evidence']})
        state['versions'].append(version);self.enter(state,'verify')

    def refresh_issues(self,state):
        checks={r['check_id']:r for r in self.current(state)['checks']}
        for issue in state['issues']:
            if issue.get('failed_check') in checks:
                passed=checks[issue['failed_check']]['passed']
                issue['status']='check_passed_scoped' if passed else 'open'
                issue['verification_version']=self.current(state)['id']

    def critique(self,state,rows):
        version=self.current(state);known={i['id']:i for i in state['issues']};valid=[]
        for row in rows:
            answer=validate(row['result'],CRITIQUE);errors=[]
            if answer['verdict']=='accept' and any(i['severity'] in {'major','blocking'} for i in answer['issues']):errors.append('approval_has_major_issue')
            if answer['verdict'] in {'revise','reject'} and not answer['issues']:errors.append('criticism_requires_concrete_issue')
            for raw in answer['issues']:
                if not anchor_valid(raw['anchor'],state,version):errors.append('unknown_or_unmatched_criticism_anchor')
            state['reviews'].append({'round':state['round'],'member':row['member'],'result':answer,'errors':errors})
            if errors:
                state['protocol_errors'].append({'round':state['round'],'task_id':row['id'],'errors':errors,'result':answer})
                continue
            valid.append(answer)
            for raw in answer['issues']:
                key='I'+fingerprint({'problem':normalize(raw['problem']),'anchor':raw['anchor']})[:16]
                old=known.get(key)
                if old:
                    old['last_round']=state['round'];continue
                failed_check=next((r['check_id'] for r in version['checks'] if raw['anchor']['kind']=='check' and raw['anchor']['ref']==r['check_id'] and not r['passed']),None)
                issue={'id':key,**raw,'first_round':state['round'],'last_round':state['round'],
                       'status':'open','failed_check':failed_check,
                       'evidence_status':'reproduced_check_failure' if failed_check else 'anchor_matches_unverified_criticism'}
                state['issues'].append(issue);known[key]=issue
        if len(valid)!=len(state['members'])-1:self.conclude(state,'invalid_or_missing_criticism')
        elif any(r['verdict']=='reject' for r in valid):self.conclude(state,'reviewer_rejected')
        elif all(r['verdict']=='accept' for r in valid):self.conclude(state,'critics_accept')
        elif state['round']>=state['max_rounds']:self.conclude(state,'round_budget')
        else:
            state['round']+=1;self.enter(state,'revise')

    def conclude(self,state,reason):
        state['pending_stop_reason']=reason
        if state['versions'] and time.time()<state['deadline']:self.enter(state,'chair')
        else:self.finish(state,reason)

    def pending_questions(self,state):
        """Controller-owned follow-ups survive even when the chair omits them."""
        questions=[{'kind':'issue','ref':i['id'],'question':i['problem'],
                    'suggested_verification':i['suggested_change'],'anchor':i['anchor']}
                   for i in self.open_issues(state)]
        selected=self.selected(state)
        receipts={r['check_id']:r for r in selected['checks']} if selected else {}
        for check in state['checks']:
            receipt=receipts.get(check['id'])
            if not receipt or not receipt['passed']:
                questions.append({'kind':'check','ref':check['id'],
                                  'question':'Can the selected artifacts pass this frozen check?',
                                  'suggested_verification':receipt['summary'] if receipt else 'Run the configured check on a valid candidate.'})
        if not state['checks']:
            questions.append({'kind':'verification_gap','ref':'no_checks',
                              'question':'Which independent checks can establish the required task outcome?',
                              'suggested_verification':'Configure task-specific checks before a new run; model agreement alone is insufficient.'})
        if state['protocol_errors']:
            questions.append({'kind':'protocol','ref':'invalid_evidence',
                              'question':'Can invalid or missing evidence be replaced with checkable support?',
                              'suggested_verification':'Inspect the retained protocol errors before treating criticism or agreement as supported.'})
        return questions

    def finish(self,state,reason):
        selected=self.selected(state)
        all_checks=bool(selected and state['checks'] and len(selected['checks'])==len(state['checks']) and all(r['passed'] for r in selected['checks']))
        state.update(status='completed_scoped' if all_checks and reason=='critics_accept' and selected['id']==self.current(state)['id'] and not self.open_issues(state) and not state['protocol_errors'] else 'needs_evidence',
                     stop_reason=reason,selected_version=selected['id'] if selected else None,ended_at=time.time(),stage='done')
        state['quality'].update(configured_checks_passed=all_checks,verification_scope=[c['id'] for c in state['checks']],
                                unresolved_issues=[i['id'] for i in self.open_issues(state)],factual_correctness_verified=False)
        state['pending_questions']=self.pending_questions(state)
        with self.pool.connect() as c:c.execute("UPDATE tasks SET status='cancelled' WHERE group_id=? AND status='queued'",(state['id'],))
        self.save(state);return state

    def advance(self,did):
        root=self.path(did)
        try:
            with file_lock(root/'controller.lock'):
                state=self.get(did)
                assert_frozen(root,state)
                for v in state['versions']:
                    if inventory(root/'versions'/v['id']/'artifacts')!=v['artifact_hashes']:raise ValueError('Saved proposal artifacts changed')
                if state['status']!='running':self.save(state);return state
                if (root/'stop.request').exists():return self.finish(state,'stop_requested')
                if time.time()>=state['deadline']:return self.finish(state,'wall_clock_budget')
                if state['stage']=='verify':
                    version=self.current(state)
                    existing={p.parent.name for p in (root/'verification').glob('*/receipt.json')}
                    version['checks']=verify(root,state,version)
                    reused=[r['check_id'] for r in version['checks']
                            if fingerprint({'proposal':version['check_input_sha256'],
                                            'check':next(c for c in state['checks'] if c['id']==r['check_id'])}) in existing]
                    state['progress'].append({'round':state['round'],'stage':'verify','version':version['id'],
                                              'checked':[r['check_id'] for r in version['checks']],
                                              'reused_receipts':reused,
                                              'passed':[r['check_id'] for r in version['checks'] if r['passed']],
                                              'scope':'Frozen checks only; cached receipts are not new executions'})
                    self.refresh_issues(state)
                    if time.time()>=state['deadline']:return self.finish(state,'wall_clock_budget')
                    self.enter(state,'critique');self.save(state);return state
                for spec in state['task_specs']:
                    if time.time()<spec['deadline']:self.pool.enqueue(did,f'r{state["round"]}_{state["stage"]}',spec['eligible'],spec['request'],spec['deadline'],task_id=spec['id'])
                tasks={t['id']:t for t in self.pool.tasks(did)}
                rows=[tasks[s['id']] for s in state['task_specs'] if s['id'] in tasks]
                settled=len(rows)==len(state['task_specs']) and all(r['status'] in {'completed','failed','expired','cancelled'} for r in rows)
                if not settled and time.time()<state['stage_deadline']:return state
                done=[r for r in rows if r['status']=='completed']
                if len(done)!=len(state['task_specs']):
                    if state['stage']=='chair':return self.finish(state,state.get('pending_stop_reason','missing_chair'))
                    self.conclude(state,'missing_stage_result');self.save(state);return state
                try:
                    if state['stage'] in {'propose','revise'}:self.accept_proposal(state,done[0]['result'])
                    elif state['stage']=='critique':self.critique(state,done)
                    else:
                        answer=validate(done[0]['result'],CHAIR)
                        if answer['candidate_version']!=self.selected(state)['id']:raise ValueError('Chair cannot replace controller-selected artifacts')
                        state['chair_summary']=answer
                        return self.finish(state,state['pending_stop_reason'])
                except ValueError as exc:
                    state['protocol_errors'].append({'round':state['round'],'stage':state['stage'],'error':str(exc)})
                    if state['stage']=='chair':return self.finish(state,'invalid_chair_result')
                    self.conclude(state,'invalid_stage_result')
                self.save(state);return state
        except BlockingIOError:return self.get(did)

    def fail(self,did):
        try:
            with file_lock(self.path(did)/'controller.lock'):
                state=self.get(did);state['protocol_errors'].append({'error':'controller_or_frozen_input_error'})
                self.finish(state,'controller_error')
        except BlockingIOError:pass

    def save(self,state):
        root=self.path(state['id']);atomic_json(root/'state.json',state)
        lines=[f'# Adversarial task {state["id"]}','',state['question'],'',f'Status: {state["status"]}; round {state["round"]}/{state["max_rounds"]}; stage: {state["stage"]}',
               '', 'Chair/model agreement is advisory. Checks certify only their declared scope; factual correctness is not generally verified.']
        selected=self.selected(state)
        if selected:
            answer=next(a['content'] for a in selected['proposal']['artifacts'] if a['id']=='answer')
            lines+=['',f'## Controller-selected candidate {selected["id"]}','',answer,'','## Independent scoped checks','',dumps(selected['checks'])]
        lines+=['','## Remaining issues','',dumps(self.open_issues(state)),'','## Unsubstantiated or invalid criticism','',dumps(state['protocol_errors'])]
        lines+=['','## Questions requiring verification','',dumps(self.pending_questions(state)),
                '', '## Progress and verification ledger','',dumps(state['progress'])]
        if state.get('chair_summary'):lines+=['','## Chair summary (advisory)','',dumps(state['chair_summary'])]
        if state.get('stop_reason'):lines+=['',f'Stop reason: {state["stop_reason"]}']
        atomic_text(root/'report.md','\n'.join(lines)+'\n');atomic_json(root/'transcript.json',state)
