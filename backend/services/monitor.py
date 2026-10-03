import asyncio
from datetime import timedelta
from backend.models.domain import Status, now
from backend.services.follow_up import eligibility
from backend.wizard.events import ClockEvent

async def monitor_once(engine, timestamp=None):
    """Real clock is an external event. Policy determines wake eligibility, never the semantic action."""
    timestamp = timestamp or now()
    if not getattr(engine.provider,'key',False): return
    for e in engine.repository.all():
        if e.status in (Status.CLOSED,Status.ESCALATED) or not e.obligations or not e.reminder_policy.enabled: continue
        if timestamp < e.clock: continue  # Preserve a Wizard-advanced clock.
        preview = e.model_copy(deep=True);preview.clock=timestamp
        due = any((eligibility(preview,o)[0] and (o.reminder_approval is None or o.reminder_approval.approved_at)) or (eligibility(preview,o)[1]=='Reminder limit exhausted' and not o.follow_up_escalated) for o in preview.obligations)
        if not due: continue
        last_tick = next((ev.timestamp for ev in reversed(e.evidence) if ev.type == 'clock' and ev.origin == 'connector'),None)
        if last_tick and timestamp-last_tick < timedelta(hours=1): continue
        await engine.handle(e.id,ClockEvent(actor='System clock',origin='connector',timestamp=timestamp))

async def monitor_loop(engine):
    while True:
        await asyncio.sleep(30)
        try: await monitor_once(engine)
        except Exception:
            # Scheduler cannot corrupt the transactional ledger; it retries on next tick.
            continue
