import asyncio
import json
import csv
import io
import pytest
import httpx
from fastapi.testclient import TestClient
from backend import main
from backend.agent.provider import HTTPProvider
from backend.agent.decision_schema import ADAPTER
from backend.agent.engine import Engine
from backend.connectors.base import Rails
from backend.database.repository import Repository
from backend.wizard.events import PaymentReference
from tests.test_invariants import ledger, settle, payment, response, run, eligible_clock, expense, reconstruct, decision, FakeProvider

@pytest.fixture
def client(tmp_path,monkeypatch):
    repository=Repository(str(tmp_path/'api.sqlite3'))
    provider=HTTPProvider();provider.key=''
    monkeypatch.setattr(main,'repo',repository)
    monkeypatch.setattr(main,'provider',provider)
    monkeypatch.setattr(main,'engine',Engine(repository,provider,Rails()))
    monkeypatch.setenv('MONITOR_ENABLED','false')
    monkeypatch.setenv('SIMULATION_TOKEN','')
    with TestClient(main.app) as c: yield c


def test_api_create_save_provenance_and_hidden_internals(client):
    data={'title':'Shared movie','payer':'N','participants':['N','P'],'total':95000,'components':[{'description':'Tickets','amount':95000}],'initial_context':'We shared two tickets equally.'}
    r=client.post('/api/episodes',json=data);assert r.status_code==200
    body=r.json();assert body['agent_notice'] and 'evidence' not in body and 'audit_events' not in body
    sim=client.get('/api/simulation/episodes/'+body['id']).json()
    assert len(sim['evidence'])==2 and sim['evidence'][0]['verification_status']=='ASSERTED'
    assert sim['audit_events'][-1]['decision']=='WAIT'
    assert client.get('/api/episodes/'+body['id']).json()['id']==body['id']


def test_api_rejects_invalid_participants(client):
    r=client.post('/api/episodes',json={'title':'x','payer':'A','participants':['B'],'total':100,'components':[{'description':'x','amount':100}]});assert r.status_code==422


def test_simulation_token_and_consumer_event_boundary(client,monkeypatch):
    e=ledger();main.repo.save(e)
    monkeypatch.setenv('SIMULATION_TOKEN','private-token')
    assert client.get('/api/simulation/config').status_code==403
    assert client.get('/api/simulation/config',headers={'X-Simulation-Token':'private-token'}).status_code==200
    r=client.post(f'/api/episodes/{e.id}/messages',json={'type':'payment','actor':'B','payment_id':'fake','amount':100,'debtor':'B','creditor':'A','verified':True});assert r.status_code==422


def test_audit_exports_json_csv_and_formula_protection(client):
    e=expense();ev=e.evidence[0];ev.content='=HYPERLINK("evil")';main.repo.save(e)
    from backend.agent.engine import record_audit
    record_audit(e,ev,e.status,'RECEIVED','=Formula', ['U1'],'normalise_event');main.repo.save(e)
    json_export=client.get(f'/api/simulation/episodes/{e.id}/audit?format=json');assert json_export.status_code==200
    rows=client.get(f'/api/simulation/episodes/{e.id}/audit?format=csv').text
    assert "'=Formula" in rows and list(csv.DictReader(io.StringIO(rows.lstrip('\ufeff'))))


def test_payment_reference_resolves_ambiguity_with_new_verified_evidence():
    e=ledger();r=settle(e,'B');payment(e,'B',1000,None)
    from backend.wizard.adapter import ingest
    ev=ingest(e,PaymentReference(actor='Payment rail',payment_id='receipt-1',reference=r['reference'],debtor='B',creditor='A',verified=True))
    run(e,'match_payment',payment_id='receipt-1',resolution_evidence_id=ev.id)
    assert e.payments[0].status=='MATCHED' and e.obligations[0].amount_paid==1000
    with pytest.raises(ValueError):run(e,'match_payment',payment_id='receipt-1',resolution_evidence_id=ev.id)


def test_conflict_resolution_requires_every_affected_person():
    e=expense();reconstruct(e)
    from backend.wizard.adapter import ingest
    from backend.wizard.events import HumanMessage
    ev=ingest(e,HumanMessage(actor='B',content='I disagree with the first part.'))
    run(e,'mark_context_conflict',component_ids=[e.components[0].id],evidence_id=ev.id)
    a=ingest(e,HumanMessage(actor='A',content='A and B agree to share the first part equally.'))
    item={'component_id':e.components[0].id,'consumers':['A','B'],'weights':{'A':1,'B':1},'citations':[{'evidence_id':a.id,'quote':a.content}]}
    with pytest.raises(ValueError):run(e,'resolve_context_conflict',items=[item])
    b=ingest(e,HumanMessage(actor='B',content='A and B agree to share the first part equally.'))
    item['citations'].append({'evidence_id':b.id,'quote':b.content});run(e,'resolve_context_conflict',items=[item]);run(e,'propose_allocation');assert e.obligations


