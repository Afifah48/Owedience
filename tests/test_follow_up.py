import asyncio
from datetime import datetime,timedelta,timezone
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.models.domain import FollowUpPolicy
from backend.services.follow_up import FollowUpAction,human_action,eligibility,next_check
from backend.services.finance import PolicyError
from backend.agent.engine import Engine
from backend.agent.decision_schema import ADAPTER
from backend.connectors.base import Rails
from backend.database.repository import Repository
from backend.wizard.adapter import ingest
from backend.wizard.events import HumanMessage,PaymentEvent
from tests.test_invariants import ledger,settle,run,response,payment,decision,FakeProvider


def ready(debtor='B'):
    e=ledger();e.clock=datetime(2026,1,1,6,30,tzinfo=timezone.utc)
    settle(e,debtor);o=next(x for x in e.obligations if x.debtor==debtor)
    return e,o


def human(e,o,action,actor=None,**p):
    return human_action(e,o,FollowUpAction(actor=actor or e.payer,action=action,policy=FollowUpPolicy(**p) if action in ('authorize','edit') else None))


def due(e,o): e.clock=next_check(e,o)
def send(e,o,rails=None):
    d=decision('remind',obligation_id=o.id,message='Hi {{debtor}}, {{remaining}} remains for {{expense}}. A quick reminder when convenient.')
    return asyncio.run(__import__('backend.agent.tools',fromlist=['execute']).execute(e,d,rails or Rails()))
def ask(e,o): return run(e,'request_reminder_approval',obligation_id=o.id,message='{{debtor}} still owes {{remaining}} for {{expense}}. Want me to send a reminder?')


def test_no_authorization_and_payment_reference_never_contact():
    e,o=ready();e.clock+=timedelta(days=4)
    assert not e.messages or all(m.get('recipient')!=o.debtor for m in e.messages)
    assert not eligibility(e,o)[0]
    with pytest.raises(PolicyError):send(e,o)
    assert o.follow_up_authorized=='NOT_DECIDED' and o.reminder_count==0


def test_scope_authorizes_only_selected_obligation():
    e,o=ready();settle(e,'C');human(e,o,'authorize',reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,o);send(e,o)
    other=e.obligations[1]
    with pytest.raises(PolicyError):send(e,other)
    assert other.follow_up_authorized=='NOT_DECIDED' and other.reminder_count==0


def test_decline_not_overridable_by_model_or_other_human():
    e,o=ready();human(e,o,'decline');e.clock+=timedelta(days=4)
    with pytest.raises(PolicyError):ask(e,o)
    with pytest.raises(PolicyError):send(e,o)
    with pytest.raises(PolicyError):human(e,o,'authorize',actor=o.debtor)
    with pytest.raises(ValidationError):ADAPTER.validate_python({'tool':'authorize','reason':'Override','rule_ids':['S8'],'obligation_id':o.id})
    assert o.follow_up_authorized=='DECLINED'
    human(e,o,'authorize');assert o.follow_up_authorized=='AUTHORIZED'


def test_ask_first_then_explicit_approval_sends_once():
    e,o=ready();human(e,o,'authorize');due(e,o)
    with pytest.raises(PolicyError):send(e,o)
    ask(e,o);assert o.reminder_approval and not o.reminder_approval.approved_at
    with pytest.raises(PolicyError):send(e,o)
    human(e,o,'approve');result=send(e,o)
    assert result['status']=='SIMULATED_DELIVERY' and o.reminder_count==1 and o.reminder_approval is None
    e.clock+=timedelta(days=2)
    with pytest.raises(PolicyError):send(e,o)


def test_ask_first_then_auto_and_optional_future_opt_in():
    e,o=ready();human(e,o,'authorize',maximum=3,reminder_approval_mode='ASK_FIRST_THEN_AUTO');due(e,o)
    with pytest.raises(PolicyError):send(e,o)
    ask(e,o);human(e,o,'approve');send(e,o);due(e,o);send(e,o)
    assert o.reminder_count==2
    human(e,o,'edit',maximum=3,reminder_approval_mode='ASK_EACH_TIME');due(e,o)
    with pytest.raises(PolicyError):send(e,o)


