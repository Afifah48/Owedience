import json
from backend.models.domain import Evidence, Payment, Status
from backend.services.finance import require
from backend.connectors.base import OPERATIONS


def ingest(e, event):
    if event.id in e.processed_event_ids: return None
    require(e.status != Status.CLOSED, 'Closed episodes no longer accept financial events')
    if event.type in ('human_message', 'audio'):
        require(event.actor in e.participants, 'Speaker must be an existing participant')
    payload = event.model_dump(mode='json')
    # Raw audio is kept locally in state, never in audit/provider context.
    content = getattr(event, 'content', None) or json.dumps({k:v for k,v in payload.items() if k != 'audio_base64'})
    verification = 'RAW' if event.type == 'audio' else 'ASSERTED'
    connector = {'audio':'gnani','payment':'pine_labs','shipment':'delhivery'}.get(event.type,'human')
    if event.type == 'payment':
        require(event.origin != 'consumer', 'Consumers cannot fabricate verified payment rail events')
        existing = next((p for p in e.payments if p.id == event.payment_id), None)
        if existing:
            require((existing.amount,existing.debtor,existing.creditor,existing.currency,existing.reference) ==
                    (event.amount,event.debtor,event.creditor,event.currency,event.reference), 'Payment ID collision with conflicting facts')
            e.processed_event_ids.append(event.id)
            return None
        verification = 'VERIFIED' if event.verified and event.origin in ('wizard','connector') else 'AMBIGUOUS'
    if event.type == 'payment_reference':
        require(event.origin != 'consumer', 'Payment references require an external rail')
        require(any(p.id == event.payment_id for p in e.payments), 'Reference must identify an existing payment')
        verification = 'VERIFIED' if event.verified else 'AMBIGUOUS'
        connector = 'pine_labs'
    if event.type == 'clock':
        require(event.timestamp >= e.clock, 'Clock cannot move backwards')
        e.clock = event.timestamp
        verification = 'VERIFIED'
    if event.type == 'connector_failure':
        if event.recovered: e.connector_failures.pop(event.connector, None)
        else: e.connector_failures[event.connector] = event.content
        connector = event.connector
    if event.type == 'connector_response':
        require(event.operation in OPERATIONS[event.connector], 'Operation outside connector scope')
        require(any(a.tool_request.get('evidence_id') == event.request_key and a.tool_request.get('operation') == event.operation and a.tool_request.get('connector') == event.connector for a in e.audit_events), 'Response must match a previously requested rail operation')
        e.connector_responses.append(payload)
        connector = event.connector
    ev = Evidence(type=event.type, content=content, source=event.actor, connector=connector,
                  verification_status=verification, origin=event.origin, raw_reference=event.id, payload=payload)
    if event.type=='clock': ev.timestamp=e.clock
    if event.type=='human_message' and event.intent=='DISPUTE':
        ids=event.obligation_ids
        require(bool(ids) and len(ids)==len(set(ids)),'Dispute requires exact unique obligation IDs')
        scoped=[o for o in e.obligations if o.id in ids]
        require(len(scoped)==len(ids) and all(event.actor in (o.debtor,o.creditor) and o.status not in ('PAID','WAIVED') for o in scoped),'Only involved humans may dispute outstanding shares')
        # An explicit human dispute installs a safety hold before any provider call.
        for o in scoped:
            o.status='DISPUTED';o.reminder_approval=None
            if o.follow_up_authorized=='AUTHORIZED': o.follow_up_authorized='PAUSED'
            next(c for c in e.components if c.id==o.component_id).dispute_status='PARTIALLY_DISPUTED'
        e.status=Status.PARTIALLY_DISPUTED
    e.evidence.append(ev)
    if event.type == 'payment':
        e.payments.append(Payment(id=event.payment_id, amount=event.amount, debtor=event.debtor,
                                 creditor=event.creditor, currency=event.currency, reference=event.reference, evidence_id=ev.id))
    if event.type == 'human_message':
        e.messages.append({'speaker':event.actor,'message':event.content,'timestamp':ev.timestamp.isoformat()})
    e.processed_event_ids.append(event.id)
    e.agent_notice = None
    if e.status == Status.DETECTED: e.status = Status.UNDERSTANDING
    return ev
