from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from backend.models.domain import Episode, Allocation, Obligation, Status

class PolicyError(ValueError):
    pass


def require(condition: bool, message: str):
    if not condition:
        raise PolicyError(message)


def format_minor(amount: int) -> str:
    return f'{amount//100}.{amount%100:02d}'


def split_minor(amount: int, weights: dict[str, int]) -> dict[str, int]:
    require(bool(weights) and all(type(w) is int and w > 0 for w in weights.values()), 'Positive integer weights required')
    denominator = sum(weights.values())
    shares = {p: amount*w//denominator for p,w in weights.items()}
    # Largest remainder, then stable lexical tie-break: no invented adjustment.
    order = sorted(weights, key=lambda p: (-(amount*weights[p] % denominator), p))
    for p in order[:amount-sum(shares.values())]:
        shares[p] += 1
    return shares


def validate_conservation(e: Episode):
    require(sum(c.amount for c in e.components) == e.total, 'Component amounts do not match expense total')
    require(bool(e.allocations), 'No allocations exist')
    require(sum(a.amount for a in e.allocations) == e.total, 'Allocation total does not match expense total')
    for c in e.components:
        rows = [a for a in e.allocations if a.component_id == c.id]
        require(sum(a.amount for a in rows) == c.amount, f'Component {c.description} does not conserve money')
        require({a.participant for a in rows} == set(c.consumers), 'Allocations must include exactly the supported consumers')
        expected = component_shares(c)
        require(all(a.amount == expected[a.participant] for a in rows), 'Allocation differs from supported weights')
    expected_obligations = {(a.component_id,a.participant):a.amount for a in e.allocations if a.participant != e.payer and a.amount > 0}
    actual = {(o.component_id,o.debtor):o.amount for o in e.obligations}
    require(actual == expected_obligations and len(actual) == len(e.obligations), 'Obligations must match allocations')


def allocate(e: Episode):
    require(not e.obligations, 'Existing ledger cannot be silently replaced')
    require(sum(c.amount for c in e.components) == e.total, 'Component total is inconsistent')
    result = []
    obligations = []
    for c in e.components:
        require(c.verification_status == 'SUPPORTED' and c.dispute_status == 'CLEAR', f'Material context missing or conflicting: {c.description}')
        require(set(c.weights) | set(c.fixed_amounts) == set(c.consumers), 'Every consumer needs a supported allocation instruction')
        ids = list(dict.fromkeys(c.supporting_evidence + [x.evidence_id for x in c.context_citations]))
        shares = component_shares(c)
        for p,amount in shares.items():
            explanation = c.allocation_explanation or f'{c.description}: share based on the stated participants and sharing instructions.'
            result.append(Allocation(component_id=c.id, participant=p, amount=amount, supporting_evidence=ids, reason=explanation))
            if p != e.payer and amount:
                obligations.append(Obligation(debtor=p, creditor=e.payer, component_id=c.id, amount=amount, supporting_evidence=ids, reason=explanation))
    e.allocations = result
    e.obligations = obligations
    validate_conservation(e)


def reminder_eligibility(e: Episode, request, channel='message') -> tuple[bool,str]:
    from backend.services.follow_up import eligibility
    obligations=[o for o in e.obligations if o.id in request.obligation_ids and o.remaining > 0]
    if not obligations: return False,'Already resolved'
    results=[eligibility(e,o,channel) for o in obligations]
    return next((r for r in results if r[0]),results[0])


def can_close(e: Episode):
    validate_conservation(e)
    require(all(o.status in ('PAID','WAIVED') for o in e.obligations), 'Unresolved obligations remain')
    require(all(d.status == 'RESOLVED' for d in e.disputes), 'Unresolved disputes remain')
    require(all(p.status == 'MATCHED' for p in e.payments), 'Unmatched or ambiguous payments remain')
    require(all(q.status == 'ANSWERED' for q in e.clarifications), 'Material questions remain')
    require(all(c.verification_status == 'SUPPORTED' and c.dispute_status == 'CLEAR' for c in e.components), 'Unresolved component context remains')


def refresh_status(e: Episode):
    if e.status in (Status.CLOSED, Status.ESCALATED): return
    if any(o.status == 'DISPUTED' for o in e.obligations) or any(c.dispute_status != 'CLEAR' for c in e.components):
        e.status = Status.PARTIALLY_DISPUTED
    elif not e.obligations and not e.allocations:
        e.status = Status.NEEDS_CLARIFICATION if any(q.status == 'OPEN' for q in e.clarifications) else Status.UNDERSTANDING
    elif any(o.amount_paid and o.remaining for o in e.obligations): e.status = Status.PARTIALLY_PAID
    elif any(o.status == 'ACTIVE' for o in e.obligations): e.status = Status.ACTIVE
    elif any(o.status == 'UNCONFIRMED' for o in e.obligations): e.status = Status.AWAITING_CONFIRMATION
    elif any(o.status == 'CONFIRMED' for o in e.obligations): e.status = Status.CONFIRMED
    else: e.status = Status.RECONCILED


def parse_minor(value: str) -> int:
    require(isinstance(value,str) and bool(re.fullmatch(r'\d+(?:\.\d{1,2})?',value)), 'Money must be a nonnegative decimal string with at most two places')
    amount=int(Decimal(value)*100)
    require(0 <= amount <= 9_000_000_000_000,'Amount outside supported bounds')
    return amount


def percentage_weights(values: dict[str,str], complete=True) -> dict[str,int]:
    try: percentages={p:Decimal(value) for p,value in values.items()}
    except (InvalidOperation,ValueError): raise PolicyError('Invalid percentage') from None
    require(all(n.is_finite() and 0<n<=100 and n.as_tuple().exponent>=-2 for n in percentages.values()),'Positive percentages with at most two decimal places required')
    if complete: require(sum(percentages.values())==100,'Explicit percentages must sum to 100')
    return {p:int(n*100) for p,n in percentages.items()}


def component_shares(c):
    require(not (set(c.weights)&set(c.fixed_amounts)), 'A participant cannot have both fixed and weighted shares')
    require(set(c.weights)|set(c.fixed_amounts)==set(c.consumers), 'All consumers need supported allocation instructions')
    require(all(type(n) is int and n>=0 for n in c.fixed_amounts.values()),'Fixed shares must be nonnegative minor units')
    remainder=c.amount-sum(c.fixed_amounts.values())
    require(remainder>=0,'Fixed shares exceed component amount')
    if not c.weights:
        require(remainder==0,'Fixed shares do not account for the component total')
        return dict(c.fixed_amounts)
    return {**c.fixed_amounts,**split_minor(remainder,c.weights)}
