import asyncio
import json
from datetime import timedelta
from pathlib import Path
import pytest
from backend.models.domain import Episode, Component, Evidence, Citation, Status, now
from backend.agent.decision_schema import ADAPTER
from backend.agent.tools import execute
from backend.agent.engine import Engine
from backend.agent.provider import ProviderError
from backend.connectors.base import Rails
from backend.database.repository import Repository
from backend.services.finance import PolicyError, allocate, split_minor, validate_conservation, can_close, reminder_eligibility
from backend.wizard.events import HumanMessage, PaymentEvent, FailureEvent, ClockEvent, ShipmentEvent
from backend.wizard.adapter import ingest


def decision(tool, **kwargs):
    return ADAPTER.validate_python({'tool':tool,'reason':'A concise supported decision.','rule_ids':['U1'],**kwargs})


def run(e,tool,**kwargs):
    if tool=='remind':
        kwargs.pop('request_id',None)
        kwargs.setdefault('obligation_id',e.obligations[0].id if e.obligations else 'missing')
        kwargs.setdefault('message','A gentle reminder: {{remaining}} remains for this shared expense.')
    return asyncio.run(execute(e,decision(tool,**kwargs),Rails()))


def expense(amount=10000, category='Shared groceries'):
    e = Episode(title=category,payer='A',participants=['A','B','C'],total=amount,
                components=[Component(description='Shared part',amount=amount//2),Component(description='Individual part',amount=amount-amount//2)])
    ev = Evidence(type='human_message',content='A and B shared the first part equally. C alone had the second part.',source='A')
    e.evidence.append(ev)
    e.status = Status.UNDERSTANDING
    return e


def reconstruct(e,second=True):
    ev = e.evidence[0]
    items=[{'component_id':e.components[0].id,'consumers':['A','B'],'weights':{'A':1,'B':1},'citations':[{'evidence_id':ev.id,'quote':'A and B shared the first part equally.'}]}]
    if second: items.append({'component_id':e.components[1].id,'consumers':['C'],'weights':{'C':1},'citations':[{'evidence_id':ev.id,'quote':'C alone had the second part.'}]})
    run(e,'reconstruct_context',items=items)


def ledger():
    e=expense();reconstruct(e);run(e,'propose_allocation');return e


def response(e,kind,ids,actor):
    text={'CONFIRM':'These shares look right.','DISPUTE':'I did not share these parts.','WAIVE':'I will cover these shares.','RESOLVE_DISPUTE':'I agree to the existing amount.'}[kind]
    ev=ingest(e,HumanMessage(actor=actor,content=text,intent=kind,obligation_ids=ids))
    return run(e,'record_response',kind=kind,obligation_ids=ids,evidence_id=ev.id,quote=text)


def settle(e,debtor):
    ids=[o.id for o in e.obligations if o.debtor==debtor]
    response(e,'CONFIRM',ids,debtor)
    return run(e,'create_settlement_request',debtor=debtor,obligation_ids=ids)


def payment(e,debtor,amount,reference,verified=True,payment_id='receipt-1'):
    ingest(e,PaymentEvent(actor='Payment rail',payment_id=payment_id,amount=amount,debtor=debtor,creditor=e.payer,currency=e.currency,reference=reference,verified=verified))
    return run(e,'match_payment',payment_id=payment_id)


def test_complete_context_reconciles():
    e=ledger();validate_conservation(e);assert e.status==Status.AWAITING_CONFIRMATION


def test_missing_context_targeted_question_preserves_context():
    e=expense();reconstruct(e,False)
    run(e,'request_clarification',recipient='A',component_ids=[e.components[1].id],message='Who shared the second part?')
    assert e.components[0].consumers==['A','B'] and not e.allocations
    assert e.clarifications[0].affected_components==[e.components[1].id]


def test_conflicting_evidence_preserved():
    e=expense();reconstruct(e)
    ev=ingest(e,HumanMessage(actor='B',content='C did not have the second part.'))
    run(e,'mark_context_conflict',component_ids=[e.components[1].id],evidence_id=ev.id)
    with pytest.raises(PolicyError):run(e,'propose_allocation')
    assert e.components[1].consumers==['C'] and not e.obligations


def test_unequal_consumption_not_default_equal():
    e=ledger();assert {o.debtor:o.amount for o in e.obligations}=={'B':2500,'C':5000}


def test_conservation_failure_blocks_confirmation():
    e=ledger();e.total+=1
    with pytest.raises(PolicyError):response(e,'CONFIRM',[e.obligations[0].id],'B')
    assert e.obligations[0].status=='UNCONFIRMED'


def test_explicit_confirmation_activates():
    e=ledger();settle(e,'B');assert e.obligations[0].status=='ACTIVE'


def test_silence_never_confirms():
    e=ledger();run(e,'wait');assert all(o.status=='UNCONFIRMED' for o in e.obligations)


def test_component_dispute_only_freezes_affected_share():
    e=expense();ev=e.evidence[0]
    ev.content='A and B shared both parts equally.'
    run(e,'reconstruct_context',items=[{'component_id':c.id,'consumers':['A','B'],'weights':{'A':1,'B':1},'citations':[{'evidence_id':ev.id,'quote':ev.content}]} for c in e.components])
    run(e,'propose_allocation');ids=[o.id for o in e.obligations];response(e,'CONFIRM',ids,'B');response(e,'DISPUTE',[ids[1]],'B')
    assert e.obligations[0].status=='CONFIRMED' and e.obligations[1].status=='DISPUTED'
    run(e,'create_settlement_request',debtor='B',obligation_ids=[ids[0]])
    assert e.obligations[0].status=='ACTIVE'


def test_waiver_is_not_payment():
    e=ledger();o=e.obligations[0];response(e,'WAIVE',[o.id],'A');assert o.status=='WAIVED' and o.amount_paid==0 and o.remaining==0


def test_full_payment_settles():
    e=ledger();r=settle(e,'B');payment(e,'B',2500,r['reference']);assert e.obligations[0].status=='PAID'


def test_partial_payment_leaves_remainder():
    e=ledger();r=settle(e,'C');payment(e,'C',2000,r['reference']);o=e.obligations[1];assert o.amount_paid==2000 and o.remaining==3000 and o.status=='PARTIALLY_PAID'


def test_ambiguous_payment_never_changes_ledger():
    e=ledger();settle(e,'B');result=payment(e,'B',2500,None);assert result['status']=='PAYMENT_MATCH_AMBIGUOUS' and e.obligations[0].amount_paid==0


def test_connector_failure_preserves_verified_ledger():
    e=ledger();ids=[e.obligations[0].id];response(e,'CONFIRM',ids,'B');ingest(e,FailureEvent(actor='World',connector='pine_labs',content='Timed out'))
    with pytest.raises(PolicyError):run(e,'create_settlement_request',debtor='B',obligation_ids=ids)
    assert e.obligations[0].status=='CONFIRMED' and not e.settlement_requests


def authorize(e,debtor='B',**policy):
    from backend.services.follow_up import human_action, FollowUpAction
    from backend.models.domain import FollowUpPolicy
    for o in e.obligations:
        if o.debtor==debtor: human_action(e,o,FollowUpAction(actor=e.payer,action='authorize',policy=FollowUpPolicy(maximum=2,reminder_approval_mode='AUTO_WITHIN_LIMITS',**policy)))


def eligible_clock(e,r):
    # Use next allowed local hour at least two days later.
    local=(r.created_at+timedelta(days=2)).astimezone(__import__('datetime').timezone(timedelta(minutes=330)))
    e.clock=local.replace(hour=12,minute=0,second=0,microsecond=0)


def test_permitted_reminder_is_neutral():
    e=ledger();settle(e,'B');authorize(e);r=e.settlement_requests[0];eligible_clock(e,r);result=run(e,'remind',request_id=r.id)
    assert r.reminders==1 and 'gentle reminder' in result['exact_message']


def test_reminder_limit_escalates():
    e=ledger();settle(e,'B');authorize(e);r=e.settlement_requests[0];e.obligations[0].reminder_count=2;eligible_clock(e,r)
    with pytest.raises(PolicyError):run(e,'remind',request_id=r.id)
    run(e,'escalate',message='I have reached your follow-up limit. I’ll leave this with you.');assert e.status==Status.ESCALATED


def test_disputed_money_never_reminded():
    e=ledger();settle(e,'B');r=e.settlement_requests[0];response(e,'DISPUTE',r.obligation_ids,'B');eligible_clock(e,r)
    with pytest.raises(PolicyError):run(e,'remind',request_id=r.id)


def test_unresolved_obligation_prevents_closure():
    with pytest.raises(PolicyError):run(ledger(),'close_episode')


def test_all_resolved_closes_and_stops():
    e=ledger();response(e,'WAIVE',[o.id for o in e.obligations],'A');run(e,'close_episode');assert e.status==Status.CLOSED
    with pytest.raises(PolicyError):run(e,'remind',request_id='anything')

@pytest.mark.parametrize('category,amount',[('Changed restaurant',11703),('Cab ride',7899),('Hotel booking',88001),('Movie tickets',4900)])
def test_arbitrary_expenses_without_code_changes(category,amount):
    e=expense(amount,category);reconstruct(e);run(e,'propose_allocation');validate_conservation(e);assert sum(a.amount for a in e.allocations)==amount


def test_largest_remainder_arithmetic_exact():
    for amount in range(1,99):
        result=split_minor(amount,{'Z':3,'A':7,'B':1});assert sum(result.values())==amount
    assert split_minor(1,{'B':1,'A':1})=={'B':0,'A':1}


def test_other_person_cannot_confirm_or_waive():
    e=ledger()
    with pytest.raises(PolicyError):response(e,'CONFIRM',[e.obligations[0].id],'A')
    with pytest.raises(PolicyError):response(e,'WAIVE',[e.obligations[0].id],'B')


def test_duplicate_payment_is_idempotent():
    e=ledger();r=settle(e,'B');payment(e,'B',1000,r['reference'])
    again=ingest(e,PaymentEvent(actor='Rail',payment_id='receipt-1',amount=1000,debtor='B',creditor='A',reference=r['reference'],verified=True))
    assert again is None and len(e.payments)==1 and e.obligations[0].amount_paid==1000


def test_same_payment_id_conflicting_payload_rejected():
    e=ledger();r=settle(e,'B');payment(e,'B',1000,r['reference'])
    with pytest.raises(PolicyError):ingest(e,PaymentEvent(actor='Rail',payment_id='receipt-1',amount=2000,debtor='B',creditor='A',reference=r['reference'],verified=True))


def test_overpayment_and_wrong_identity_rejected():
    e=ledger();r=settle(e,'B');payment(e,'C',2500,r['reference']);assert e.obligations[0].amount_paid==0
    payment(e,'B',2600,r['reference'],payment_id='overpay');assert e.obligations[0].amount_paid==0


def test_voice_and_delivery_cannot_prove_consumption():
    e=expense();ev=ingest(e,ShipmentEvent(actor='Courier',content='B received the package',reference='shipment-1'))
    with pytest.raises(PolicyError):run(e,'reconstruct_context',items=[{'component_id':e.components[0].id,'consumers':['B'],'weights':{'B':1},'citations':[{'evidence_id':ev.id,'quote':ev.content}]}])


def test_nonexistent_citations_and_participants_rejected():
    e=expense()
    with pytest.raises(PolicyError):run(e,'reconstruct_context',items=[{'component_id':e.components[0].id,'consumers':['X'],'weights':{'X':1},'citations':[{'evidence_id':e.evidence[0].id,'quote':'made up'}]}])


def test_duplicate_clarification_blocked():
    e=expense();kwargs={'recipient':'A','component_ids':[e.components[0].id],'message':'Who shared this?'};run(e,'request_clarification',**kwargs)
    with pytest.raises(PolicyError):run(e,'request_clarification',**kwargs)


def test_clock_cannot_move_backwards():
    e=expense()
    with pytest.raises(PolicyError):ingest(e,ClockEvent(actor='World',timestamp=e.clock-timedelta(days=1)))

class FakeProvider:
    # TEST-ONLY: explicit decisions exercise contracts; never shipped as runtime fallback.
    def __init__(self,decisions):self.decisions=iter(decisions);self.observations=[]
    async def decide(self,state,observation):self.observations.append(observation);return next(self.decisions)


def test_real_loop_observes_tools_and_rejects_partial_mutations(tmp_path):
    e=expense();ev=e.evidence[0];repo=Repository(str(tmp_path/'test.sqlite3'));repo.save(e)
    provider=FakeProvider([decision('reconstruct_context',items=[{'component_id':e.components[0].id,'consumers':['A','B'],'weights':{'A':1,'B':1},'citations':[{'evidence_id':ev.id,'quote':'A and B shared the first part equally.'}]},{'component_id':e.components[1].id,'consumers':['C'],'weights':{'C':1},'citations':[{'evidence_id':ev.id,'quote':'FABRICATED'}]}]),decision('wait')])
    result=asyncio.run(Engine(repo,provider,Rails()).handle(e.id))
    assert not result.components[0].consumers and not result.allocations
    assert provider.observations[1]['status']=='POLICY_REJECTED' and not result.audit_events[0].accepted


def test_missing_key_records_honest_wait(tmp_path):
    class Missing:
        async def decide(self,state,observation):raise ProviderError('LLM_NOT_CONFIGURED')
    e=expense();repo=Repository(str(tmp_path/'test.sqlite3'));repo.save(e)
    result=asyncio.run(Engine(repo,Missing(),Rails()).handle(e.id,HumanMessage(actor='A',content='Here is the context.')))
    assert result.agent_notice and len(result.evidence)==2 and not result.obligations
    assert result.audit_events[-1].concise_reason=='LLM_NOT_CONFIGURED'


def test_dispute_resolution_requires_both_people():
    e=ledger();o=e.obligations[0];response(e,'DISPUTE',[o.id],'B');response(e,'RESOLVE_DISPUTE',[o.id],'A');assert o.status=='DISPUTED'
    response(e,'RESOLVE_DISPUTE',[o.id],'B');assert o.status=='CONFIRMED'


def test_reminder_channel_hours_interval_checked():
    e=ledger();settle(e,'B');r=e.settlement_requests[0]
    assert not reminder_eligibility(e,r)[0]
    eligible_clock(e,r);assert not reminder_eligibility(e,r,'voice')[0]
    e.clock=e.clock.replace(hour=23);assert not reminder_eligibility(e,r)[0]
