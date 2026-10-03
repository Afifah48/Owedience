import csv
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import io
import json
import os
from pathlib import Path
from typing import Literal
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, model_validator
from backend.models.domain import Episode, Component, Evidence, Model, ReminderPolicy, Status
from backend.agent.engine import Engine, public_state, record_audit
from backend.agent.provider import create_provider
from backend.agent.system_prompt import RULES
from backend.connectors.base import Rails
from backend.database.repository import Repository
from backend.services.finance import PolicyError, require
from backend.wizard.events import Event, HumanMessage, AudioEvent
from backend.wizard.adapter import ingest
from backend.models.inputs import Category, SplitPreference, ItemAssignment, FileInput
from backend.services.attachments import AttachmentStore
from backend.services.demo_access import DemoAccessMiddleware
from backend.services.finance import format_minor

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
@asynccontextmanager
async def lifespan(app):
    from backend.services.monitor import monitor_loop
    task = asyncio.create_task(monitor_loop(engine)) if os.getenv('MONITOR_ENABLED','true').lower() == 'true' else None
    try: yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError): await task

app = FastAPI(title='OWEDIENCE', version='0.1.0', lifespan=lifespan)
app.add_middleware(DemoAccessMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173','http://127.0.0.1:5173','http://localhost:8000','http://127.0.0.1:8000'], allow_methods=['GET','POST','PUT'], allow_headers=['Content-Type','X-Simulation-Token'])
DATA_DIR = Path(os.getenv('DATA_DIR') or str(ROOT / 'data'))
repo = Repository(os.getenv('DATABASE_PATH') or str(DATA_DIR / 'owedience.sqlite3'))
provider = create_provider()
engine = Engine(repo,provider,Rails(os.getenv('CONNECTOR_MODE','wizard')))
attachment_store=AttachmentStore(DATA_DIR/'attachments')

@app.exception_handler(PolicyError)
async def policy_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409,content={'detail':str(exc)})


def simulation_access(x_simulation_token: str | None = Header(default=None)):
    token = os.getenv('SIMULATION_TOKEN','')
    if token and not hmac.compare_digest(x_simulation_token or '',token):
        raise HTTPException(403,'Simulation token required')


def get_episode(id):
    e = repo.get(id)
    if not e: raise HTTPException(404,'Expense not found')
    return e


def consumer_view(e):
    # Technical provenance/audits/provider details remain confined to simulation.
    state = public_state(e)
    for key in ('evidence','audit_events','processed_event_ids','connector_failures','connector_responses','unknowns','clock'):
        state.pop(key,None)
    for c in state['components']:
        for key in ('supporting_evidence','context_citations','weights','verification_status'): c.pop(key,None)
    for o in state['obligations']:
        for key in ('supporting_evidence','confirmation_evidence'): o.pop(key,None)
    return state

class CreateEpisode(Model):
    title: str = Field(min_length=1,max_length=120)
    payer: str = Field(min_length=1,max_length=80)
    participants: list[str] = Field(min_length=1,max_length=30)
    currency: Literal['INR','USD','EUR','GBP'] = 'INR'
    total: int = Field(gt=0,le=9_000_000_000_000,strict=True)
    merchant: str = Field(default='',max_length=120)
    components: list[Component] = Field(default_factory=list,max_length=100)
    initial_context: str = Field(default='',max_length=8000)
    category: Category = 'other'
    category_description: str = Field(default='',max_length=120)
    split_preference: SplitPreference = 'infer'
    user_proposed_split: dict[str,int] = Field(default_factory=dict)
    item_assignments: list[ItemAssignment] = Field(default_factory=list,max_length=100)
    extra_context: str = Field(default='',max_length=4000)
    receipt: FileInput | None = None
    voice_note: FileInput | None = None
    @model_validator(mode='after')
    def valid_people(self):
        if self.payer not in self.participants or len(set(self.participants)) != len(self.participants) or any(not p.strip() or len(p)>80 for p in self.participants):
            raise ValueError('Participants must be unique nonempty names and include the payer')
        if not set(self.user_proposed_split)<=set(self.participants) or any(type(n) is not int or n<0 or n>9_000_000_000_000 for n in self.user_proposed_split.values()):
            raise ValueError('Rough shares must reference existing participants and nonnegative minor-unit amounts')
        if len({a.item_index for a in self.item_assignments})!=len(self.item_assignments):
            raise ValueError('Only one assignment per bill item')
        for a in self.item_assignments:
            if a.item_index>=len(self.components) or len(set(a.consumers))!=len(a.consumers) or not set(a.consumers)<=set(self.participants) or not set(a.amounts)<=set(a.consumers) or not set(a.percentages)<=set(a.consumers):
                raise ValueError('Item assignments must reference existing items and participants')
            if any(type(n) is not int or n<0 or n>9_000_000_000_000 for n in a.amounts.values()):
                raise ValueError('Item amounts must use nonnegative minor units')
        return self

