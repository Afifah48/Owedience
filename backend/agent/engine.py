import asyncio
from collections import defaultdict
from pydantic import ValidationError
from backend.models.domain import AuditEvent, Status
from backend.wizard.adapter import ingest
from backend.agent.tools import execute
from backend.agent.provider import ProviderError
from backend.agent.system_prompt import RULES
from backend.services.finance import PolicyError


def public_state(e):
    state = e.model_dump(mode='json', exclude={'audit_events','processed_event_ids'})
    for ev in state['evidence']:
        ev['payload'].pop('audio_base64',None)
        ev['payload'].pop('data_base64',None)
    from backend.services.follow_up import next_check, eligibility
    for o in state['obligations']:
        entity = next(x for x in e.obligations if x.id==o['id'])
        due=next_check(e,entity)
        o['next_check_at']=due.isoformat() if due else None
        o['reminder_eligibility']={'eligible':eligibility(e,entity)[0],'reason':eligibility(e,entity)[1],'send_allowed':eligibility(e,entity,sending=True)[0]}
        o['amount_remaining'] = 0 if o['status'] == 'WAIVED' else o['amount']-o['amount_paid']
    return state


def record_audit(e, ev, before, decision, reason, rules, action, request=None, response=None, accepted=True):
    request = request or {}
    response = response or {}
    e.audit_events.append(AuditEvent(input_received={'content':ev.content,'evidence_id':ev.id,'connector':ev.connector} if ev else {'event':'resume'},
        input_source=ev.origin if ev else 'system', real_world_source=ev.source if ev else 'system', connector=request.get('connector') or {'create_settlement_request':'pine_labs + messaging','request_clarification':'messaging','remind':'messaging','escalate':'messaging'}.get(action,ev.connector if ev else 'agent'),
        state_before=str(before),decision=decision,concise_reason=reason,rule_ids=rules,action=action,
        exact_message=response.get('exact_message') or request.get('message'),recipient=request.get('recipient') or response.get('recipient'),
        tool_request=request,tool_response=response,state_after=str(e.status),accepted=accepted))


class Engine:
    def __init__(self, repository, provider, rails, max_iterations=16):
        self.repository = repository
        self.provider = provider
        self.rails = rails
        self.max_iterations = max_iterations
        self.locks = defaultdict(asyncio.Lock)
    async def handle(self, id, event=None):
        async with self.locks[id]:
            e = self.repository.get(id)
            if e is None: raise KeyError(id)
            ev = None
            if event:
                before = e.status
                ev = ingest(e,event)
                if ev is None: return e
                record_audit(e,ev,before,'RECEIVED','External event preserved with provenance.',['U1'],'normalise_event',response={'verification_status':ev.verification_status})
                self.repository.save(e)
            if e.status == Status.CLOSED: return e
            e.agent_notice = None
            observation = {'status':'EVENT_RECEIVED' if event else 'RESUMED'}
            if not event and e.audit_events and e.audit_events[-1].action in ('loop_guard','provider'):
                last_rejection=next((a for a in reversed(e.audit_events[-5:]) if not a.accepted and a.action not in ('loop_guard','provider')),None)
                if last_rejection:
                    observation['previous_policy_rejection']={'tool':last_rejection.action,'error':last_rejection.concise_reason,'arguments':last_rejection.tool_request}
            seen = set()
            rejected = 0
            for _ in range(self.max_iterations):
                before = e.status
                try:
                    d = await self.provider.decide(public_state(e),observation)
                except ProviderError as error:
                    e.agent_notice = 'I have saved the expense. Analysis is waiting for a connection.'
                    record_audit(e,ev,before,'WAIT',str(error),['E1','E3'],'provider',response={'status':'UNAVAILABLE'},accepted=False)
                    e.audit_events[-1].provider_call = error.receipt
                    self.repository.save(e)
                    return e
                fingerprint = d.model_dump_json(exclude={'reason','rule_ids','affected_entities'})
                if fingerprint in seen:
                    record_audit(e,ev,before,'WAIT','Repeated action stopped without changing the ledger.',['E3'],'loop_guard',accepted=False)
                    self.repository.save(e)
                    return e
                seen.add(fingerprint)
                trial = e.model_copy(deep=True)
                try:
                    response = await execute(trial,d,self.rails)
                    e = trial
                    observation = response
                    record_audit(e,ev,before,d.tool.upper(),d.reason,d.rule_ids,d.tool,d.model_dump(mode='json'),response)
                except (PolicyError, ValidationError, KeyError, TypeError) as error:
                    safe_error = str(error) if isinstance(error, PolicyError) else 'Invalid tool or connector result; last verified state preserved'
                    observation = {'status':'POLICY_REJECTED','error':safe_error}
                    if d.tool == 'propose_allocation' and 'total' in safe_error: e.status = Status.INCONSISTENT
                    record_audit(e,ev,before,d.tool.upper(),safe_error,list(dict.fromkeys(d.rule_ids+['E3'])),d.tool,d.model_dump(mode='json'),observation,False)
                    rejected += 1
                e.audit_events[-1].provider_call = d._provider_receipt
                self.repository.save(e)
                if d.tool == 'wait' or e.status == Status.CLOSED or observation.get('requires_external_input') or observation.get('status') == 'PENDING_EXTERNAL_RESPONSE': return e
                if rejected >= 3:
                    e.agent_notice = 'I need a little help to safely continue. Your existing shares are saved.'
                    record_audit(e,ev,e.status,'WAIT','Three unsafe actions rejected; awaiting human review.',['E3'],'loop_guard',accepted=False)
                    self.repository.save(e)
                    return e
            e.agent_notice = 'I have paused here to keep the expense safe. You can ask me to continue.'
            record_audit(e,ev,e.status,'WAIT','Iteration budget reached.',['E3'],'loop_guard')
            self.repository.save(e)
            return e
