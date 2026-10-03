"""Real-provider A–E semantic evaluation through the same POST endpoint used by the UI.
No model decisions are supplied. --persist keeps demonstration episodes in the app.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env')
os.environ['MONITOR_ENABLED']='false'
from backend import main
from backend.agent.engine import Engine
from backend.database.repository import Repository
from backend.services.finance import validate_conservation

PEOPLE=['Afifah','Riya','Arjun','Kabir']
BILL=[{'description':'Veg platter','amount':60000},{'description':'Chicken platter','amount':80000},{'description':'Dessert','amount':40000},{'description':'Drinks','amount':60000}]
BASE_CONTEXT='Riya and I shared the veg platter. Arjun and Kabir shared the chicken. Kabir ordered dessert.'
COMPLETE=BASE_CONTEXT+' Arjun and Kabir had the drinks.'

def body(label,total,context,**extra):
    return {'title':f'Live test {label}','payer':'Afifah','participants':PEOPLE,'currency':'INR','total':total,'initial_context':context,**extra}

CASES=[
 ('A',body('A · Pizza',120000,'All four of us shared the pizza equally.',category='food_dining',components=[{'description':'Pizza','amount':120000}])),
 ('B',body('B · Dinner missing drinks',240000,BASE_CONTEXT,category='food_dining',components=BILL)),
 ('C',body('C · Dinner complete',240000,COMPLETE,category='food_dining',components=BILL)),
 ('D',body('D · Rough equal proposal',240000,COMPLETE,category='food_dining',components=BILL,split_preference='known_consumption',user_proposed_split={p:60000 for p in PEOPLE})),
 ('E',body('E · Cab agreement',100000,"Riya joined halfway through the ride and we agreed she'd only pay ₹100.",category='travel_transport',components=[{'description':'Cab','amount':100000}])),
]

def assert_proposed(e):
    assert e.allocations and e.obligations,'No allocation was proposed'
    validate_conservation(e)
    assert all(o.status=='UNCONFIRMED' for o in e.obligations),'Confirmation boundary bypassed'
    assert not e.settlement_requests,'Settlement started before confirmation'

def check(label,e):
    assert not e.agent_notice,f'Agent paused: {next((a.concise_reason for a in reversed(e.audit_events) if not a.accepted),e.agent_notice)}'
    assert any(a.provider_call.get('response_id') for a in e.audit_events),'No actual provider response ID recorded'
    open_questions=[q for q in e.clarifications if q.status=='OPEN']
    shares=defaultdict(int)
    for a in e.allocations:shares[a.participant]+=a.amount
    if label=='A':
        assert_proposed(e);assert not open_questions,'Unnecessary question despite complete equal context'
        assert dict(shares)=={p:30000 for p in PEOPLE}
    elif label=='B':
        drinks=next(c for c in e.components if c.description=='Drinks')
        assert not e.allocations,'Allocated with missing drinks consumers'
        assert not drinks.consumers,'Guessed consumers were stored for the unknown drinks component'
        assert len(open_questions)==1 and open_questions[0].affected_components==[drinks.id],'Question not limited to missing drinks'
        assert all(c.verification_status=='SUPPORTED' for c in e.components if c.id!=drinks.id),'Known context lost'
    elif label in ('C','D'):
        assert_proposed(e);assert not open_questions,'Complete context still requires clarification'
        assert dict(shares)==dict(zip(PEOPLE,[30000,30000,70000,110000])),'Consumption evidence was not respected'
        if label=='D':assert e.preference_reviewed,'Rough proposal was not reviewed'
    elif label=='E':
        cab=e.components[0]
        assert not e.allocations and len(open_questions)==1,'Assumed unspecified remainder sharing'
        assert cab.fixed_amounts.get('Riya')==10000,'Fixed ₹100 agreement was not preserved'
    return dict(shares)

def main_cli():
    if not main.provider.key:
        print('NOT RUN: configure LLM_API_KEY in the local .env.');return 2
    reports=[];failures=0
    cases=[(label,{**data,'title':'Shared dinner','initial_context':('Riya and I had veg. Arjun and Kabir had chicken. Kabir had dessert.'+(' Arjun and Kabir shared the drinks.' if label=='C' else ''))}) for label,data in CASES if label in ('B','C')] if '--bc' in sys.argv else CASES
    report_path=ROOT/'docs'/('gemini-reasoning.json' if '--bc' in sys.argv else 'live-evaluation.json')
    previous=json.loads(report_path.read_text(encoding='utf-8')) if '--resume' in sys.argv and report_path.exists() else {}
    with tempfile.TemporaryDirectory(prefix='owedience-live-') as temp:
        if '--persist' not in sys.argv and '--resume' not in sys.argv:
            main.repo=Repository(str(Path(temp)/'eval.sqlite3'))
            main.engine=Engine(main.repo,main.provider,main.engine.rails)
        with TestClient(main.app) as client:
            for label,input_data in cases:
                prior=next((r for r in previous.get('cases',[]) if r['case']==label and r['input']==input_data),None)
                result=(client.get(f'/api/episodes/{prior["episode_id"]}') if prior.get('passed') else client.post(f'/api/episodes/{prior["episode_id"]}/continue')) if prior else client.post('/api/episodes',json=input_data)
                result.raise_for_status();e=main.repo.get(result.json()['id'])
                errors=[];shares={}
                try:shares=check(label,e)
                except (AssertionError,ValueError) as error:errors.append(str(error));failures+=1
                trace=[a.model_dump(mode='json') for a in e.audit_events if a.provider_call or a.action in ('provider','loop_guard')]
                report={'case':label,'episode_id':e.id,'input':input_data,'passed':not errors,'errors':errors,'status':str(e.status),'questions':[q.message for q in e.clarifications if q.status=='OPEN'],'shares_minor':shares,'decisions':trace}
                reports.append(report)
                print(f'{"PASS" if not errors else "FAIL"} {label}: {e.id}',flush=True)
                for error in errors:print('  '+error,flush=True)
                for a in trace:print(f'  {a["action"]} [{"accepted" if a["accepted"] else "rejected"}]: {a["concise_reason"]}',flush=True)
                for q in report['questions']:print('  Question: '+q,flush=True)
                # Also prove a natural answer re-enters the loop and preserves known facts.
                if label=='B' and not errors and '--bc' not in sys.argv:
                    response=client.post(f'/api/episodes/{e.id}/messages',json={'actor':'Afifah','content':'Arjun and Kabir had the drinks.'})
                    response.raise_for_status();answered=main.repo.get(e.id)
                    try:check('C',answered)
                    except (AssertionError,ValueError) as error:failures+=1;report['follow_up_error']=str(error)
                    report['follow_up']={'status':str(answered.status),'question_answered':all(q.status=='ANSWERED' for q in answered.clarifications),'decisions':[a.model_dump(mode='json') for a in answered.audit_events[len(e.audit_events):]]}
                    print('  Follow-up: '+report['follow_up']['status'],flush=True)
    path=ROOT/'docs'/('gemini-reasoning.json' if '--bc' in sys.argv else 'live-evaluation.json');path.write_text(json.dumps({'provider':main.provider.kind,'model':main.provider.model,'passed':len(cases)-failures,'total':len(cases),'cases':reports},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{len(cases)-failures}/{len(cases)} live cases passed. Report: {path}',flush=True)
    return 1 if failures else 0

if __name__=='__main__':sys.exit(main_cli())
