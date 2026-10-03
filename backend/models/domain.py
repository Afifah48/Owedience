from __future__ import annotations
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal, Annotated
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex[:12]}'


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True)


class Status(StrEnum):
    DETECTED = 'DETECTED'
    UNDERSTANDING = 'UNDERSTANDING'
    NEEDS_CLARIFICATION = 'NEEDS_CLARIFICATION'
    READY_TO_RECONCILE = 'READY_TO_RECONCILE'
    INCONSISTENT = 'INCONSISTENT'
    AWAITING_CONFIRMATION = 'AWAITING_CONFIRMATION'
    PARTIALLY_DISPUTED = 'PARTIALLY_DISPUTED'
    CONFIRMED = 'CONFIRMED'
    ACTIVE = 'ACTIVE'
    PARTIALLY_PAID = 'PARTIALLY_PAID'
    ESCALATED = 'ESCALATED'
    RECONCILED = 'RECONCILED'
    CLOSED = 'CLOSED'


class Evidence(Model):
    id: str = Field(default_factory=lambda: uid('ev'))
    type: str
    content: str
    source: str
    connector: str = 'human'
    timestamp: datetime = Field(default_factory=now)
    verification_status: Literal['RAW', 'ASSERTED', 'VERIFIED', 'AMBIGUOUS', 'CONFLICTING', 'REJECTED'] = 'ASSERTED'
    confidence: float | None = Field(default=None, ge=0, le=1)
    raw_reference: str | None = None
    derived_from: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    origin: Literal['consumer', 'wizard', 'connector', 'system'] = 'consumer'


class Citation(Model):
    evidence_id: str
    quote: str = Field(min_length=1, max_length=2000)


class Component(Model):
    id: str = Field(default_factory=lambda: uid('cmp'))
    description: str = Field(min_length=1, max_length=120)
    amount: int = Field(gt=0, le=9_000_000_000_000, strict=True)
    supporting_evidence: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)
    weights: dict[str, Annotated[int, Field(strict=True,gt=0)]] = Field(default_factory=dict)
    fixed_amounts: dict[str,int] = Field(default_factory=dict)
    allocation_explanation: str = ''
    context_citations: list[Citation] = Field(default_factory=list)
    verification_status: str = 'UNKNOWN'
    dispute_status: str = 'CLEAR'


class Allocation(Model):
    component_id: str
    participant: str
    amount: int = Field(ge=0, strict=True)
    supporting_evidence: list[str]
    reason: str


