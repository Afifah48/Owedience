"""Verify a running competition service with real Gemini and fictional evidence.

Credentials come from local environment or private prompts, never reports.
Run --recheck with the same report after an operator-controlled service restart.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def main_cli():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--report', default='.repro/deployment-verification.json')
    parser.add_argument('--recheck', action='store_true')
    parser.add_argument('--pace-seconds', type=float, default=8, help='Pause between evidence submissions to limit free-tier bursts.')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    code=os.getenv('DEMO_ACCESS_CODE') or getpass.getpass('Demo access code (private): ')
    token=os.getenv('SIMULATION_TOKEN') or getpass.getpass('Simulation token (private): ')
    report_path=Path(args.report)
    report=json.loads(report_path.read_text(encoding='utf-8')) if args.recheck else {
        'url':args.url.rstrip('/'), 'timestamp':datetime.now(timezone.utc).isoformat(),
        'fixture':'Fictional Afifah/Arjun dinner: INR 1400, equal shares; payments INR 400 and INR 300',
        'passed':False, 'checks':[],
    }
    def check(label, condition):
        report['checks'].append({'check':label, 'passed':bool(condition)})
        assert condition, label
        print('PASS: '+label, flush=True)
    headers={'X-Simulation-Token':token}
    try:
        with httpx.Client(base_url=args.url.rstrip('/'), timeout=600, follow_redirects=False) as client:
            health=client.get('/health')
            check('Public health without credentials', health.status_code==200 and health.json()=={'status':'ok'})
            check('Access gate loads in a new session', 'Private competition prototype' in client.get('/').text)
            check('Direct expense API blocked before access', client.get('/api/episodes').status_code==401)
            login=client.post('/demo/access',data={'access_code':code})
            check('Correct code creates a browser-session cookie', login.status_code==303 and 'HttpOnly' in login.headers.get('set-cookie',''))
            check('Home serves production React', 'id="root"' in client.get('/').text)
            check('Simulation requires separate token', client.get('/api/simulation/config').status_code==403)
            config=client.get('/api/simulation/config',headers=headers);config.raise_for_status()
            check('Server-side Gemini and Wizard configured', config.json()['provider']=='gemini' and config.json()['model']=='gemini-3.5-flash-lite' and config.json()['llm_configured'] and config.json()['connector_mode']=='wizard')
            for route in ('/expenses','/activity','/simulation'):
                check('Production route '+route, 'id="root"' in client.get(route).text)
            def get_state():
                r=client.get('/api/simulation/episodes/'+report['episode_id'],headers=headers);r.raise_for_status();return r.json()
            if args.recheck:
                state=get_state()
                check('Episode survives service restart', state['id']==report['episode_id'])
                check('Authorization/balance/reminder/audit survive restart', state==report['final_state'])
                r=client.get(f"/api/episodes/{state['id']}/attachments/{state['attachments'][0]['id']}")
                check('Uploaded attachment survives service restart', r.status_code==200 and hashlib.sha256(r.content).hexdigest()==state['attachments'][0]['sha256'])
                report['persistence_passed']=True
            else:
                def post(path, body, simulation=False):
                    time.sleep(max(0, args.pace_seconds))
                    r=client.post('/api'+path,json=body,headers=headers if simulation else None)
                    r.raise_for_status();return r.json()
                created=post('/episodes',{'title':'Competition deployment verification','payer':'Afifah','participants':['Afifah','Arjun'],'currency':'INR','total':140000,'category':'food_dining',
                    'initial_context':'The dinner bill is 1400. I have not provided who consumed it or how it was shared.',
                    'receipt':{'filename':'fictional-receipt.txt','mime_type':'text/plain','data_base64':base64.b64encode(b'Fictional deployment receipt: INR 1400').decode()}})
                report['episode_id']=created['id'];eid=created['id']
                def state(label):
                    e=get_state()
                    report['last_state']=e
                    check(label+' — genuine agent available', not e['agent_notice'])
                    return e
                e=state('New Expense persisted')
                check('Gemini asks for missing context', any(q['status']=='OPEN' for q in e['clarifications']) and not e['allocations'])
                post(f'/episodes/{eid}/messages',{'actor':'Afifah','content':'Arjun and I shared the entire dinner equally.'})
                e=state('Clarification answered')
                o=next(o for o in e['obligations'] if o['debtor']=='Arjun');oid=o['id']
                check('Ledger respects equal sharing and confirmation boundary', o['amount']==70000 and o['status']=='UNCONFIRMED')
                post(f'/episodes/{eid}/messages',{'actor':'Arjun','content':'My share looks right.','intent':'CONFIRM','obligation_ids':[oid]})
                e=state('Confirmation');o=e['obligations'][0]
                check('Confirmation does not authorize reminders', o['status'] in ('CONFIRMED','ACTIVE') and o['follow_up_authorized']=='NOT_DECIDED' and o['reminder_count']==0)
                follow=f'/episodes/{eid}/obligations/{oid}/follow-up'
                policy={'minimum_interval_hours':72,'maximum':3,'reminder_approval_mode':'ASK_EACH_TIME','quiet_start':'22:00','quiet_end':'08:00'}
                post(follow,{'actor':'Afifah','action':'authorize','policy':policy})
                e=state('Scoped human follow-up authorization')
                check('Reminder policy saved', e['obligations'][0]['follow_up_policy']['minimum_interval_hours']==72 and e['obligations'][0]['follow_up_authorized']=='AUTHORIZED')
                def event(body):return post(f'/simulation/episodes/{eid}/events',body,True)
                event({'type':'clock','actor':'World clock','timestamp':e['obligations'][0]['next_check_at']})
                e=state('Manual clock injection')
                check('Ask-first boundary holds', e['obligations'][0]['reminder_approval'] is not None and e['obligations'][0]['reminder_count']==0)
                post(follow,{'actor':'Afifah','action':'approve'})
                e=state('Reminder approval');o=e['obligations'][0]
                check('Gemini wrote reminder; Wizard simulated delivery', o['reminder_count']==1 and re.search(r'(?<![\d.])700(?:\.00)?(?!\d|\.\d)', o['reminder_history'][-1]['message']) and o['reminder_history'][-1]['connector']['status']=='SIMULATED_DELIVERY')
                post(follow,{'actor':'Afifah','action':'edit','policy':{**policy,'reminder_approval_mode':'ASK_FIRST_THEN_AUTO'}})
                reference=e['settlement_requests'][0]['reference']
                def pay(suffix, amount):return event({'type':'payment','actor':'Payment rail','payment_id':eid+suffix,'amount':amount,'debtor':'Arjun','creditor':'Afifah','currency':'INR','reference':reference,'verified':True})
                pay('-partial',40000);e=state('Verified partial payment')
                check('Partial payment updates remaining balance', e['obligations'][0]['amount_remaining']==30000)
                event({'type':'clock','actor':'World clock','timestamp':e['obligations'][0]['next_check_at']})
                e=state('Remaining-balance reminder');o=e['obligations'][0]
                check('Next reminder uses INR 300 remaining', o['reminder_count']==2 and re.search(r'(?<![\d.])300(?:\.00)?(?!\d|\.\d)', o['reminder_history'][-1]['message']) and not re.search(r'(?<![\d.])700(?:\.00)?(?!\d|\.\d)', o['reminder_history'][-1]['message']))
                pay('-final',30000);e=state('Verified final payment')
                check('Final settlement closes episode and stops follow-up', e['status']=='CLOSED' and e['obligations'][0]['status']=='PAID' and e['obligations'][0]['amount_remaining']==0 and e['obligations'][0]['next_check_at'] is None)
                future=datetime.fromisoformat(e['clock'])+timedelta(days=7)
                closed_event=client.post(f'/api/simulation/episodes/{eid}/events', json={'type':'clock','actor':'World clock','timestamp':future.isoformat()}, headers=headers)
                check('Closed episode rejects new external events', closed_event.status_code==409 and closed_event.json().get('detail')=='Closed episodes no longer accept financial events')
                e=get_state()
                check('Future clock cannot restart closed reminders', e['status']=='CLOSED' and e['obligations'][0]['reminder_count']==2)
                for fmt in ('json','csv'):
                    r=client.get(f'/api/simulation/episodes/{eid}/audit',params={'format':fmt},headers=headers)
                    check('Audit '+fmt+' export', r.status_code==200 and bool(r.content))
                calls=[a['provider_call'] for a in e['audit_events'] if a['provider_call']]
                check('Real Gemini response metadata recorded', calls and any(c.get('response_id') for c in calls) and all(c.get('provider')=='gemini' for c in calls))
                report['provider_calls']=calls;report['final_state']=e
                report.pop('last_state',None)
                # Client headers, cookies, configured credentials and private inputs are excluded.
                raw=json.dumps(report,ensure_ascii=False)
                check('Credentials excluded from report', all(secret not in raw for secret in (code,token,os.getenv('GEMINI_API_KEY','')) if secret))
            report['passed']=True
    except Exception as error:
        # Do not emit exception payloads: HTTP errors can contain sensitive URLs.
        report['passed']=False
        report['error_type']=type(error).__name__
        print('FAIL: verification stopped; inspect the last check and protected agent audit.',flush=True)
    report_path.parent.mkdir(parents=True,exist_ok=True)
    # Also redact failed runs before writing: a failed exclusion assertion must
    # never turn a configured credential into a diagnostic artifact.
    private_values=[value for value in (code,token,os.getenv('GEMINI_API_KEY','')) if value]
    def redact(value):
        if isinstance(value,str):
            for private in private_values: value=value.replace(private,'[REDACTED]')
        elif isinstance(value,list): value=[redact(item) for item in value]
        elif isinstance(value,dict): value={redact(key):redact(item) for key,item in value.items()}
        return value
    report_path.write_text(json.dumps(redact(report),indent=2,ensure_ascii=False),encoding='utf-8')
    print('Report: '+str(report_path),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main_cli())