@pytest.mark.parametrize('hours',[1,7,24,48,72,168,240])
def test_frequency_and_custom_interval(hours):
    e,o=ready();human(e,o,'authorize',minimum_interval_hours=hours,reminder_approval_mode='AUTO_WITHIN_LIMITS')
    expected=next_check(e,o);e.clock=expected-timedelta(seconds=1)
    assert not eligibility(e,o)[0]
    e.clock=expected;assert eligibility(e,o,sending=True)[0]


def test_maximum_and_edit_do_not_reset_count():
    e,o=ready();human(e,o,'authorize',maximum=1,reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,o);send(e,o)
    e.clock+=timedelta(days=3)
    with pytest.raises(PolicyError):send(e,o)
    human(e,o,'edit',maximum=1,reminder_approval_mode='AUTO_WITHIN_LIMITS')
    assert o.reminder_count==1 and eligibility(e,o)[1]=='Reminder limit exhausted'


@pytest.mark.parametrize('start,end,local_hour,expected_hour',[('22:00','08:00',23,8),('22:00','08:00',7,8),('12:00','14:00',13,14)])
def test_quiet_hours_defer_to_next_allowed_minute(start,end,local_hour,expected_hour):
    e,o=ready();human(e,o,'authorize',quiet_start=start,quiet_end=end,reminder_approval_mode='AUTO_WITHIN_LIMITS')
    tz=timezone(timedelta(minutes=330));e.clock=datetime(2026,1,4,local_hour,tzinfo=tz)
    from backend.services.follow_up import quiet
    assert quiet(e,o)[0] and quiet(e,o)[1].hour==expected_hour
    with pytest.raises(PolicyError):send(e,o)
    e.clock=quiet(e,o)[1];assert eligibility(e,o)[0]


def test_pause_resume_and_snooze_are_human_controls():
    e,o=ready();human(e,o,'authorize');due(e,o);ask(e,o);human(e,o,'snooze')
    assert o.reminder_approval is None and not eligibility(e,o)[0]
    due(e,o);ask(e,o);human(e,o,'pause');assert o.reminder_approval is None
    with pytest.raises(PolicyError):send(e,o)
    human(e,o,'resume');assert o.follow_up_authorized=='AUTHORIZED' and not eligibility(e,o)[0]


def test_waiver_stops_without_recording_payment():
    e,o=ready();human(e,o,'authorize');human(e,o,'waive')
    assert o.status=='WAIVED' and o.amount_paid==0 and not eligibility(e,o)[0]
    with pytest.raises(PolicyError):human(e,o,'resume')


def test_partial_payment_invalidates_approval_and_mentions_remaining_only():
    e,o=ready();human(e,o,'authorize',maximum=3);due(e,o);ask(e,o);human(e,o,'approve')
    payment(e,'B',1000,e.settlement_requests[0].reference)
    assert o.remaining==1500 and o.reminder_approval is None
    with pytest.raises(PolicyError):send(e,o)
    ask(e,o);human(e,o,'approve');result=send(e,o)
    assert '15.00' in result['exact_message'] and '25.00' not in result['exact_message']


def test_full_payment_automatically_stops_and_retains_history():
    e,o=ready();human(e,o,'authorize',maximum=3,reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,o);send(e,o)
    payment(e,'B',o.remaining,e.settlement_requests[0].reference)
    assert o.status=='PAID' and next_check(e,o) is None and len(o.reminder_history)==1
    e.clock+=timedelta(days=100)
    with pytest.raises(PolicyError):send(e,o)


def test_pending_verified_payment_blocks_contact_before_llm_matches():
    e,o=ready();human(e,o,'authorize',reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,o)
    ingest(e,PaymentEvent(actor='Rail',payment_id='pending',amount=1000,debtor='B',creditor='A',reference=e.settlement_requests[0].reference,verified=True))
    with pytest.raises(PolicyError):send(e,o)


def test_dispute_installs_hold_before_llm_and_only_human_resumes():
    e,o=ready();human(e,o,'authorize');due(e,o);ask(e,o);human(e,o,'approve')
    ev=ingest(e,HumanMessage(actor='B',content='This component is disputed.',intent='DISPUTE',obligation_ids=[o.id]))
    assert o.status=='DISPUTED' and o.follow_up_authorized=='PAUSED' and not o.reminder_approval
    with pytest.raises(PolicyError):human(e,o,'resume')
    run(e,'record_response',kind='DISPUTE',obligation_ids=[o.id],evidence_id=ev.id,quote=ev.content)
    response(e,'RESOLVE_DISPUTE',[o.id],'A');response(e,'RESOLVE_DISPUTE',[o.id],'B')
    assert o.follow_up_authorized=='PAUSED'
    human(e,o,'resume');assert o.follow_up_authorized=='AUTHORIZED'