async def create_episode(body, origin='consumer'):
    components=[Component(description=c.description,amount=c.amount) for c in body.components] or [Component(description='Whole bill',amount=body.total)]
    e=Episode(title=body.title,payer=body.payer,participants=body.participants,currency=body.currency,total=body.total,merchant=body.merchant,
              category=body.category,category_description=body.category_description,split_preference=body.split_preference,
              user_proposed_split=body.user_proposed_split,components=components,bill_itemization_complete=bool(body.components))
    discrepancy=sum(c.amount for c in components)!=e.total
    if discrepancy:e.status=Status.INCONSISTENT
    receipt_facts={'title':e.title,'payer':e.payer,'participants':e.participants,'currency':e.currency,'total':e.total,'category':e.category,
                   'category_description':e.category_description,'components':[{'id':c.id,'description':c.description,'amount':c.amount} for c in components]}
    ev=Evidence(type='expense_created',content=json.dumps(receipt_facts,ensure_ascii=False),source=e.payer,origin=origin,payload=receipt_facts)
    e.evidence.append(ev)
    for c in e.components:c.supporting_evidence.append(ev.id)
    e.unknowns=[c.id for c in e.components]
    record_audit(e,ev,e.status,'RECEIVED','Payer supplied expense information; claims remain asserted.',['U1'],'create_episode',response={'bill_total_consistent':not discrepancy})
    def add_fact(kind,content,payload=None,status='ASSERTED'):
        fact=Evidence(type=kind,content=content,source=e.payer,origin=origin,payload=payload or {},verification_status=status)
        e.evidence.append(fact)
        record_audit(e,fact,e.status,'RECEIVED','Initial context preserved without precomputing allocations.',['U1'],'normalise_event')
        return fact
    if body.split_preference!='infer':
        content='Requested starting preference: '+body.split_preference
        if body.split_preference=='equal':content='The payer explicitly requests that every bill item be shared equally among all listed participants. This instruction must be checked against any conflicting consumption evidence.'
        add_fact('allocation_instruction',content,{'split_preference':body.split_preference})
    if body.user_proposed_split:
        proposed={p:format_minor(n) for p,n in body.user_proposed_split.items()}
        add_fact('USER_PROPOSED_SPLIT','Optional rough proposal, not verified responsibility: '+json.dumps(proposed,ensure_ascii=False),{'amounts':body.user_proposed_split,'currency':e.currency})
    for assignment in body.item_assignments:
        c=components[assignment.item_index]
        content=f'Payer reports for {c.description}: '+json.dumps({'consumers':assignment.consumers,'sharing':assignment.sharing,
                 'amounts':{p:format_minor(n) for p,n in assignment.amounts.items()},'percentages':assignment.percentages},ensure_ascii=False)
        add_fact('allocation_instruction',content,{**assignment.model_dump(),'component_id':c.id})
    for content in (body.initial_context,body.extra_context):
        if content:
            fact=ingest(e,HumanMessage(actor=e.payer,origin=origin,content=content))
            record_audit(e,fact,e.status,'RECEIVED','Human context preserved with provenance.',['U1'],'normalise_event')
    for kind,file in [('receipt',body.receipt),('audio',body.voice_note)]:
        if file is None:continue
        metadata=attachment_store.save(file)
        if kind=='receipt':
            fact=add_fact('receipt',f'Receipt attached: {metadata["filename"]}. File preserved; not parsed. Only manually supplied items are currently available for financial interpretation.',metadata,'RAW')
        else:
            fact=ingest(e,AudioEvent(actor=e.payer,origin=origin,content=f'Voice note: {metadata["filename"]}'))
            fact.payload.update(metadata)
            record_audit(e,fact,e.status,'RECEIVED','Voice note preserved; transcription requires an external rail observation.',['U1'],'normalise_event')
        fact.raw_reference=metadata['id']
        e.attachments.append({**metadata,'evidence_id':fact.id,'kind':kind})
    repo.save(e)
    # Every submission enters the existing agent, including no-text/equal/voice-only input.
    return await engine.handle(e.id)

@app.get('/health')
@app.get('/api/health')
def health(): return {'status':'ok'}

@app.get('/api/episodes')
def episodes(): return [consumer_view(e) for e in repo.all()]

@app.post('/api/episodes')
async def create(body: CreateEpisode): return consumer_view(await create_episode(body))

@app.get('/api/episodes/{id}')
def episode(id: str): return consumer_view(get_episode(id))

@app.get('/api/episodes/{id}/attachments/{attachment_id}')
def attachment(id: str, attachment_id: str):
    e=get_episode(id)
    found=next((a for a in e.attachments if a['id']==attachment_id),None)
    if not found:raise HTTPException(404,'Attachment not found')
    return FileResponse(attachment_store.path(attachment_id),media_type='application/octet-stream',filename=found['filename'],headers={'X-Content-Type-Options':'nosniff'})

