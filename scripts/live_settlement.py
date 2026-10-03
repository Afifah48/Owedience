"""Live Gemini settlement demonstration through the same APIs as the UI. No supplied decisions."""
import json,os,sys,tempfile
from pathlib import Path
from dotenv import load_dotenv
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env');os.environ['MONITOR_ENABLED']='false'
from backend import main
from backend.agent.engine import Engine
from backend.database.repository import Repository
from backend.services.follow_up import next_check

def main_cli():
    report={'provider':main.provider.kind,'model':main.provider.model,'passed':False,'steps':[]}
    if '--finish' in sys.argv:
        path=ROOT/'docs'/'gemini-settlement.json';report=json.loads(path.read_text(encoding='utf-8'))
        eid=report['episode_id'];e=main.repo.get(eid)
        assert e and e.payments[-1].amount==30000 and e.obligations[0].reminder_count==2,'Only resume the recorded final payment, never recreate earlier calls'
        with TestClient(main.app) as client:
            result=client.post(f'/api/episodes/{eid}/continue');result.raise_for_status();e=main.repo.get(eid)
            report['steps'].append({'step':'Resume final verified payment after provider quota limit','state':e.model_dump(mode='json')})
            report['passed']=not e.agent_notice and e.obligations[0].status=='PAID' and next_check(e,e.obligations[0]) is None
            report['final_state']=e.model_dump(mode='json')
            if report['passed']: report.pop('error',None)
        path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(('PASS' if report['passed'] else 'FAILED')+': final verified payment; '+str(e.status),flush=True)
        return 0 if report['passed'] else 1
    with tempfile.TemporaryDirectory(prefix='owedience-settlement-') as temp:
        if '--persist' not in sys.argv:
            main.repo=Repository(str(Path(temp)/'live.sqlite3'));main.engine=Engine(main.repo,main.provider,main.engine.rails)
        with TestClient(main.app) as client:
            def post(path,body):
                result=client.post('/api'+path,json=body);result.raise_for_status();return result.json()
            try:
                data=post('/episodes',{'title':'Dinner follow-up demonstration','payer':'Afifah','participants':['Afifah','Arjun'],'total':140000,'category':'food_dining','initial_context':'The dinner bill is 1400. I have not provided who consumed it or how it was shared.'})
                eid=data['id'];report['episode_id']=eid
                def snapshot(label):
                    e=main.repo.get(eid)
                    report['steps'].append({'step':label,'state':e.model_dump(mode='json')});print(label+': '+str(e.status),flush=True)
                    assert not e.agent_notice, 'Gemini unavailable or policy loop paused; see audit'
                    return e
                e=snapshot('Missing context');assert any(q.status=='OPEN' for q in e.clarifications) and not e.allocations
                post(f'/episodes/{eid}/messages',{'actor':'Afifah','content':'Arjun and I shared the entire dinner equally.'})
                e=snapshot('Supported shares');o=next(x for x in e.obligations if x.debtor=='Arjun');assert o.amount==70000 and o.status=='UNCONFIRMED'
                post(f'/episodes/{eid}/messages',{'actor':'Arjun','content':'My share looks right.','intent':'CONFIRM','obligation_ids':[o.id]})
                e=snapshot('Confirmed, no pursuit');assert e.obligations[0].follow_up_authorized=='NOT_DECIDED' and not e.obligations[0].reminder_history
                path=f'/episodes/{eid}/obligations/{o.id}/follow-up'
                policy={'minimum_interval_hours':72,'maximum':3,'reminder_approval_mode':'ASK_EACH_TIME','quiet_start':'22:00','quiet_end':'08:00'}
                post(path,{'actor':'Afifah','action':'authorize','policy':policy});e=snapshot('Human authorized only this obligation')
                token=os.getenv('SIMULATION_TOKEN','')
                def event(body):
                    result=client.post(f'/api/simulation/episodes/{eid}/events',json=body,headers={'X-Simulation-Token':token});result.raise_for_status()
                event({'type':'clock','actor':'World clock','timestamp':next_check(e,e.obligations[0]).isoformat()})
                e=snapshot('Agent requested first approval');assert e.obligations[0].reminder_approval and e.obligations[0].reminder_count==0
                post(path,{'actor':'Afifah','action':'approve'});e=snapshot('Gemini wrote first reminder');o=e.obligations[0]
                assert o.reminder_count==1 and '700.00' in o.reminder_history[-1]['message'];assert o.reminder_history[-1]['connector']['status']=='SIMULATED_DELIVERY'
                post(path,{'actor':'Afifah','action':'edit','policy':{**policy,'reminder_approval_mode':'ASK_FIRST_THEN_AUTO'}})
                reference=e.settlement_requests[0].reference
                event({'type':'payment','actor':'Payment rail','payment_id':eid+'-partial','amount':40000,'debtor':'Arjun','creditor':'Afifah','currency':'INR','reference':reference,'verified':True})
                e=snapshot('Verified partial payment');assert e.obligations[0].remaining==30000 and e.obligations[0].reminder_count==1
                event({'type':'clock','actor':'World clock','timestamp':next_check(e,e.obligations[0]).isoformat()})
                e=snapshot('Gemini wrote remaining-balance reminder');o=e.obligations[0]
                assert o.reminder_count==2 and '300.00' in o.reminder_history[-1]['message'] and '700.00' not in o.reminder_history[-1]['message']
                event({'type':'payment','actor':'Payment rail','payment_id':eid+'-final','amount':30000,'debtor':'Arjun','creditor':'Afifah','currency':'INR','reference':reference,'verified':True})
                e=snapshot('Verified full payment stopped follow-up');assert e.obligations[0].status=='PAID' and next_check(e,e.obligations[0]) is None
                assert all(a.provider_call.get('provider')=='gemini' and a.provider_call.get('response_id') for a in e.audit_events if a.action in ('request_reminder_approval','remind') and a.accepted)
                report['passed']=True
            except Exception as error:
                report['error']=str(error)
                if report.get('episode_id'): report['final_state']=main.repo.get(report['episode_id']).model_dump(mode='json')
                print('FAILED: '+str(error),flush=True)
    path=ROOT/'docs'/'gemini-settlement.json';path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print('Report: '+str(path),flush=True)
    return 0 if report['passed'] else 1
if __name__=='__main__':sys.exit(main_cli())