def test_two_components_same_person_are_independently_pursued():
    from tests.test_invariants import expense
    e=expense();ev=e.evidence[0];ev.content='A and B shared both parts equally.'
    run(e,'reconstruct_context',items=[{'component_id':c.id,'consumers':['A','B'],'weights':{'A':1,'B':1},'citations':[{'evidence_id':ev.id,'quote':ev.content}]} for c in e.components]);run(e,'propose_allocation');settle(e,'B')
    left,right=e.obligations
    human(e,left,'authorize',reminder_approval_mode='AUTO_WITHIN_LIMITS');human(e,right,'authorize',reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,left)
    response(e,'DISPUTE',[right.id],'B');send(e,left)
    assert left.reminder_count==1 and right.reminder_count==0 and right.follow_up_authorized=='PAUSED'


def test_separate_debtor_policies_and_scoped_escalation():
    e,left=ready();settle(e,'C');right=e.obligations[1]
    human(e,left,'authorize',maximum=1,minimum_interval_hours=24,reminder_approval_mode='AUTO_WITHIN_LIMITS')
    human(e,right,'authorize',maximum=3,minimum_interval_hours=72,reminder_approval_mode='AUTO_WITHIN_LIMITS')
    due(e,left);send(e,left);assert not eligibility(e,right)[0]
    run(e,'escalate',obligation_id=left.id,message='Your follow-up limit is reached. Over to you.')
    due(e,right);send(e,right)
    assert left.follow_up_escalated and not right.follow_up_escalated and right.reminder_count==1


def test_connector_failure_is_transactional_and_does_not_consume_approval(tmp_path):
    e,o=ready();human(e,o,'authorize');due(e,o);ask(e,o);human(e,o,'approve');e.connector_failures['messaging']='Temporary outage'
    repo=Repository(str(tmp_path/'fail.sqlite3'));repo.save(e)
    d=decision('remind',obligation_id=o.id,message='Hi {{debtor}}, {{remaining}} remains for {{expense}}.')
    result=asyncio.run(Engine(repo,FakeProvider([d,decision('wait')]),Rails()).handle(e.id))
    saved=result.obligations[0]
    assert saved.reminder_count==0 and saved.reminder_approval.approved_at and not saved.reminder_history
    assert not result.audit_events[-2].accepted


@pytest.mark.parametrize('message',['Pay 25.00 now for {{remaining}}','Last warning: {{remaining}}','C owes another amount. {{remaining}}','{{remaining}} {{remaining}}','{{remaining}} http://invented.test'])
def test_unsafe_or_stale_financial_wording_is_blocked(message):
    e,o=ready();human(e,o,'authorize',reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,o)
    with pytest.raises(PolicyError):run(e,'remind',obligation_id=o.id,message=message)


def test_approval_endpoint_enforces_creditor_and_audits_scope(tmp_path,monkeypatch):
    from backend import main
    e,o=ready();repo=Repository(str(tmp_path/'api.sqlite3'));repo.save(e)
    monkeypatch.setattr(main,'repo',repo);monkeypatch.setattr(main,'engine',Engine(repo,FakeProvider([decision('wait')]),Rails()))
    with TestClient(main.app) as client:
        path=f'/api/episodes/{e.id}/obligations/{o.id}/follow-up'
        assert client.post(path,json={'actor':'B','action':'authorize','policy':{}}).status_code==409
        result=client.post(path,json={'actor':'A','action':'authorize','policy':{}})
        assert result.status_code==200 and result.json()['obligations'][0]['authorized_by']=='A'
        assert repo.get(e.id).audit_events[-1].tool_response['obligation_id']==o.id

def test_monitor_without_authorization_makes_no_provider_call(tmp_path):
    from backend.services.monitor import monitor_once
    e,o=ready();e.clock+=timedelta(days=4);repo=Repository(str(tmp_path/'no-authority.sqlite3'));repo.save(e)
    provider=FakeProvider([]);provider.key='test-only'
    asyncio.run(monitor_once(Engine(repo,provider,Rails()),e.clock))
    assert not provider.observations and repo.get(e.id).obligations[0].reminder_count==0