class FollowUpPolicy(Model):
    minimum_interval_hours: int = Field(default=24, ge=1, le=8760)
    maximum: int = Field(default=1, ge=1, le=10)
    quiet_start: str = Field(default='22:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    quiet_end: str = Field(default='08:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    utc_offset_minutes: int = Field(default=330, ge=-720, le=840)
    channels: list[Literal['message']] = Field(default_factory=lambda: ['message'], max_length=1)
    reminder_approval_mode: Literal['ASK_EACH_TIME','ASK_FIRST_THEN_AUTO','AUTO_WITHIN_LIMITS'] = 'ASK_EACH_TIME'
    tone: Literal['neutral','friendly'] = 'neutral'
    relationship_context: str = Field(default='', max_length=200)
    @model_validator(mode='after')
    def quiet_range(self):
        if self.quiet_start == self.quiet_end:
            raise ValueError('Quiet hours must have different start and end times')
        return self


class ReminderApproval(Model):
    amount: int
    requested_at: datetime
    message: str
    approved_at: datetime | None = None


class Obligation(Model):
    id: str = Field(default_factory=lambda: uid('obl'))
    debtor: str
    creditor: str
    component_id: str
    amount: int = Field(gt=0, le=9_000_000_000_000, strict=True)
    amount_paid: int = Field(default=0, ge=0, strict=True)
    status: Literal['UNCONFIRMED', 'CONFIRMED', 'ACTIVE', 'PARTIALLY_PAID', 'PAID', 'DISPUTED', 'WAIVED'] = 'UNCONFIRMED'
    follow_up_authorized: Literal['NOT_DECIDED','AUTHORIZED','DECLINED','PAUSED'] = 'NOT_DECIDED'
    authorized_by: str | None = None
    authorized_at: datetime | None = None
    follow_up_policy: FollowUpPolicy = Field(default_factory=FollowUpPolicy)
    reminder_count: int = 0
    reminder_history: list[dict[str, Any]] = Field(default_factory=list)
    last_reminder_at: datetime | None = None
    next_check_after: datetime | None = None
    reminder_approval: ReminderApproval | None = None
    follow_up_escalated: bool = False
    future_autonomy_decided: bool = False
    follow_up_deferred: bool = False
    confirmation_evidence: list[str] = Field(default_factory=list)
    supporting_evidence: list[str]
    reason: str
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    @property
    def remaining(self) -> int:
        return 0 if self.status == 'WAIVED' else self.amount - self.amount_paid


class Payment(Model):
    id: str
    amount: int = Field(gt=0, le=9_000_000_000_000, strict=True)
    debtor: str
    creditor: str
    currency: str
    reference: str | None = None
    evidence_id: str
    status: Literal['UNMATCHED', 'MATCHED', 'AMBIGUOUS'] = 'UNMATCHED'
    applied: dict[str, int] = Field(default_factory=dict)


class SettlementRequest(Model):
    id: str = Field(default_factory=lambda: uid('request'))
    debtor: str
    obligation_ids: list[str]
    amount: int
    reference: str
    url: str | None = None
    mode: str = 'wizard'
    created_at: datetime = Field(default_factory=now)
    reminders: int = 0
    last_reminder_at: datetime | None = None


class Clarification(Model):
    id: str = Field(default_factory=lambda: uid('question'))
    recipient: str
    message: str
    topic: Literal['context','bill','preference'] = 'context'
    affected_components: list[str]
    evidence_count: int
    status: Literal['OPEN', 'ANSWERED'] = 'OPEN'
    created_at: datetime = Field(default_factory=now)


class Dispute(Model):
    id: str = Field(default_factory=lambda: uid('dispute'))
    obligation_ids: list[str]
    evidence_id: str
    status: Literal['OPEN', 'RESOLVED'] = 'OPEN'


class ReminderPolicy(Model):
    enabled: bool = True
    maximum: int = Field(default=2, ge=0, le=5)
    minimum_interval_hours: int = Field(default=24, ge=1, le=720)
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=20, ge=0, le=23)
    utc_offset_minutes: int = Field(default=330, ge=-720, le=840)
    channels: list[Literal['message', 'voice']] = Field(default_factory=lambda: ['message'])
    voice_allowed: bool = False
    @model_validator(mode='after')
    def hours(self):
        if self.start_hour >= self.end_hour:
            raise ValueError('Allowed hours must form a daytime interval')
        return self


class AuditEvent(Model):
    provider_call: dict[str, Any] = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: uid('audit'))
    timestamp: datetime = Field(default_factory=now)
    input_received: Any
    input_source: str
    real_world_source: str
    connector: str
    state_before: str
    decision: str
    concise_reason: str
    rule_ids: list[str] = Field(default_factory=list)
    action: str
    exact_message: str | None = None
    recipient: str | None = None
    tool_request: dict[str, Any] = Field(default_factory=dict)
    tool_response: dict[str, Any] = Field(default_factory=dict)
    state_after: str
    accepted: bool = True


class Episode(Model):
    id: str = Field(default_factory=lambda: uid('episode'))
    title: str = Field(min_length=1, max_length=120)
    payer: str
    participants: list[str]
    currency: Literal['INR', 'USD', 'EUR', 'GBP'] = 'INR'
    total: int = Field(gt=0, le=9_000_000_000_000, strict=True)
    merchant: str = ''
    bill_itemization_complete: bool = True
    category: str = 'other'
    category_description: str = ''
    split_preference: str = 'infer'
    user_proposed_split: dict[str,int] = Field(default_factory=dict)
    preference_reviewed: bool = False
    attachments: list[dict[str,Any]] = Field(default_factory=list)
    status: Status = Status.DETECTED
    components: list[Component]
    evidence: list[Evidence] = Field(default_factory=list)
    allocations: list[Allocation] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    payments: list[Payment] = Field(default_factory=list)
    settlement_requests: list[SettlementRequest] = Field(default_factory=list)
    disputes: list[Dispute] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    reminder_policy: ReminderPolicy = Field(default_factory=ReminderPolicy)
    audit_events: list[AuditEvent] = Field(default_factory=list)
    processed_event_ids: list[str] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    connector_failures: dict[str, str] = Field(default_factory=dict)
    connector_responses: list[dict[str, Any]] = Field(default_factory=list)
    agent_notice: str | None = None
    clock: datetime = Field(default_factory=now)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    @model_validator(mode='after')
    def participants_valid(self):
        if self.payer not in self.participants or len(set(self.participants)) != len(self.participants):
            raise ValueError('Payer must be a participant; participants must be unique')
        if not self.components or len({c.id for c in self.components}) != len(self.components):
            raise ValueError('Expense requires uniquely identified components')
        return self
