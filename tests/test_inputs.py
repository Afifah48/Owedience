import asyncio
import json
import base64
import pytest
from fastapi.testclient import TestClient
from backend import main
from backend.agent.engine import Engine, public_state
from backend.agent.provider import HTTPProvider
from backend.connectors.base import Rails
from backend.database.repository import Repository
from backend.models.domain import Episode, Component, Evidence
from backend.services.attachments import AttachmentStore
from backend.services.finance import component_shares, percentage_weights, validate_conservation
from backend.wizard.adapter import ingest
from backend.wizard.events import HumanMessage
from tests.test_invariants import run

@pytest.fixture
def client(tmp_path,monkeypatch):
    repo=Repository(str(tmp_path/'inputs.sqlite3'));p=HTTPProvider();p.key=''
    monkeypatch.setattr(main,'repo',repo);monkeypatch.setattr(main,'engine',Engine(repo,p,Rails()))
    monkeypatch.setattr(main,'attachment_store',AttachmentStore(tmp_path/'files'))
    monkeypatch.setenv('MONITOR_ENABLED','false');monkeypatch.setenv('SIMULATION_TOKEN','')
    with TestClient(main.app) as c:yield c

def payload(**changes):
    return {'title':'Shared cost','payer':'A','participants':['A','B'],'total':10000,**changes}

def test_no_text_and_no_items_still_enters_provider(client):
    result=client.post('/api/episodes',json=payload()).json();e=main.repo.get(result['id'])
    assert e.audit_events[-1].action=='provider' and e.agent_notice
    assert e.components[0].description=='Whole bill' and not e.bill_itemization_complete
    assert not e.allocations and not e.components[0].consumers

def test_inputs_remain_evidence_not_ledger(client):
    result=client.post('/api/episodes',json=payload(category='utilities',split_preference='custom',user_proposed_split={'B':4000},components=[{'description':'Internet','amount':10000}],item_assignments=[{'item_index':0,'consumers':['A','B'],'sharing':'percentages','percentages':{'A':'60','B':'40'}}],extra_context='I paid the bill.')).json()
    e=main.repo.get(result['id']);assert e.category=='utilities' and not e.allocations and not e.components[0].consumers
    rough=next(ev for ev in e.evidence if ev.type=='USER_PROPOSED_SPLIT')
    assert rough.payload['amounts']=={'B':4000} and not e.preference_reviewed
    assignment=next(ev for ev in e.evidence if 'Internet' in ev.content and ev.type=='allocation_instruction')
    assert assignment.payload['percentages']=={'A':'60','B':'40'}

def test_bill_mismatch_reaches_agent_and_requires_explicit_correction(client):
    result=client.post('/api/episodes',json=payload(components=[{'description':'Tickets','amount':9000}])).json()
    e=main.repo.get(result['id']);assert e.audit_events[-1].action=='provider' and not e.allocations
    run(e,'request_clarification',topic='bill',recipient='A',component_ids=[e.components[0].id],message='Is the total 100 or 90?')
    ev=ingest(e,HumanMessage(actor='A',content='The correct total is 90.'))
    with pytest.raises(ValueError):run(e,'correct_bill_amounts',total='95',evidence_id=ev.id,quote=ev.content)
    run(e,'correct_bill_amounts',total='90',evidence_id=ev.id,quote=ev.content)
    assert e.total==9000 and e.clarifications[0].status=='ANSWERED'

def test_uploads_preserved_and_blobs_omitted(client):
    raw=b'local test evidence';encoded=base64.b64encode(raw).decode()
    result=client.post('/api/episodes',json=payload(receipt={'filename':'receipt.pdf','mime_type':'application/pdf','data_base64':encoded},voice_note={'filename':'voice.webm','mime_type':'audio/webm','data_base64':encoded})).json()
    e=main.repo.get(result['id']);assert len(e.attachments)==2 and not e.allocations
    assert encoded not in json.dumps(public_state(e))
    assert any(ev.type=='audio' and ev.verification_status=='RAW' for ev in e.evidence)
    assert any(ev.type=='receipt' and 'not parsed' in ev.content for ev in e.evidence)
    file=e.attachments[0];response=client.get(f'/api/episodes/{e.id}/attachments/{file["id"]}')
    assert response.content==raw and 'attachment' in response.headers['content-disposition']
    assert client.get(f'/api/episodes/{e.id}/attachments/file_missing').status_code==404