def test_reminder_limit_cannot_be_reset_with_second_request():
    from backend.models.domain import SettlementRequest
    from backend.services.finance import reminder_eligibility
    from tests.test_invariants import authorize
    e=ledger();settle(e,'B');authorize(e);r=e.settlement_requests[0];e.obligations[0].reminder_count=2
    new=SettlementRequest(debtor='B',obligation_ids=r.obligation_ids,amount=r.amount,reference='new-reference',created_at=r.created_at)
    e.settlement_requests.append(new);eligible_clock(e,new)
    assert reminder_eligibility(e,new)[1]=='Reminder limit exhausted'

@pytest.mark.parametrize('kind',['openai','compatible'])
def test_provider_function_call_contract_without_live_network(monkeypatch,kind):
    observed={}
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers,json):
            observed.update({'url':url,'body':json})
            args='{"reason":"Waiting for evidence.","rule_ids":["E3"]}'
            payload={'output':[{'type':'function_call','name':'wait','arguments':args}]} if kind=='openai' else {'choices':[{'message':{'tool_calls':[{'function':{'name':'wait','arguments':args}}]}}]}
            return httpx.Response(200,json=payload)
    monkeypatch.setattr('backend.agent.provider.httpx.AsyncClient',Client)
    p=HTTPProvider();p.key='test-only';p.kind=kind
    d=asyncio.run(p.decide({'title':'Independent example'},{'status':'NEW'}));assert d.tool=='wait'
    assert observed['body']['tool_choice']=='required'
    assert 'test-only' not in json.dumps(observed['body'])
    if kind=='openai':assert observed['url'].endswith('/responses') and observed['body']['store'] is False


def test_end_to_end_agent_loop_builds_ledger_from_provider_actions(tmp_path):
    e=expense();ev=e.evidence[0];repo=Repository(str(tmp_path/'loop.sqlite3'));repo.save(e)
    items=[{'component_id':c.id,'consumers':people,'weights':{p:1 for p in people},'citations':[{'evidence_id':ev.id,'quote':quote}]} for c,people,quote in [(e.components[0],['A','B'],'A and B shared the first part equally.'),(e.components[1],['C'],'C alone had the second part.')]]
    provider=FakeProvider([decision('reconstruct_context',items=items),decision('propose_allocation'),decision('wait')])
    result=asyncio.run(Engine(repo,provider,Rails()).handle(e.id));assert result.obligations and len(result.audit_events)==3
    assert provider.observations[1]['status']=='CONTEXT_UPDATED' and provider.observations[2]['status']=='ALLOCATION_PROPOSED'


def test_monitor_wakes_due_episode_with_external_clock_not_a_decision(tmp_path):
    from backend.services.monitor import monitor_once
    from datetime import timedelta
    from tests.test_invariants import authorize
    e=ledger();settle(e,'B');authorize(e);eligible_clock(e,e.settlement_requests[0]);due=e.clock
    repo=Repository(str(tmp_path/'monitor.sqlite3'));repo.save(e)
    p=FakeProvider([decision('wait')]);p.key='test-only'
    engine=Engine(repo,p,Rails());asyncio.run(monitor_once(engine,due))
    saved=repo.get(e.id);assert saved.evidence[-1].type=='clock' and saved.evidence[-1].origin=='connector'
    assert saved.audit_events[-1].decision=='WAIT' and saved.settlement_requests[0].reminders==0


def test_malformed_connector_response_rolls_back_and_is_audited(tmp_path):
    from backend.wizard.events import AudioEvent
    from backend.wizard.adapter import ingest
    e=expense();audio=ingest(e,AudioEvent(actor='A',content='Actual voice reference'))
    repo=Repository(str(tmp_path/'bad-response.sqlite3'));repo.save(e)
    class BadRails:
        async def call(self,*args):return {'transcript':'some words','human_verified':True,'confidence':'invalid'}
    p=FakeProvider([decision('rail_tool',connector='gnani',operation='transcribe_audio',evidence_id=audio.id),decision('wait')])
    saved=asyncio.run(Engine(repo,p,BadRails()).handle(e.id));assert not any(ev.type=='voice_transcript' for ev in saved.evidence)
    assert not saved.audit_events[0].accepted and 'last verified state preserved' in saved.audit_events[0].concise_reason
