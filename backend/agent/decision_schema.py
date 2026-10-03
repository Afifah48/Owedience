from typing import Annotated, Literal, Union
from pydantic import Field, TypeAdapter, PrivateAttr
from backend.models.domain import Model, Citation

class Meta(Model):
    _provider_receipt: dict = PrivateAttr(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)
    rule_ids: list[str] = Field(min_length=1)
    affected_entities: list[str] = Field(default_factory=list)

class ContextItem(Model):
    component_id: str
    consumers: list[str] = Field(min_length=1)
    weights: dict[str, Annotated[int, Field(strict=True,gt=0)]] = Field(default_factory=dict)
    fixed_amounts: dict[str,str] = Field(default_factory=dict)
    percentages: dict[str,str] = Field(default_factory=dict)
    complete: bool = True
    explanation: str = Field(default='',max_length=300)
    citations: list[Citation] = Field(min_length=1)

class Reconstruct(Meta):
    tool: Literal['reconstruct_context'] = 'reconstruct_context'
    items: list[ContextItem] = Field(min_length=1)

class ResolveContext(Meta):
    tool: Literal['resolve_context_conflict'] = 'resolve_context_conflict'
    items: list[ContextItem] = Field(min_length=1)

class ReviewPreference(Meta):
    tool: Literal['review_split_preference'] = 'review_split_preference'
    evidence_id: str
    message: str = Field(min_length=1,max_length=500)

class BillAmount(Model):
    component_id: str
    amount: str

class CorrectBill(Meta):
    tool: Literal['correct_bill_amounts'] = 'correct_bill_amounts'
    total: str | None = None
    components: list[BillAmount] = Field(default_factory=list)
    evidence_id: str
    quote: str = Field(min_length=1)

class NewBillItem(Model):
    description: str = Field(min_length=1,max_length=120)
    amount: str

class DescribeBill(Meta):
    tool: Literal['describe_bill_items'] = 'describe_bill_items'
    items: list[NewBillItem] = Field(min_length=1,max_length=100)
    evidence_id: str
    quote: str = Field(min_length=1)

class Conflict(Meta):
    tool: Literal['mark_context_conflict'] = 'mark_context_conflict'
    component_ids: list[str] = Field(min_length=1)
    evidence_id: str

class Ask(Meta):
    tool: Literal['request_clarification'] = 'request_clarification'
    recipient: str
    component_ids: list[str] = Field(min_length=1)
    message: str = Field(min_length=1, max_length=500)
    topic: Literal['context','bill','preference'] = 'context'

class Propose(Meta):
    tool: Literal['propose_allocation'] = 'propose_allocation'

class Respond(Meta):
    tool: Literal['record_response'] = 'record_response'
    kind: Literal['CONFIRM', 'DISPUTE', 'WAIVE', 'RESOLVE_DISPUTE']
    obligation_ids: list[str] = Field(min_length=1)
    evidence_id: str
    quote: str = Field(min_length=1)

class Request(Meta):
    tool: Literal['create_settlement_request'] = 'create_settlement_request'
    debtor: str
    obligation_ids: list[str] = Field(min_length=1)

class Match(Meta):
    tool: Literal['match_payment'] = 'match_payment'
    payment_id: str
    resolution_evidence_id: str | None = Field(default=None, description='Omit or null for a new UNMATCHED verified payment. ONLY for resolving AMBIGUOUS payment: ID of a NEW VERIFIED payment_reference evidence, never the original payment evidence.')

class AskReminderApproval(Meta):
    tool: Literal['request_reminder_approval'] = 'request_reminder_approval'
    obligation_id: str
    message: str = Field(min_length=1, max_length=400, description='Short question to creditor. Use {{remaining}} for the outstanding amount; never write currency numbers yourself.')

class Remind(Meta):
    tool: Literal['remind'] = 'remind'
    obligation_id: str
    channel: Literal['message'] = 'message'
    message: str = Field(min_length=1, max_length=400, description='Generate a short neutral contextual reminder to this debtor only. Include {{remaining}} exactly once. Optional {{payment_link}}. No numeric amounts, threats, guilt, unrelated people or unsupported claims.')

class Escalate(Meta):
    tool: Literal['escalate'] = 'escalate'
    message: str = Field(min_length=1, max_length=500)
    obligation_id: str | None = None

class Rail(Meta):
    tool: Literal['rail_tool'] = 'rail_tool'
    connector: Literal['gnani', 'pine_labs', 'delhivery']
    operation: Literal['transcribe_audio', 'synthesize_voice', 'get_transaction', 'check_payment_status', 'get_shipment_status', 'standardize_address']
    evidence_id: str

class Close(Meta):
    tool: Literal['close_episode'] = 'close_episode'

class Wait(Meta):
    tool: Literal['wait'] = 'wait'

Decision = Annotated[Union[Reconstruct, ResolveContext, ReviewPreference, CorrectBill, DescribeBill, Conflict, Ask, Propose, Respond, Request, Match, AskReminderApproval, Remind, Escalate, Rail, Close, Wait], Field(discriminator='tool')]
ADAPTER = TypeAdapter(Decision)
TOOL_MODELS = [Reconstruct, ResolveContext, ReviewPreference, CorrectBill, DescribeBill, Conflict, Ask, Propose, Respond, Request, Match, AskReminderApproval, Remind, Escalate, Rail, Close, Wait]

def schemas():
    descriptions={
        'reconstruct_context':'Extract NEW known sharing facts ONLY from exact human quotes. Listing participants or payment is not consumption evidence. Do not repeat stored supported facts.',
        'propose_allocation':'When all components are supported and no ledger exists, compute and propose conserved shares server-side. Use after context is reconstructed.',
        'create_settlement_request':'Prepare a payment reference for confirmed outstanding obligations of one debtor. No contact is sent. Needed before remind when no reference covers the obligation.',
        'request_reminder_approval':'Ask the CREDITOR whether to send this eligible reminder. QUESTION ending in ?. Not a debtor reminder. Include literal {{remaining}} for outstanding amount. Never duplicate pending approval.',
        'remind':'Send your generated neutral message for one authorized eligible obligation ONLY with required human approval or explicitly selected auto mode. No request ID needed: server finds its payment reference. Include literal {{remaining}} once.',
        'wait':'Stop this iteration when waiting for a human, time, or external fact. Do not repeat tools.',
        'match_payment':'Apply a new verified payment through the deterministic ledger. Prioritize payment before contact. For a new UNMATCHED verified payment, omit resolution_evidence_id or set null. Only AMBIGUOUS payments need new VERIFIED payment_reference evidence.'}
    result = []
    for model in TOOL_MODELS:
        schema = model.model_json_schema()
        name = schema['properties']['tool']['const']
        schema['properties'].pop('tool')
        schema['required'] = [x for x in schema.get('required', []) if x != 'tool']
        result.append({'name': name, 'description': descriptions.get(name,f'{name}: {model.__name__}. All monetary arithmetic is server-side.'), 'parameters': schema})
    return result
