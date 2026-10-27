"""Durable parallel task decomposition, integration and independent acceptance."""
from __future__ import annotations

import math
import secrets
import time

from .council import obj
from .debate import Debate, PROPOSAL, RULES, txt, seq
from .debate_evidence import prepare, assert_frozen, fingerprint
from .platforms import file_lock
from .pool import dumps, name, validate

ASSIGNMENT=obj(member=txt(64),goal=txt(4000),deliverables=seq(txt(200),1,8),acceptance=txt(2000))
PLAN=obj(summary=txt(4000),assignments=seq(ASSIGNMENT,1,4),integration_contract=txt(4000))


class Cooperation(Debate):
    def __init__(self,project):
        super().__init__(project)
        self.root=self.project/'.foreman'/'cooperations'

    def reviewer_members(self,state):return state['reviewers']

    def create(self,question,members,chair,*,reviewers,evidence=None,checks=None,
               max_rounds=3,budget_seconds=3600,stage_seconds=600,run_id=None,task_contract=None):
        from .task_contract import existing, bind
        resumed=existing(self,run_id,task_contract)
        if resumed:return resumed
        if not isinstance(question,str) or not question.strip() or len(question)>16000:
            raise ValueError('Provide a bounded question')
        if not isinstance(members,list) or not 2<=len(members)<=5:
            raise ValueError('Choose an integrator followed by 1..4 contributors')
        if not isinstance(reviewers,list) or not 1<=len(reviewers)<=3:
            raise ValueError('Choose 1..3 independent reviewers')
        identities=[*members,*reviewers,chair]
        for member in identities:name(member)
        if len(set(identities))!=len(identities):
            raise ValueError('Contributors, integrator, reviewers and chair must be distinct identities')
        enabled={m['id'] for m in self.pool.members() if m['enabled']}
        if set(identities)-enabled:raise ValueError('All participants must be registered and enabled')
        if type(max_rounds) is not int or not 1<=max_rounds<=5:raise ValueError('Choose 1..5 review rounds')
        for value in (budget_seconds,stage_seconds):
            if type(value) not in (int,float) or not math.isfinite(value) or not 10<=value<=86400:
                raise ValueError('Finite budgets 10..86400 seconds required')
        did=run_id or secrets.token_hex(6);root=self.path(did)
        sources,frozen_checks,frozen=prepare(self.project,root,[] if evidence is None else evidence,[] if checks is None else checks)
        if len(dumps(sources).encode())>100000:raise ValueError('Combined evidence exceeds 100 KB')
        now=time.time()
        state={'id':did,'question':question,'mode':'cooperative','members':members,'reviewers':reviewers,'chair':chair,
               'created_at':now,'deadline':now+budget_seconds,'stage_seconds':stage_seconds,
               'max_rounds':max_rounds,'round':1,'status':'running','stage':'plan','plan':None,'contributions':[],
               'evidence':sources,'checks':frozen_checks,'frozen':frozen,'versions':[],'issues':[],
               'reviews':[],'protocol_errors':[],'progress':[],
               'quality':{'factual_correctness_verified':False,'chair_is_advisory':True}}
        bind(root,state,task_contract)
        self.enter(state,'plan');self.save(state);self.advance(did)
        return did

    def context(self,state):
        context=super().context(state)
        context.update(plan=state['plan'],contributions=state['contributions'] if not state['versions'] else [],
                       contributors=state['members'][1:],integrator=state['members'][0])
        return context

    def enter(self,state,stage):
        if stage not in {'plan','contribute'}:
            super().enter(state,stage)
            if stage=='propose':
                for spec in state['task_specs']:
                    spec['request']['prompt']=('Integrate ALL recorded contributions under the integration contract. '
                        'Resolve inconsistencies explicitly. Only the combined artifacts will be independently checked.\n'
                        +spec['request']['prompt'])
            return
        state.update(stage=stage,stage_deadline=min(time.time()+state['stage_seconds'],state['deadline']))
        context=self.context(state)
        if stage=='plan':
            extra=('Decompose the task into independent parallel assignments: exactly one for each listed contributor, '
                   'no others. State concrete deliverables, acceptance criteria, and an integration contract. '
                   'Assignments cannot depend on unfinished peer outputs. No executable commands.')
            jobs=[(state['members'][0],PLAN,context,extra)]
        else:
            jobs=[(a['member'],self.proposal_schema(state),{'question':state['question'],'evidence':state['evidence'],
                   'task_contract':state.get('task_contract'),
                   'assignment':a,'integration_contract':state['plan']['integration_contract']},
                   'Perform only your assignment. Return concrete bounded artifacts including answer, '
                   'with supplied source quotations for claims. responses must be empty. '
                   'Your contribution is not yet an accepted final answer. If a task contract is present, set '
                   'scope_change.required=true when fulfilling the assignment needs a change to its scope/authority; '
                   'otherwise false. Explain in scope_change.reason.') for a in state['plan']['assignments']]
        state['task_specs']=[{'id':f'{state["id"]}_r1_{stage}_{i}','eligible':[member],
                             'request':{'prompt':RULES+'\n'+extra+'\n'+dumps(ctx),'schema':schema},
                             'deadline':state['stage_deadline']}
                            for i,(member,schema,ctx,extra) in enumerate(jobs)]

    def advance(self,did):
        # Plan/contribution transitions use the same controller lock and immutable
        # task IDs as subsequent proposal/review/verification transitions.
        if self.get(did)['stage'] not in {'plan','contribute'}:return super().advance(did)
        root=self.path(did)
        try:
            with file_lock(root/'controller.lock'):
                state=self.get(did)
                if state['stage'] not in {'plan','contribute'}:return state
                assert_frozen(root,state)
                if state['status']!='running':return state
                if (root/'stop.request').exists():return self.finish(state,'stop_requested')
                if time.time()>=state['deadline']:return self.finish(state,'wall_clock_budget')
                for spec in state['task_specs']:
                    if time.time()<spec['deadline']:
                        self.pool.enqueue(did,f'r1_{state["stage"]}',spec['eligible'],spec['request'],spec['deadline'],task_id=spec['id'])
                tasks={t['id']:t for t in self.pool.tasks(did)}
                rows=[tasks[s['id']] for s in state['task_specs'] if s['id'] in tasks]
                settled=len(rows)==len(state['task_specs']) and all(r['status'] in {'completed','failed','expired','cancelled'} for r in rows)
                if not settled and time.time()<state['stage_deadline']:return state
                done=[r for r in rows if r['status']=='completed']
                # Preserve partial results for diagnosis without silently integrating them.
                if state['stage']=='contribute':
                    state['contribution_tasks']=[{'id':r['id'],'member':r['member'],'eligible':r['eligible'],
                                                   'status':r['status'],'result':r.get('result')} for r in rows]
                if len(done)!=len(state['task_specs']):
                    state['protocol_errors'].append({'stage':state['stage'],'error':'missing_or_failed_contribution_or_plan'})
                    return self.finish(state,'missing_stage_result')
                try:
                    if state['stage']=='plan':
                        plan=validate(done[0]['result'],PLAN)
                        assigned=[a['member'] for a in plan['assignments']]
                        if len(assigned)!=len(set(assigned)) or set(assigned)!=set(state['members'][1:]):
                            raise ValueError('Plan must assign each registered contributor exactly once')
                        state['plan']=plan
                        state['progress'].append({'round':1,'stage':'plan','assignments':assigned})
                        self.enter(state,'contribute')
                    else:
                        contributions=[]
                        for row in done:
                            validate(row['result'],self.proposal_schema(state))
                            if state.get('task_contract') and row['result']['scope_change']['required']:
                                return self.pause_for_contract(state,row['member']+': '+row['result']['scope_change']['reason'])
                            self.validate_proposal(state,row['result'])
                            contributions.append({'member':row['member'],'task_id':row['id'],
                                                  'proposal':row['result'],'sha256':fingerprint(row['result'])})
                        state['contributions']=contributions
                        state['progress'].append({'round':1,'stage':'contribute','members':[r['member'] for r in done]})
                        self.enter(state,'propose')
                except ValueError as exc:
                    state['protocol_errors'].append({'stage':state['stage'],'error':str(exc)})
                    return self.finish(state,'invalid_stage_result')
                self.save(state);return state
        except BlockingIOError:return self.get(did)

    def pending_questions(self,state):
        questions=super().pending_questions(state)
        for task in state.get('contribution_tasks',[]):
            if task['status']!='completed':
                questions.append({'kind':'contribution','ref':task['id'],
                                  'question':'Missing usable contribution from '+', '.join(task.get('eligible',[])),
                                  'suggested_verification':'Inspect the worker receipt and complete this assignment before integration.'})
        return questions

    def save(self,state):
        super().save(state)
        root=self.path(state['id']);report=root/'report.md'
        from .files import atomic_text
        text=report.read_text(encoding='utf-8').replace('# Adversarial task','# Cooperative task',1)
        text+='\n## Decomposition and integration contract\n\n'+dumps(state.get('plan'))
        text+='\n\n## Contributions (not independently accepted)\n\n'+dumps(state.get('contributions',[]))+'\n'
        atomic_text(report,text)
