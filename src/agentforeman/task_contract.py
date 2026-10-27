"""Frozen human-confirmed task bindings shared by execution controllers."""
from .debate_evidence import fingerprint
from .files import atomic_json, digest


def bind(root,state,binding):
    if binding is None:return
    if not isinstance(binding,dict) or set(binding)!={'briefing_id','version','sha256','bundle'}:
        raise ValueError('Invalid confirmed task contract')
    if fingerprint(binding['bundle'])!=binding['sha256']:raise ValueError('Task contract digest mismatch')
    atomic_json(root/'task-contract.json',binding)
    state['task_contract']=binding
    state['frozen']['contract']=digest(root/'task-contract.json')


def existing(controller,run_id,binding):
    if run_id is None:return None
    root=controller.path(run_id)
    if (root/'state.json').exists():
        state=controller.get(run_id)
        if binding is None or state.get('task_contract')!=binding:
            raise ValueError('Run ID already belongs to another task contract')
        from .debate_evidence import assert_frozen
        assert_frozen(root,state)
        return run_id


def manual_questions(state):
    binding=state.get('task_contract')
    if not binding:return []
    return [{'kind':'contract_acceptance','ref':str(i+1),'question':c['criterion'],
             'suggested_verification':'This criterion requires independent manual review; model agreement cannot certify it.'}
            for i,c in enumerate(binding['bundle']['contract']['acceptance']) if c['verification']=='manual']
