import json
import os
import hashlib
from typing import Protocol
import httpx
from backend.agent.system_prompt import SYSTEM_PROMPT
from backend.agent.decision_schema import ADAPTER, schemas

class ProviderError(RuntimeError):
    def __init__(self,message,receipt=None):
        super().__init__(message)
        self.receipt=receipt or {}

class Provider(Protocol):
    async def decide(self, state: dict, observation: dict): ...

class HTTPProvider:
    def __init__(self):
        self.kind = os.getenv('LLM_PROVIDER','openai')
        self.key = os.getenv('LLM_API_KEY','')
        self.model = os.getenv('LLM_MODEL','gpt-4.1-mini')
        self.base = os.getenv('LLM_BASE_URL','https://api.openai.com/v1').rstrip('/')
    async def decide(self, state, observation):
        if not self.key: raise ProviderError('LLM_NOT_CONFIGURED')
        if self.kind not in ('openai','compatible'): raise ProviderError('UNSUPPORTED_PROVIDER')
        context = json.dumps({'episode':state, 'previous_observation':observation}, ensure_ascii=False)
        tool_schemas = schemas()
        if self.kind == 'openai':
            path = '/responses'
            body = {'model':self.model,'instructions':SYSTEM_PROMPT, 'input':[{'role':'user','content':context}],
                    'tools':[{'type':'function',**t,'strict':False} for t in tool_schemas],
                    'tool_choice':'required','parallel_tool_calls':False,'store':False}
        else:
            path = '/chat/completions'
            body = {'model':self.model,'messages':[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':context}],
                    'tools':[{'type':'function','function':t} for t in tool_schemas], 'tool_choice':'required','parallel_tool_calls':False}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(self.base+path, headers={'Authorization':f'Bearer {self.key}'}, json=body)
                # No response body, headers or credentials in logs/errors.
                if r.status_code >= 400:
                    suffix=''
                    try:
                        code=r.json().get('error',{}).get('code')
                        if code in ('insufficient_quota','rate_limit_exceeded','invalid_api_key','model_not_found'):suffix='_'+code.upper()
                    except (ValueError,AttributeError):pass
                    raise ProviderError(f'LLM_HTTP_{r.status_code}'+suffix)
                payload = r.json()
            if self.kind == 'openai':
                calls = [x for x in payload.get('output',[]) if x.get('type') == 'function_call']
                if len(calls) != 1: raise ProviderError('EXPECTED_ONE_TOOL_CALL')
                name, args = calls[0]['name'], calls[0]['arguments']
            else:
                calls = payload['choices'][0]['message'].get('tool_calls',[])
                if len(calls) != 1: raise ProviderError('EXPECTED_ONE_TOOL_CALL')
                name, args = calls[0]['function']['name'], calls[0]['function']['arguments']
            decision = ADAPTER.validate_python({**json.loads(args),'tool':name})
            decision._provider_receipt = {'provider':self.kind,'model':payload.get('model',self.model),'endpoint':path,
                'response_id':payload.get('id'),'input_sha256':hashlib.sha256(context.encode()).hexdigest(),
                'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()}
            return decision
        except ProviderError: raise
        except httpx.HTTPError: raise ProviderError('LLM_CONNECTION_FAILED') from None
        except Exception: raise ProviderError('INVALID_LLM_DECISION') from None

def gemini_parameters(schema, participants):
    """Describe dynamic participant dictionaries with explicit allowed keys.

    This is wire-schema adaptation only: no weights, amounts or consumers are
    supplied. The allowed key set already matches the shared policy boundary.
    The canonical Pydantic schema and returned Decision remain unchanged.
    """
    if isinstance(schema,list):return [gemini_parameters(x,participants) for x in schema]
    if not isinstance(schema,dict):return schema
    result={k:gemini_parameters(v,participants) for k,v in schema.items() if k!='default'}
    if result.get('type')=='object' and isinstance(result.get('additionalProperties'),dict) and not result.get('properties'):
        value=result.pop('additionalProperties')
        result['properties']={person:value for person in participants}
        result['additionalProperties']=False
        result['description']='Participant-name keyed allocation instructions. Supply evidence-supported values. A complete context item needs a weight, fixed amount, or percentage for every consumer, including a sole consumer. Never leave all three maps empty for complete context.'
    properties=result.get('properties',{})
    if all(key in properties for key in ('consumers','weights','fixed_amounts','percentages')):
        result['description']='A component with actually known consumption facts in the cited evidence. OMIT a component from reconstruction items when none of its consumers are known; ask clarification instead. Incomplete facts may preserve a genuinely known consumer/agreement, never guessed candidates.'
        properties['consumers']['description']='Actually known consumers supported by the exact cited statement. Never the candidate participant set. If no consumer is known for a component, OMIT that entire component item rather than inventing consumers or using complete=false as a guess.'
        properties['weights']['description']='Positive integer RELATIVE sharing weights keyed by consumer name, not money. Every consumer needs an instruction when complete=true. Use weight 1 for a sole consumer; for evidence-supported equal sharing, each consumer has the same relative weight.'
        properties['fixed_amounts']['description']='Only EXACT decimal CURRENCY-UNIT amounts explicitly agreed in human words. Never copy episode/component amount numbers here: those are MINOR UNITS. Do not calculate shares here. A sole consumer without a special human-stated fixed agreement uses weights instead.'
        properties['percentages']['description']='Only exact human-stated percentage strings keyed by consumer name. Use weights for relative/equal sharing, not invented percentages.'
    return result


class GeminiProvider:
    """Official Gemini Developer REST API; returns the same validated Decision.

    Each decide call is a fresh state/observation request, matching the existing
    loop. No SDK automatic tool execution or provider-side financial logic.
    """
    kind = 'gemini'
    base = 'https://generativelanguage.googleapis.com/v1beta'

    def __init__(self):
        self.key = os.getenv('GEMINI_API_KEY','')
        self.model = os.getenv('LLM_MODEL','gemini-3.5-flash-lite')

    async def decide(self, state, observation):
        if not self.key: raise ProviderError('LLM_NOT_CONFIGURED')
        # Keep credentials in a header and a model identifier out of URL syntax.
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',self.model):
            raise ProviderError('INVALID_MODEL_NAME')
        context = json.dumps({'episode':state,'previous_observation':observation},ensure_ascii=False)
        path = f'/models/{self.model}:generateContent'
        body = {
            'systemInstruction':{'parts':[{'text':SYSTEM_PROMPT}]},
            'contents':[{'role':'user','parts':[{'text':context}]}],
            'tools':[{'functionDeclarations':[
                {'name':t['name'],'description':t['description'],'parametersJsonSchema':gemini_parameters(t['parameters'],state.get('participants',[]))}
                for t in schemas()
            ]}],
            'toolConfig':{'functionCallingConfig':{'mode':'ANY'}},
            'generationConfig':{'maxOutputTokens':8192},
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                import asyncio
                for attempt in range(3):
                    r = await client.post(self.base+path,headers={'x-goog-api-key':self.key},json=body)
                    if r.status_code not in (500,502,503,504) or attempt==2:break
                    await asyncio.sleep(2**attempt)
                # Never retain response bodies/headers on errors (may echo a key).
                if r.status_code >= 400:
                    raise ProviderError(f'LLM_HTTP_{r.status_code}',{'provider':self.kind,'requested_model':self.model,'endpoint':path,'http_status':r.status_code,'attempts':attempt+1,'response_id':None,'input_sha256':hashlib.sha256(context.encode()).hexdigest()})
                payload = r.json()
            candidates = payload.get('candidates',[])
            if len(candidates)!=1: raise ProviderError('EXPECTED_ONE_CANDIDATE')
            candidate = candidates[0]
            if candidate.get('finishReason') not in (None,'STOP'):
                raise ProviderError('INCOMPLETE_LLM_DECISION')
            calls = [part['functionCall'] for part in candidate.get('content',{}).get('parts',[]) if 'functionCall' in part]
            if len(calls)!=1: raise ProviderError('EXPECTED_ONE_TOOL_CALL')
            call = calls[0]
            decision = ADAPTER.validate_python({**call.get('args',{}),'tool':call['name']})
            decision._provider_receipt = {
                'provider':self.kind,'model':payload.get('modelVersion',self.model),
                'requested_model':self.model,'endpoint':path,'response_id':payload.get('responseId'),'attempts':attempt+1,
                'function_call_id':call.get('id'),'finish_reason':candidate.get('finishReason'),
                'usage':{k:v for k,v in payload.get('usageMetadata',{}).items()
                         if k in ('promptTokenCount','candidatesTokenCount','totalTokenCount','thoughtsTokenCount') and type(v) is int},
                'input_sha256':hashlib.sha256(context.encode()).hexdigest(),
                'prompt_sha256':hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            }
            return decision
        except ProviderError: raise
        except httpx.HTTPError: raise ProviderError('LLM_CONNECTION_FAILED') from None
        except Exception: raise ProviderError('INVALID_LLM_DECISION') from None


def create_provider() -> Provider:
    """Only this composition boundary knows how provider selection works."""
    return GeminiProvider() if os.getenv('LLM_PROVIDER','openai')=='gemini' else HTTPProvider()