def test_invalid_file_cannot_be_saved(client):
    response=client.post('/api/episodes',json=payload(receipt={'filename':'file','mime_type':'x','data_base64':'not base64'}))
    assert response.status_code==409 and not main.repo.all()

def cab():
    e=Episode(title='Cab',payer='A',participants=['A','B','C','Riya'],total=100000,components=[Component(description='Cab',amount=100000)])
    ev=Evidence(type='human_message',content='Riya pays 100; A, B and C share the rest equally.',source='A');e.evidence.append(ev)
    return e,ev

def test_fixed_share_is_exact_and_remainder_computed_by_code():
    e,ev=cab();c=e.components[0]
    run(e,'reconstruct_context',items=[{'component_id':c.id,'consumers':e.participants,'fixed_amounts':{'Riya':'100'},'weights':{'A':1,'B':1,'C':1},'explanation':'Riya’s agreed amount; others share the remainder.','citations':[{'evidence_id':ev.id,'quote':ev.content}]}])
    run(e,'propose_allocation');validate_conservation(e)
    assert {a.participant:a.amount for a in e.allocations}=={'Riya':10000,'A':30000,'B':30000,'C':30000}

def test_partial_fixed_fact_preserved_until_remainder_known():
    e,ev=cab();c=e.components[0]
    run(e,'reconstruct_context',items=[{'component_id':c.id,'consumers':['Riya'],'fixed_amounts':{'Riya':'100'},'complete':False,'citations':[{'evidence_id':ev.id,'quote':'Riya pays 100'}]}])
    assert c.fixed_amounts=={'Riya':10000} and c.verification_status=='PARTIAL'
    with pytest.raises(ValueError):run(e,'propose_allocation')
    run(e,'request_clarification',recipient='A',component_ids=[c.id],message='How should the remaining cost be shared?')
    run(e,'reconstruct_context',items=[{'component_id':c.id,'consumers':e.participants,'fixed_amounts':{'Riya':'100'},'weights':{'A':1,'B':1,'C':1},'citations':[{'evidence_id':ev.id,'quote':ev.content}]}])
    assert c.fixed_amounts['Riya']==10000 and e.clarifications[0].status=='ANSWERED'

def test_percentages_conserve_rounding_without_float():
    weights=percentage_weights({'A':'33.33','B':'66.67'});assert weights=={'A':3333,'B':6667}
    c=Component(description='Odd amount',amount=10001,consumers=['A','B'],weights=weights)
    assert sum(component_shares(c).values())==10001
    with pytest.raises(ValueError):percentage_weights({'A':'30','B':'60'})

def test_rough_split_cannot_prove_context_and_review_never_changes_money():
    e,ev=cab();c=e.components[0];e.user_proposed_split={'Riya':25000}
    rough=Evidence(type='USER_PROPOSED_SPLIT',content='Riya should pay 250',source='A');e.evidence.append(rough)
    with pytest.raises(ValueError):run(e,'reconstruct_context',items=[{'component_id':c.id,'consumers':['Riya'],'fixed_amounts':{'Riya':'250'},'citations':[{'evidence_id':rough.id,'quote':rough.content}]}])
    run(e,'reconstruct_context',items=[{'component_id':c.id,'consumers':e.participants,'fixed_amounts':{'Riya':'100'},'weights':{'A':1,'B':1,'C':1},'citations':[{'evidence_id':ev.id,'quote':ev.content}]}])
    with pytest.raises(ValueError):run(e,'propose_allocation')
    result=run(e,'review_split_preference',evidence_id=rough.id,message='Riya’s agreed share differs from the rough proposal.')
    assert result['discrepancies']=={'Riya':{'proposed':25000,'supported':10000}} and not e.allocations
    run(e,'propose_allocation');assert next(a.amount for a in e.allocations if a.participant=='Riya')==10000

def test_later_itemization_requires_payer_evidence_and_conservation():
    e,ev=cab();e.bill_itemization_complete=False
    ev=ingest(e,HumanMessage(actor='A',content='Fare 900 and toll 100.'))
    run(e,'describe_bill_items',items=[{'description':'Fare','amount':'900'},{'description':'toll','amount':'100'}],evidence_id=ev.id,quote=ev.content)
    assert e.bill_itemization_complete and sum(c.amount for c in e.components)==100000