def test_monitor_eligible_confirmed_obligation_before_payment_reference(tmp_path):
    from backend.services.monitor import monitor_once
    e=ledger();o=e.obligations[0];response(e,'CONFIRM',[o.id],o.debtor);human(e,o,'authorize');due(e,o)
    repo=Repository(str(tmp_path/'no-reference.sqlite3'));repo.save(e)
    provider=FakeProvider([decision('wait')]);provider.key='test-only'
    asyncio.run(monitor_once(Engine(repo,provider,Rails()),e.clock))
    assert provider.observations and repo.get(e.id).evidence[-1].type=='clock'


def test_resume_after_prior_reminder_starts_new_wait_interval():
    e,o=ready();human(e,o,'authorize',maximum=3,reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,o);send(e,o)
    human(e,o,'pause');e.clock+=timedelta(days=10);human(e,o,'resume')
    assert not eligibility(e,o)[0] and next_check(e,o)>=e.clock+timedelta(hours=24)


def test_episode_pause_cannot_be_overridden_by_scoped_permission():
    e,o=ready();human(e,o,'authorize',reminder_approval_mode='AUTO_WITHIN_LIMITS');due(e,o);e.reminder_policy.enabled=False
    assert next_check(e,o) is None
    with pytest.raises(PolicyError):send(e,o)


def test_message_ready_does_not_consume_approval_or_contact_count():
    class Pending:
        async def send_message(self,*args):return {'status':'MESSAGE_READY','mode':'wizard'}
    e,o=ready();human(e,o,'authorize');due(e,o);ask(e,o);human(e,o,'approve');rails=Rails();rails.messaging=Pending()
    result=send(e,o,rails)
    assert result['requires_external_input'] and o.reminder_count==0 and o.reminder_approval.approved_at


def test_approval_prompt_must_be_a_question():
    e,o=ready();human(e,o,'authorize');due(e,o)
    with pytest.raises(PolicyError):run(e,'request_reminder_approval',obligation_id=o.id,message='Hi debtor, a reminder for {{remaining}}.')


def test_loop_resume_retains_last_policy_rejection(tmp_path):
    from backend.agent.engine import record_audit
    e,o=ready();record_audit(e,None,e.status,'REMIND','Wrong reference',['E3'],'remind',response={'status':'POLICY_REJECTED'},accepted=False)
    record_audit(e,None,e.status,'WAIT','Repeated action stopped',['E3'],'loop_guard',accepted=False)
    repo=Repository(str(tmp_path/'resume.sqlite3'));repo.save(e);provider=FakeProvider([decision('wait')])
    asyncio.run(Engine(repo,provider,Rails()).handle(e.id))
    assert provider.observations[0]['previous_policy_rejection']['error']=='Wrong reference'

def test_not_now_persists_deferral_without_permission():
    e,o=ready();human(e,o,'defer');assert o.follow_up_deferred and o.follow_up_authorized=='NOT_DECIDED'
    assert next_check(e,o) is None
    human(e,o,'authorize');assert not o.follow_up_deferred

def test_audit_records_rendered_message_and_retains_llm_draft():
    from backend.agent.engine import record_audit
    e,o=ready();record_audit(e,None,e.status,'REMIND','Approved',['S6'],'remind',{'message':'Hi {{remaining}}'},{'exact_message':'Hi INR 25.00'})
    assert e.audit_events[-1].exact_message=='Hi INR 25.00' and e.audit_events[-1].tool_request['message']=='Hi {{remaining}}'

def test_global_handoff_requires_human_resume_for_each_scope():
    e,left=ready();settle(e,'C');right=e.obligations[1]
    for o in (left,right):human(e,o,'authorize',reminder_approval_mode='AUTO_WITHIN_LIMITS')
    run(e,'escalate',message='Please review this expense.')
    assert left.follow_up_authorized==right.follow_up_authorized=='PAUSED'
    human(e,left,'resume');due(e,left);send(e,left)
    assert right.follow_up_authorized=='PAUSED' and not eligibility(e,right)[0]

def test_external_clock_requires_explicit_timezone():
    from backend.wizard.events import ClockEvent
    e,o=ready()
    with pytest.raises(ValidationError):ingest(e,ClockEvent(actor='Clock',timestamp=datetime(2030,1,1)))
