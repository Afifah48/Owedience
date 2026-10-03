"""One minimal genuine Gemini call; no prescribed semantic decision."""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from backend.agent.provider import create_provider, ProviderError
from backend.agent.engine import public_state
from backend.models.domain import Episode, Component, Evidence
ROOT=Path(__file__).resolve().parent.parent

async def main():
    load_dotenv(ROOT/'.env')
    provider=create_provider()
    report={'timestamp':datetime.now(timezone.utc).isoformat(),'provider':provider.kind,'requested_model':provider.model,'status':'NOT_RUN'}
    path=ROOT/'docs'/'gemini-smoke.json'
    if provider.kind!='gemini' or not provider.key:
        report['error']='Set LLM_PROVIDER=gemini and GEMINI_API_KEY in the local .env.'
        path.write_text(json.dumps(report,indent=2),encoding='utf-8');print(report['error']);return 2
    e=Episode(title='Connection check',payer='A',participants=['A','B'],total=100,components=[Component(description='Shared item',amount=100)])
    e.evidence.append(Evidence(type='human_message',content='I need to check the bill before describing who shared it.',source='A'))
    try:
        d=await provider.decide(public_state(e),{'status':'NEW'})
        report.update(status='PASS',decision=d.model_dump(mode='json'),provider_call=d._provider_receipt)
    except ProviderError as error:report.update(status='FAILED',error=str(error),provider_call=error.receipt)
    path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':sys.exit(asyncio.run(main()))
