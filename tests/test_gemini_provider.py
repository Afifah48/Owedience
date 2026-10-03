import asyncio
import json
import pytest
import httpx
from backend.agent.provider import GeminiProvider, HTTPProvider, ProviderError, create_provider, gemini_parameters
from backend.agent.decision_schema import schemas
from backend.agent.system_prompt import SYSTEM_PROMPT
from backend.agent.engine import Engine
from backend.database.repository import Repository
from backend.connectors.base import Rails
from tests.test_invariants import expense

class ReplyClient:
    observed={}
    payload={}
    status=200
    def __init__(self,**kwargs):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def post(self,url,headers,json):
        type(self).observed={'url':url,'headers':headers,'body':json}
        return httpx.Response(type(self).status,json=type(self).payload)

def response(*calls):
    return {'responseId':'test-contract-response','modelVersion':'test-contract-model','usageMetadata':{'promptTokenCount':30,'totalTokenCount':40},'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'Private thought must not be retained','thought':True}]+[{'functionCall':c} for c in calls]}}]}

def wait_call():return {'name':'wait','args':{'reason':'External evidence needed.','rule_ids':['E3']}}

@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-key');monkeypatch.setenv('LLM_MODEL','gemini-3.5-flash-lite')
    monkeypatch.setattr('backend.agent.provider.httpx.AsyncClient',ReplyClient)
    ReplyClient.status=200;ReplyClient.payload=response(wait_call())
    return GeminiProvider()

def test_factory_selects_provider_only_at_composition_boundary(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER','gemini');monkeypatch.setenv('GEMINI_API_KEY','gemini-test');monkeypatch.setenv('LLM_API_KEY','openai-test');monkeypatch.delenv('LLM_MODEL',raising=False)
    gemini=create_provider();assert isinstance(gemini,GeminiProvider) and gemini.key=='gemini-test' and gemini.model=='gemini-3.5-flash-lite'
    monkeypatch.setenv('LLM_PROVIDER','openai');openai=create_provider();assert isinstance(openai,HTTPProvider) and openai.key=='openai-test'

def test_gemini_uses_same_prompt_and_schema_and_secret_only_in_header(provider,monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL','https://openai-only.invalid')
    decision=asyncio.run(provider.decide({'title':'Any context','participants':['A','B']},{'status':'NEW'}));observed=ReplyClient.observed
    assert decision.tool=='wait'
    assert observed['url']=='https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent'
    assert observed['headers']=={'x-goog-api-key':'test-key'}
    body=observed['body'];assert body['systemInstruction']['parts'][0]['text']==SYSTEM_PROMPT
    assert body['toolConfig']['functionCallingConfig']['mode']=='ANY'
    assert [t['parametersJsonSchema'] for t in body['tools'][0]['functionDeclarations']]==[gemini_parameters(t['parameters'],['A','B']) for t in schemas()]
    assert 'test-key' not in json.dumps(body) and 'test-key' not in json.dumps(decision._provider_receipt)
    assert decision._provider_receipt['response_id']=='test-contract-response'
    assert decision._provider_receipt['model']=='test-contract-model'
    assert 'Private thought' not in decision.model_dump_json()+json.dumps(decision._provider_receipt)

@pytest.mark.parametrize('calls',[[],[wait_call(),wait_call()],[{'name':'invented','args':{}}],[{'name':'wait','args':{'reason':'x','rule_ids':['E3'],'unrecognized':True}}]])
def test_gemini_rejects_invalid_or_multiple_function_calls(provider,calls):
    ReplyClient.payload=response(*calls)
    with pytest.raises(ProviderError):asyncio.run(provider.decide({},{}))

def test_gemini_rejects_truncated_response(provider):
    ReplyClient.payload=response(wait_call());ReplyClient.payload['candidates'][0]['finishReason']='MAX_TOKENS'
    with pytest.raises(ProviderError,match='INCOMPLETE'):asyncio.run(provider.decide({},{}))

def test_gemini_error_body_cannot_leak_secret(provider):
    ReplyClient.status=403;ReplyClient.payload={'error':{'message':'test-key confidential text'}}
    with pytest.raises(ProviderError,match='LLM_HTTP_403') as error:asyncio.run(provider.decide({},{}))
    assert 'test-key' not in str(error.value)

def test_gemini_does_not_fall_back_to_openai_when_key_missing(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY',raising=False);monkeypatch.setenv('LLM_API_KEY','some-openai-key')
    with pytest.raises(ProviderError,match='LLM_NOT_CONFIGURED'):asyncio.run(GeminiProvider().decide({},{}))

def test_engine_persists_receipt_without_knowing_provider(provider,tmp_path):
    repo=Repository(str(tmp_path/'receipt.sqlite3'));e=expense();repo.save(e)
    result=asyncio.run(Engine(repo,provider,Rails()).handle(e.id))
    receipt=result.audit_events[-1].provider_call
    assert receipt['provider']=='gemini' and receipt['response_id']=='test-contract-response'
    assert receipt==repo.get(e.id).audit_events[-1].provider_call
    assert not result.allocations and 'Private thought' not in result.model_dump_json()

def test_gemini_retries_only_transient_server_errors(provider,monkeypatch):
    calls=[];delays=[]
    class TransientClient(ReplyClient):
        async def post(self,url,headers,json):
            calls.append(url)
            if len(calls)<3:return httpx.Response(503,json={'error':{'message':'unavailable'}})
            return httpx.Response(200,json=response(wait_call()))
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr('backend.agent.provider.httpx.AsyncClient',TransientClient)
    monkeypatch.setattr('asyncio.sleep',sleep)
    d=asyncio.run(provider.decide({},{}))
    assert d.tool=='wait' and len(calls)==3 and delays==[1,2]
    assert d._provider_receipt['attempts']==3

def test_gemini_map_adaptation_declares_names_but_never_values():
    original=next(t['parameters'] for t in schemas() if t['name']=='reconstruct_context')
    wire=gemini_parameters(original,['Maya','Dev'])
    weights=wire['$defs']['ContextItem']['properties']['weights']
    assert set(weights['properties'])=={'Maya','Dev'}
    assert all(spec['type']=='integer' and 'const' not in spec and 'default' not in spec for spec in weights['properties'].values())
    assert weights['additionalProperties'] is False
    assert original['$defs']['ContextItem']['properties']['weights']['additionalProperties']['type']=='integer'
