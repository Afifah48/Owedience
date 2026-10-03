from datetime import datetime
from typing import Annotated, Literal, Union, Any
from pydantic import Field, TypeAdapter, model_validator
from backend.models.domain import Model, uid

class ExternalEvent(Model):
    id: str = Field(default_factory=lambda: uid('event'))
    actor: str
    origin: Literal['consumer', 'wizard', 'connector'] = 'wizard'

class HumanMessage(ExternalEvent):
    type: Literal['human_message'] = 'human_message'
    content: str = Field(min_length=1, max_length=8000)
    intent: Literal['CONFIRM', 'DISPUTE', 'WAIVE', 'RESOLVE_DISPUTE'] | None = None
    obligation_ids: list[str] = Field(default_factory=list)

class AudioEvent(ExternalEvent):
    type: Literal['audio'] = 'audio'
    content: str = Field(min_length=1, max_length=500)
    audio_base64: str | None = Field(default=None, max_length=14000000)
    mime_type: str = 'audio/webm'

class PaymentEvent(ExternalEvent):
    type: Literal['payment'] = 'payment'
    payment_id: str = Field(min_length=1, max_length=120)
    amount: int = Field(gt=0, le=9_000_000_000_000, strict=True)
    debtor: str
    creditor: str
    currency: Literal['INR','USD','EUR','GBP'] = 'INR'
    reference: str | None = None
    verified: bool = False

class PaymentReference(ExternalEvent):
    type: Literal['payment_reference'] = 'payment_reference'
    payment_id: str
    reference: str = Field(min_length=1,max_length=120)
    debtor: str
    creditor: str
    currency: Literal['INR','USD','EUR','GBP'] = 'INR'
    verified: bool = False

class ClockEvent(ExternalEvent):
    type: Literal['clock'] = 'clock'
    timestamp: datetime
    @model_validator(mode='after')
    def aware(self):
        if self.timestamp.tzinfo is None: raise ValueError('Clock timestamp must include timezone')
        return self

class FailureEvent(ExternalEvent):
    type: Literal['connector_failure'] = 'connector_failure'
    connector: Literal['gnani','pine_labs','delhivery','messaging']
    content: str = Field(min_length=1, max_length=500)
    recovered: bool = False

class ConnectorResponse(ExternalEvent):
    type: Literal['connector_response'] = 'connector_response'
    connector: Literal['gnani','pine_labs','delhivery','messaging']
    operation: str
    request_key: str
    result: dict[str, Any]

class ShipmentEvent(ExternalEvent):
    type: Literal['shipment'] = 'shipment'
    content: str = Field(min_length=1, max_length=2000)
    reference: str

Event = Annotated[Union[HumanMessage, AudioEvent, PaymentEvent, PaymentReference, ClockEvent, FailureEvent, ConnectorResponse, ShipmentEvent], Field(discriminator='type')]
EVENT_ADAPTER = TypeAdapter(Event)
