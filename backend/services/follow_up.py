"""Human authority and deterministic contact boundaries. No semantic agent decisions."""
from datetime import timedelta, timezone
import re
from typing import Literal
from pydantic import Field
from backend.models.domain import Model, FollowUpPolicy, Evidence, Status
from backend.services.finance import require, format_minor, refresh_status

class FollowUpAction(Model):
    actor: str
    action: Literal['authorize','decline','defer','pause','resume','approve','snooze','waive','edit']
    policy: FollowUpPolicy | None = None


def quiet(e, o, timestamp=None):
    p = o.follow_up_policy
    local = (timestamp or e.clock).astimezone(timezone(timedelta(minutes=p.utc_offset_minutes)))
    minute = local.hour*60 + local.minute
    start, end = [int(t[:2])*60+int(t[3:]) for t in (p.quiet_start,p.quiet_end)]
    inside = start <= minute < end if start < end else minute >= start or minute < end
    if not inside: return False, timestamp or e.clock
    target = local.replace(hour=end//60,minute=end%60,second=0,microsecond=0)
    if target <= local: target += timedelta(days=1)
    return True,target


def next_check(e,o):
    if not e.reminder_policy.enabled: return None
    if o.follow_up_authorized != 'AUTHORIZED' or o.remaining <= 0 or o.status not in ('CONFIRMED','ACTIVE','PARTIALLY_PAID') or o.reminder_count >= o.follow_up_policy.maximum or o.follow_up_escalated: return None
    base = max(t for t in (o.last_reminder_at,o.authorized_at) if t is not None) if o.authorized_at else o.created_at
    due = base + timedelta(hours=o.follow_up_policy.minimum_interval_hours)
    if o.next_check_after: due = max(due,o.next_check_after)
    return quiet(e,o,due)[1]


def eligibility(e,o,channel='message',sending=False):
    p=o.follow_up_policy
    if e.status in (Status.CLOSED,Status.ESCALATED): return False,'Episode monitoring stopped'
    if o.status not in ('CONFIRMED','ACTIVE','PARTIALLY_PAID'): return False,'Disputed, unconfirmed or resolved obligation'
    if o.remaining <= 0: return False,'Already settled'
    if o.follow_up_authorized != 'AUTHORIZED' or o.authorized_by != o.creditor: return False,'Creditor has not authorized this obligation'
    if o.follow_up_escalated: return False,'Returned to creditor'
    if not e.reminder_policy.enabled: return False,'Episode follow-ups disabled'
    if channel not in p.channels: return False,'Channel not authorized'
    # An unprocessed/ambiguous payment could reduce this balance. Never race it.
    refs={r.reference for r in e.settlement_requests if o.id in r.obligation_ids}
    if any(pay.status != 'MATCHED' and pay.debtor==o.debtor and pay.creditor==o.creditor and (not pay.reference or pay.reference in refs) for pay in e.payments): return False,'Payment needs review before contact'
    if o.reminder_count >= p.maximum: return False,'Reminder limit exhausted'
    due=next_check(e,o)
    if due is None or e.clock < due: return False,'Minimum interval or quiet hours requires waiting'
    if quiet(e,o)[0]: return False,'Quiet hours require waiting'
    if sending:
        approval=o.reminder_approval
        auto=p.reminder_approval_mode=='AUTO_WITHIN_LIMITS' or (p.reminder_approval_mode=='ASK_FIRST_THEN_AUTO' and o.reminder_count>0)
        if not auto and not (approval and approval.approved_at and approval.amount==o.remaining): return False,'Creditor approval required for this balance'
    return True,'Within scoped human authorization and limits'


def render_message(e,o,draft,request=None):
    require(draft.count('{{remaining}}')==1,'Include {{remaining}} exactly once; code supplies the current balance')
    cleaned=draft
    for token in ('remaining','payment_link','debtor','creditor','expense','component'): cleaned=cleaned.replace('{{'+token+'}}','')
    require(not re.search(r'\d|\{\{|https?://|www\.',cleaned),'Do not invent amounts, dates or links in a reminder')
    require(not re.search(r'\b(threat|shame|lazy|lawsuit|police|punish|irresponsible|owe me now|last warning|guilty)\b',cleaned,re.I),'Reminder must be neutral and non-aggressive')
    for person in e.participants:
        if person not in (o.debtor,o.creditor): require(not re.search(r'(?<!\w)'+re.escape(person)+r'(?!\w)',cleaned,re.I),'Do not mention unrelated participants')
    if '{{payment_link}}' in draft: require(request is not None and bool(request.url),'No verified payment link is available')
    values={'remaining':e.currency+' '+format_minor(o.remaining),'payment_link':request.url if request and request.url else '', 'debtor':o.debtor,'creditor':o.creditor,'expense':e.title,'component':next(c.description for c in e.components if c.id==o.component_id)}
    for key,value in values.items(): draft=draft.replace('{{'+key+'}}',value)
    return draft


def human_action(e,o,body):
    require(body.actor==o.creditor,'Only this obligation’s creditor may control follow-up')
    require(e.status!=Status.CLOSED and o.status not in ('PAID','WAIVED'),'Resolved obligation cannot be pursued')
    action=body.action
    if action in ('authorize','edit'):
        require(o.status in ('CONFIRMED','ACTIVE','PARTIALLY_PAID'),'Confirm the obligation before authorizing follow-up')
        require(body.policy is not None,'Explicit bounded policy required')
        if action=='edit': require(o.follow_up_authorized=='AUTHORIZED','Edit cannot authorize declined or paused follow-up')
        if action=='edit' and o.reminder_count>0: o.future_autonomy_decided=True
        o.follow_up_policy=body.policy
        o.follow_up_deferred=False
        o.follow_up_authorized='AUTHORIZED';o.authorized_by=body.actor
        if o.authorized_at is None or action=='authorize': o.authorized_at=e.clock
        o.follow_up_escalated=False;o.next_check_after=None;o.reminder_approval=None
    elif action=='decline':
        o.follow_up_authorized='DECLINED';o.reminder_approval=None
    elif action=='defer':
        require(o.follow_up_authorized=='NOT_DECIDED','Not now cannot undo an earlier decision')
        o.follow_up_deferred=True
    elif action=='pause':
        require(o.follow_up_authorized=='AUTHORIZED','Only authorized follow-up can be paused')
        o.follow_up_authorized='PAUSED';o.reminder_approval=None
    elif action=='resume':
        require(o.follow_up_authorized=='PAUSED' and o.status in ('CONFIRMED','ACTIVE','PARTIALLY_PAID'),'Only human may resume a resolved dispute or paused follow-up')
        o.follow_up_authorized='AUTHORIZED';o.follow_up_escalated=False;o.reminder_approval=None
        o.authorized_at=e.clock;o.next_check_after=None
    elif action=='approve':
        okay,reason=eligibility(e,o);require(okay,reason)
        require(o.reminder_approval is not None and o.reminder_approval.approved_at is None and o.reminder_approval.amount==o.remaining,'Approval must match the pending current balance')
        o.reminder_approval.approved_at=e.clock
    elif action=='snooze':
        require(o.reminder_approval is not None,'No pending reminder')
        o.reminder_approval=None;o.next_check_after=e.clock+timedelta(hours=o.follow_up_policy.minimum_interval_hours)
    elif action=='waive':
        o.status='WAIVED';o.reminder_approval=None;o.follow_up_authorized='DECLINED'
        for dispute in e.disputes:
            if all(next(x for x in e.obligations if x.id==oid).status in ('PAID','WAIVED') for oid in dispute.obligation_ids): dispute.status='RESOLVED'
        for c in e.components:
            if c.dispute_status=='PARTIALLY_DISPUTED' and not any(x.component_id==c.id and x.status=='DISPUTED' for x in e.obligations): c.dispute_status='CLEAR'
        refresh_status(e)
    if action in ('authorize','resume') and e.status==Status.ESCALATED:
        e.status=Status.ACTIVE
        refresh_status(e)
    ev=Evidence(type='follow_up_control',source=body.actor,timestamp=e.clock,content=body.model_dump_json(),payload={**body.model_dump(mode='json'),'obligation_id':o.id,'debtor':o.debtor,'amount_remaining':o.remaining})
    e.evidence.append(ev);o.updated_at=e.clock
    return ev