@app.post('/api/episodes/{id}/messages')
async def message(id: str, body: HumanMessage):
    body.origin = 'consumer'
    get_episode(id)
    return consumer_view(await engine.handle(id,body))

@app.post('/api/episodes/{id}/audio')
async def audio(id: str, body: Event):
    require(body.type == 'audio','Audio endpoint accepts voice notes only')
    body.origin = 'consumer'
    get_episode(id)
    return consumer_view(await engine.handle(id,body))

@app.post('/api/episodes/{id}/continue')
async def continue_episode(id: str):
    get_episode(id)
    return consumer_view(await engine.handle(id))

class Preferences(Model):
    actor: str
    policy: ReminderPolicy

@app.put('/api/episodes/{id}/preferences')
async def preferences(id: str, body: Preferences):
    async with engine.locks[id]:
        e = get_episode(id)
        require(body.actor == e.payer,'Only payer may authorise reminder preferences')
        require(e.status != Status.CLOSED,'Episode closed')
        e.reminder_policy = body.policy
        ev = Evidence(type='preferences',content=body.policy.model_dump_json(),source=body.actor)
        e.evidence.append(ev)
        record_audit(e,ev,e.status,'PREFERENCE_UPDATED','Payer set communication boundaries.',['S6'],'update_preferences')
        repo.save(e)
    return consumer_view(e)

from backend.services.follow_up import FollowUpAction, human_action

@app.post('/api/episodes/{id}/obligations/{obligation_id}/follow-up')
async def follow_up(id: str, obligation_id: str, body: FollowUpAction):
    async with engine.locks[id]:
        e=get_episode(id)
        o=next((o for o in e.obligations if o.id==obligation_id),None)
        require(o is not None,'Unknown obligation')
        before=e.status
        ev=human_action(e,o,body)
        record_audit(e,ev,before,'HUMAN_'+body.action.upper(),'Creditor explicitly controlled this obligation’s follow-up.',['S6','S8'],'human_follow_up',body.model_dump(mode='json'),{'obligation_id':o.id,'debtor':o.debtor,'authorized_by':o.authorized_by,'authorization':o.follow_up_authorized,'amount_remaining':o.remaining,'policy':o.follow_up_policy.model_dump(mode='json')})
        repo.save(e)
    # Human approval supplies permission, not wording or a mandated agent action.
    if body.action=='approve': e=await engine.handle(id)
    return consumer_view(e)

@app.get('/api/simulation/config', dependencies=[Depends(simulation_access)])
def config():
    return {'provider':provider.kind,'model':provider.model,'llm_configured':bool(provider.key),
            'connector_mode':os.getenv('CONNECTOR_MODE','wizard'),'rules':RULES,'event_schema':__import__('backend.wizard.events',fromlist=['EVENT_ADAPTER']).EVENT_ADAPTER.json_schema()}

@app.get('/api/simulation/episodes/{id}', dependencies=[Depends(simulation_access)])
def sim_episode(id: str):
    e=get_episode(id)
    return {**public_state(e),'audit_events':[a.model_dump(mode='json') for a in e.audit_events]}

@app.post('/api/simulation/episodes/{id}/events', dependencies=[Depends(simulation_access)])
async def inject(id: str, body: Event):
    body.origin = 'wizard'
    get_episode(id)
    e=await engine.handle(id,body)
    return {**public_state(e),'audit_events':[a.model_dump(mode='json') for a in e.audit_events]}

@app.post('/api/simulation/seed/{name}', dependencies=[Depends(simulation_access)])
async def seed(name: Literal['restaurant','cab']):
    data = json.loads((ROOT / 'fixtures' / f'{name}.json').read_text(encoding='utf-8-sig'))
    return consumer_view(await create_episode(CreateEpisode.model_validate(data),'wizard'))

@app.get('/api/simulation/episodes/{id}/audit', dependencies=[Depends(simulation_access)])
def export(id: str, format: Literal['json','csv']='json'):
    rows = [a.model_dump(mode='json') for a in get_episode(id).audit_events]
    if format == 'json': content = json.dumps(rows,indent=2,ensure_ascii=False); mime = 'application/json'
    else:
        buf = io.StringIO()
        fields = list(rows[0]) if rows else list(__import__('backend.models.domain',fromlist=['AuditEvent']).AuditEvent.model_fields)
        writer = csv.DictWriter(buf,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            safe = {}
            for k,v in row.items():
                value = json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else str(v or '')
                # Prevent spreadsheet formula injection in exported human text.
                safe[k] = "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
            writer.writerow(safe)
        content = '\ufeff'+buf.getvalue(); mime = 'text/csv'
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="owedience-{id}.{format}"'})

DIST = ROOT / 'frontend' / 'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST / 'assets'),name='assets')
    @app.get('/{path:path}')
    def spa(path: str):
        if path.startswith('api/'): raise HTTPException(404,'API route not found')
        return FileResponse(DIST / 'index.html')
