from backend.models.domain import Component, Citation, Clarification, Dispute, SettlementRequest, Status, Evidence, ReminderApproval, now, uid
from backend.agent.system_prompt import RULES
from backend.services.finance import parse_minor, percentage_weights, component_shares, require, PolicyError, format_minor, allocate, validate_conservation, reminder_eligibility, can_close, refresh_status

from backend.services.follow_up import eligibility, render_message

def find(items, id):
    value = next((x for x in items if x.id == id), None)
    require(value is not None, f'Unknown entity: {id}')
    return value


def cited(e, evidence_id, quote):
    ev = find(e.evidence, evidence_id)
    require(ev.verification_status in ('ASSERTED','VERIFIED'), 'Ambiguous, conflicting or raw evidence cannot change the ledger')
    require(quote.strip() and quote in ev.content, 'Citation must be an exact nonempty quote from evidence')
    return ev


def scoped_obligations(e, ids):
    require(len(ids) == len(set(ids)), 'Duplicate obligation IDs')
    return [find(e.obligations, id) for id in ids]


async def execute(e, d, rails):
    require(e.status != Status.CLOSED, 'Closed episodes cannot act')
    require(all(r in RULES for r in d.rule_ids), 'Unknown policy rule ID')
    if d.tool == 'wait': return {'status':'WAITING','requires_external_input':True}
    if d.tool == 'reconstruct_context':
        require(not e.allocations, 'Established financial ledger cannot be overwritten')
        changed = False
        for item in d.items:
            c = find(e.components, item.component_id)
            require(c.dispute_status == 'CLEAR', 'Conflicting context requires human resolution')
            require(len(set(item.consumers)) == len(item.consumers) and set(item.consumers) <= set(e.participants), 'Consumers must be unique existing participants')
            require(not (item.percentages and item.weights),'Choose relative weights or percentages, never both')
            weights=percentage_weights(item.percentages,item.complete) if item.percentages else item.weights
            fixed={p:parse_minor(value) for p,value in item.fixed_amounts.items()}
            require(not set(weights)&set(fixed),'Fixed and weighted instructions overlap')
            require(set(weights)|set(fixed) <= set(item.consumers),'Allocation instructions must concern known consumers')
            for citation in item.citations:
                ev = cited(e,citation.evidence_id,citation.quote)
                require(ev.type in ('human_message','voice_transcript','expense_created','allocation_instruction'), 'Rail evidence and rough proposed splits cannot prove consumption or responsibility')
            if c.consumers:
                require(set(c.consumers)<=set(item.consumers) and all(weights.get(p)==w for p,w in c.weights.items()) and all(fixed.get(p)==v for p,v in c.fixed_amounts.items()),'Conflicting claims cannot overwrite supported context; mark conflict')
                if c.verification_status=='SUPPORTED':
                    require(set(c.consumers)==set(item.consumers) and c.weights==weights and c.fixed_amounts==fixed,'Supported context cannot change without agreement')
                    continue
            c.consumers = item.consumers
            c.weights = weights
            c.fixed_amounts = fixed
            c.context_citations = list({(x.evidence_id,x.quote):x for x in c.context_citations+item.citations}.values())
            c.allocation_explanation = item.explanation
            c.verification_status = 'SUPPORTED' if item.complete else 'PARTIAL'
            if item.complete: component_shares(c)
            changed = True
        require(changed, 'No new context; choose another action or wait')
        for q in e.clarifications:
            if q.status == 'OPEN' and q.topic=='context' and all(find(e.components,id).verification_status == 'SUPPORTED' and find(e.components,id).dispute_status=='CLEAR' for id in q.affected_components): q.status = 'ANSWERED'
        e.unknowns = [c.id for c in e.components if c.verification_status != 'SUPPORTED']
        e.status = Status.INCONSISTENT if sum(c.amount for c in e.components)!=e.total else Status.READY_TO_RECONCILE if not e.unknowns else Status.UNDERSTANDING
        return {'status':'CONTEXT_UPDATED','unknown_components':e.unknowns}
    if d.tool == 'review_split_preference':
        ev=find(e.evidence,d.evidence_id)
        require(ev.type=='USER_PROPOSED_SPLIT' and ev.source==e.payer,'Review needs the payer’s proposed-split evidence')
        require(not e.preference_reviewed,'Preference already reviewed')
        require(all(c.verification_status=='SUPPORTED' for c in e.components),'Review the rough proposal only after material context is known')
        expected={p:0 for p in e.participants}
        for c in e.components:
            for p,n in component_shares(c).items():expected[p]+=n
        discrepancies={p:{'proposed':n,'supported':expected[p]} for p,n in e.user_proposed_split.items() if n!=expected[p]}
        e.preference_reviewed=True
        for q in e.clarifications:
            if q.topic=='preference':q.status='ANSWERED'
        e.messages.append({'speaker':'Owedience','recipient':e.payer,'message':d.message,'timestamp':e.clock.isoformat()})
        return {'status':'PREFERENCE_REVIEWED','discrepancies':discrepancies,'supported_shares':expected,'financial_state_changed':False,'exact_message':d.message,'recipient':e.payer}
    if d.tool == 'describe_bill_items':
        require(not e.allocations and not e.bill_itemization_complete,'Only an unitemized, unallocated bill can be itemized')
        ev=cited(e,d.evidence_id,d.quote)
        require(ev.type=='human_message' and ev.source==e.payer,'Bill items need payer-supplied evidence')
        components=[]
        for item in d.items:
            require(item.description.casefold() in d.quote.casefold(),'Item description must appear in the source statement')
            amount=parse_minor(item.amount)
            require(amount>0,'Bill items must be positive')
            import re
            numbers=re.findall(r'(?<![\d.])\d+(?:\.\d{1,2})?(?!\d|\.\d)',d.quote.replace(',',''))
            require(amount in [parse_minor(n) for n in numbers],'Item amount must appear in payer evidence')
            components.append(Component(description=item.description,amount=amount,supporting_evidence=[ev.id]))
        require(sum(c.amount for c in components)==e.total,'Supplied bill items do not reconcile with the total; ask rather than invent an adjustment')
        for q in e.clarifications:
            if q.topic=='bill':q.status='ANSWERED'
            else:q.affected_components=[c.id for c in components]
        e.components=components
        e.bill_itemization_complete=True
        e.unknowns=[c.id for c in components]
        e.status=Status.UNDERSTANDING
        return {'status':'BILL_ITEMS_DESCRIBED','component_ids':e.unknowns}
    if d.tool == 'correct_bill_amounts':
        require(not e.allocations,'Established financial amounts require human escalation')
        ev=cited(e,d.evidence_id,d.quote)
        require(ev.type=='human_message' and ev.source==e.payer,'Only payer-supplied correction evidence may change bill amounts')
        require(d.total is not None or bool(d.components),'Provide an explicit correction')
        def supported_amount(value):
            amount=parse_minor(value)
            require(amount>0,'Bill amounts must be positive')
            import re
            normal=d.quote.replace(',','')
            numbers=re.findall(r'(?<![\d.])\d+(?:\.\d{1,2})?(?!\d|\.\d)',normal)
            require(amount in [parse_minor(n) for n in numbers],'Corrected amount must appear literally in the quoted human evidence')
            return amount
        if d.total is not None:e.total=supported_amount(d.total)
        for change in d.components:
            c=find(e.components,change.component_id);c.amount=supported_amount(change.amount)
            c.supporting_evidence=list(dict.fromkeys(c.supporting_evidence+[ev.id]))
        consistent=sum(c.amount for c in e.components)==e.total
        if consistent:
            for q in e.clarifications:
                if q.topic=='bill':q.status='ANSWERED'
        e.status=Status.READY_TO_RECONCILE if consistent and all(c.verification_status=='SUPPORTED' for c in e.components) else Status.UNDERSTANDING if consistent else Status.INCONSISTENT
        return {'status':'BILL_CORRECTION_RECORDED','consistent':consistent}
    if d.tool == 'resolve_context_conflict':
        require(not e.allocations, 'Changing an established allocation requires human escalation')
        for item in d.items:
            c = find(e.components,item.component_id)
            require(c.dispute_status == 'CONFLICTING', 'Component has no context conflict')
            require(set(item.consumers) <= set(e.participants) and len(set(item.consumers)) == len(item.consumers), 'Existing unique participants required')
            weights=percentage_weights(item.percentages) if item.percentages else item.weights
            fixed={p:parse_minor(v) for p,v in item.fixed_amounts.items()}
            require(set(weights)|set(fixed)==set(item.consumers),'Complete allocation instructions required')
            sources = set()
            for citation in item.citations:
                ev = cited(e,citation.evidence_id,citation.quote)
                require(ev.type == 'human_message', 'Human agreement is required to resolve conflict')
                sources.add(ev.source)
            require({e.payer,*c.consumers,*item.consumers} <= sources, 'Payer and all affected participants must explicitly agree')
            c.consumers = item.consumers
            c.weights = weights
            c.fixed_amounts = fixed
            component_shares(c)
            c.context_citations = item.citations
            c.verification_status = 'SUPPORTED'
            c.dispute_status = 'CLEAR'
        for q in e.clarifications:
            if all(find(e.components,id).dispute_status == 'CLEAR' and find(e.components,id).verification_status == 'SUPPORTED' for id in q.affected_components): q.status = 'ANSWERED'
        e.unknowns = [c.id for c in e.components if c.verification_status != 'SUPPORTED']
        e.status = Status.READY_TO_RECONCILE if not e.unknowns else Status.UNDERSTANDING
        return {'status':'CONTEXT_RESOLVED_BY_AGREEMENT'}
    if d.tool == 'mark_context_conflict':
        ev = find(e.evidence,d.evidence_id)
        require(ev.type in ('human_message','voice_transcript'), 'Conflict needs participant evidence')
        require(ev.verification_status in ('ASSERTED','VERIFIED','CONFLICTING'), 'Unusable evidence')
        for id in d.component_ids:
            c = find(e.components,id)
            c.dispute_status = 'CONFLICTING'
            for o in e.obligations:
                if o.component_id == id and o.remaining: o.status = 'DISPUTED'
        ev.verification_status = 'CONFLICTING'
        e.status = Status.PARTIALLY_DISPUTED
        return {'status':'CONFLICT_PRESERVED','components':d.component_ids}
    if d.tool == 'request_clarification':
        require(d.recipient in e.participants, 'Recipient outside episode scope')
        for id in d.component_ids:
            c = find(e.components,id)
            require(d.topic=='preference' and bool(e.user_proposed_split) or d.topic=='bill' and (sum(x.amount for x in e.components)!=e.total or not e.bill_itemization_complete) or c.verification_status != 'SUPPORTED' or c.dispute_status != 'CLEAR' or any(o.component_id == id and o.status == 'DISPUTED' for o in e.obligations), 'Question must concern materially missing or disputed context')
        require(not any(q.status == 'OPEN' and q.topic == d.topic and set(q.affected_components) == set(d.component_ids) for q in e.clarifications), 'Question already pending; wait for a response')
        result = await rails.call(e,'messaging','send_message',{'recipient':d.recipient,'message':d.message},uid('clarify'))
        e.clarifications.append(Clarification(recipient=d.recipient,message=d.message,affected_components=d.component_ids,topic=d.topic,evidence_count=len(e.evidence)))
        e.messages.append({'speaker':'Owedience','recipient':d.recipient,'message':d.message,'timestamp':e.clock.isoformat()})
        refresh_status(e)
        return {**result,'requires_external_input':True}
    if d.tool == 'propose_allocation':
        require(e.status in (Status.UNDERSTANDING,Status.READY_TO_RECONCILE,Status.NEEDS_CLARIFICATION,Status.INCONSISTENT), 'Allocation not legal in current state')
        require(not e.user_proposed_split or e.preference_reviewed,'Review rough split against supported context before proposing obligations')
        allocate(e)
        e.status = Status.AWAITING_CONFIRMATION
        return {'status':'ALLOCATION_PROPOSED','allocation_total':sum(a.amount for a in e.allocations),'obligation_count':len(e.obligations)}
    if d.tool == 'record_response':
        validate_conservation(e)
        ev = cited(e,d.evidence_id,d.quote)
        require(ev.type == 'human_message', 'Financial authorisation requires an explicit human response')
        obligations = scoped_obligations(e,d.obligation_ids)
        if ev.payload.get('intent'):
            require(ev.payload['intent'] == d.kind and set(d.obligation_ids) <= set(ev.payload['obligation_ids']), 'Response must respect exact human intent and selected components')
        # One cited human statement cannot be reinterpreted later as a different action.
        previous = [a for a in e.audit_events if a.accepted and a.action == 'record_response' and a.tool_request.get('evidence_id') == ev.id]
        require(not previous or all(a.tool_request.get('kind') == d.kind for a in previous), 'Human response already consumed for a different intent')
        for o in obligations:
            if d.kind == 'CONFIRM':
                require(ev.source == o.debtor, 'Only debtor may confirm their obligation; payer cannot confirm for them')
                require(o.status == 'UNCONFIRMED', 'Only unconfirmed shares can be confirmed; disputes require agreement')
                o.status = 'CONFIRMED'
                o.confirmation_evidence.append(ev.id)
            elif d.kind == 'WAIVE':
                require(ev.source == e.payer, 'Only payer may waive an obligation')
                require(o.status not in ('PAID','WAIVED'), 'Obligation already resolved')
                o.status = 'WAIVED'
                o.reminder_approval = None
                o.follow_up_authorized = 'DECLINED'
                o.confirmation_evidence.append(ev.id)
                for dispute in e.disputes:
                    if all(find(e.obligations,id).status in ('WAIVED','PAID') for id in dispute.obligation_ids): dispute.status = 'RESOLVED'
            elif d.kind == 'DISPUTE':
                require(ev.source in (o.debtor,e.payer), 'Speaker cannot dispute someone else’s share')
                require(o.status not in ('PAID','WAIVED'), 'Resolved payments require manual review')
                o.status = 'DISPUTED'
                o.reminder_approval = None
                if o.follow_up_authorized == 'AUTHORIZED': o.follow_up_authorized = 'PAUSED'
                find(e.components,o.component_id).dispute_status = 'PARTIALLY_DISPUTED'
            elif d.kind == 'RESOLVE_DISPUTE':
                require(o.status == 'DISPUTED' and ev.source in (o.debtor,e.payer), 'Dispute resolution requires involved parties')
                o.confirmation_evidence.append(ev.id)
                consent = {find(e.evidence,id).source for id in o.confirmation_evidence if find(e.evidence,id).payload.get('intent') == 'RESOLVE_DISPUTE'}
                if {o.debtor,e.payer} <= consent:
                    o.status = 'CONFIRMED' if o.amount_paid == 0 else 'PARTIALLY_PAID'
                    for dispute in e.disputes:
                        if all(find(e.obligations,id).status != 'DISPUTED' for id in dispute.obligation_ids): dispute.status = 'RESOLVED'
            o.updated_at = now()
        if d.kind == 'DISPUTE':
            require(not any(set(x.obligation_ids) == set(d.obligation_ids) and x.status == 'OPEN' for x in e.disputes), 'Dispute already recorded')
            e.disputes.append(Dispute(obligation_ids=d.obligation_ids,evidence_id=ev.id))
        for c in e.components:
            if c.dispute_status == 'PARTIALLY_DISPUTED' and not any(o.component_id == c.id and o.status == 'DISPUTED' for o in e.obligations): c.dispute_status = 'CLEAR'
        for q in e.clarifications:
            if q.status == 'OPEN' and q.topic=='context' and all(not any(o.component_id == id and o.status == 'DISPUTED' for o in e.obligations) and find(e.components,id).dispute_status == 'CLEAR' for id in q.affected_components): q.status = 'ANSWERED'
        refresh_status(e)
        return {'status':'RESPONSE_RECORDED','kind':d.kind,'obligation_ids':d.obligation_ids}
    if d.tool == 'create_settlement_request':
        require(e.status != Status.ESCALATED, 'Payer must explicitly resume autonomous settlement')
        validate_conservation(e)
        obligations = scoped_obligations(e,d.obligation_ids)
        require(all(o.debtor == d.debtor and o.status in ('CONFIRMED','ACTIVE','PARTIALLY_PAID') and o.remaining > 0 for o in obligations), 'Only confirmed undisputed outstanding shares may be requested')
        active_ids = {id for r in e.settlement_requests for id in r.obligation_ids if find(e.obligations,id).status in ('ACTIVE','PARTIALLY_PAID')}
        require(not set(d.obligation_ids) & active_ids, 'A request already exists for these shares')
        reference = uid('payref')
        amount = sum(o.remaining for o in obligations)
        result = await rails.call(e,'pine_labs','create_payment_link',{'debtor':d.debtor,'creditor':e.payer,'amount':amount,'currency':e.currency,'obligation_ids':d.obligation_ids},reference)
        require(result.get('status') != 'PENDING_EXTERNAL_RESPONSE', 'Payment rail response pending')
        e.settlement_requests.append(SettlementRequest(debtor=d.debtor,obligation_ids=d.obligation_ids,amount=amount,reference=reference,url=result.get('url'),mode=result.get('mode','real'),created_at=e.clock))
        for o in obligations:
            if o.status == 'CONFIRMED': o.status = 'ACTIVE'
        refresh_status(e)
        return {'status':'REQUEST_CREATED','reference':reference,'payment_rail':result,'notice':'Payment reference prepared. No debtor contacted.'}
    if d.tool == 'match_payment':
        p = find(e.payments,d.payment_id)
        require(p.status != 'MATCHED', 'Payment already applied')
        ev = find(e.evidence,p.evidence_id)
        if d.resolution_evidence_id:
            resolved = find(e.evidence,d.resolution_evidence_id)
            require(resolved.type == 'payment_reference' and resolved.verification_status == 'VERIFIED', 'Resolution requires verified external payment reference')
            facts = resolved.payload
            require((facts['payment_id'],facts['debtor'],facts['creditor'],facts['currency']) == (p.id,p.debtor,p.creditor,p.currency), 'Resolved reference must agree with the immutable payment identity')
            p.reference = facts['reference']
            ev = resolved
        else:
            require(p.status == 'UNMATCHED', 'Ambiguous matching requires new verified reference evidence')
        requests = [r for r in e.settlement_requests if p.reference == r.reference]
        if ev.verification_status != 'VERIFIED' or len(requests) != 1:
            p.status = 'AMBIGUOUS'
            return {'status':'PAYMENT_MATCH_AMBIGUOUS','ledger_changed':False}
        request = requests[0]
        obligations = scoped_obligations(e,request.obligation_ids)
        eligible = [o for o in obligations if o.status in ('ACTIVE','PARTIALLY_PAID','PAID')]
        if p.debtor != request.debtor or p.creditor != e.payer or p.currency != e.currency or p.amount > sum(o.remaining for o in eligible) or not eligible:
            p.status = 'AMBIGUOUS'
            return {'status':'PAYMENT_MATCH_AMBIGUOUS','ledger_changed':False}
        remaining = p.amount
        for o in eligible:
            applied = min(remaining,o.remaining)
            if not applied: continue
            o.reminder_approval = None
            o.amount_paid += applied
            p.applied[o.id] = applied
            remaining -= applied
            o.status = 'PAID' if o.remaining == 0 else 'PARTIALLY_PAID'
        require(remaining == 0, 'Payment cannot be fully attributed')
        p.status = 'MATCHED'
        refresh_status(e)
        return {'status':'PAYMENT_APPLIED','applied':p.applied,'remaining':sum(o.remaining for o in e.obligations)}
    if d.tool == 'request_reminder_approval':
        o=find(e.obligations,d.obligation_id)
        eligible,reason=eligibility(e,o)
        require(eligible,reason)
        require(o.reminder_approval is None,'Approval already requested; wait for creditor')
        require(not eligibility(e,o,sending=True)[0],'Policy already permits bounded automatic sending')
        require(d.message.rstrip().endswith('?'),'Ask the creditor a question about sending a reminder')
        message=render_message(e,o,d.message)
        o.reminder_approval=ReminderApproval(amount=o.remaining,requested_at=e.clock,message=message)
        e.messages.append({'speaker':'Owedience','recipient':o.creditor,'message':message,'timestamp':e.clock.isoformat(),'obligation_id':o.id})
        return {'status':'APPROVAL_REQUESTED','exact_message':message,'recipient':o.creditor,'obligation_id':o.id}
    if d.tool == 'remind':
        o=find(e.obligations,d.obligation_id)
        request=next((r for r in reversed(e.settlement_requests) if o.id in r.obligation_ids and r.debtor==o.debtor),None)
        require(request is not None,'No payment reference exists for this obligation. Consider create_settlement_request before remind.')
        eligible,reason=eligibility(e,o,d.channel,sending=True)
        require(eligible,reason)
        message=render_message(e,o,d.message,request)
        result=await rails.messaging.send_message(e,o.debtor,message,o.id)
        require(result.get('status') in ('SIMULATED_DELIVERY','DELIVERED','MESSAGE_READY'),'Messaging connector did not confirm message acceptance')
        # MESSAGE_READY is not delivery and consumes neither approval nor contact budget.
        if result['status']=='MESSAGE_READY': return {**result,'requires_external_input':True,'exact_message':message,'recipient':o.debtor}
        o.reminder_count+=1;o.last_reminder_at=e.clock;o.reminder_approval=None;o.next_check_after=None
        o.reminder_history.append({'timestamp':e.clock.isoformat(),'amount':o.remaining,'message':message,'connector':result})
        request.reminders+=1;request.last_reminder_at=e.clock
        e.messages.append({'speaker':'Owedience','recipient':o.debtor,'message':message,'timestamp':e.clock.isoformat(),'obligation_id':o.id,'delivery_status':result['status']})
        return {**result,'exact_message':message,'recipient':o.debtor,'obligation_id':o.id,'amount_remaining':o.remaining,'reminder_count':o.reminder_count}
    if d.tool == 'escalate':
        if d.obligation_id:
            o=find(e.obligations,d.obligation_id)
            require(o.follow_up_authorized=='AUTHORIZED' and not o.follow_up_escalated,'Only active scoped follow-up may return to creditor')
            require(o.remaining>0,'Settled obligation needs no escalation')
            o.follow_up_escalated=True;o.reminder_approval=None
        else:
            require(e.status != Status.ESCALATED,'Already handed back to payer')
            e.status=Status.ESCALATED
            for o in e.obligations:
                if o.follow_up_authorized=='AUTHORIZED': o.follow_up_authorized='PAUSED'
                o.reminder_approval=None
        # Creditor attention is an in-app notice; no external social contact.
        e.messages.append({'speaker':'Owedience','recipient':e.payer,'message':d.message,'timestamp':e.clock.isoformat()})
        return {'status':'ESCALATE_TO_PAYER','recipient':e.payer,'exact_message':d.message,'requires_external_input':True}
    if d.tool == 'rail_tool':
        ev = find(e.evidence,d.evidence_id)
        arguments = {'evidence_id':ev.id,'raw_reference':ev.raw_reference,'content':ev.content}
        if d.operation == 'transcribe_audio':
            require(ev.type == 'audio', 'Transcription needs audio evidence')
            arguments['audio_base64'] = ev.payload.get('audio_base64')
        result = await rails.call(e,d.connector,d.operation,arguments,ev.id)
        if d.operation == 'transcribe_audio' and result.get('transcript'):
            existing = any(ev.id in x.derived_from and x.type == 'voice_transcript' for x in e.evidence)
            require(not existing,'Transcript already imported')
            # Preserve uncertain transcriptions, never promote to verified truth.
            confidence = result.get('confidence')
            supported = result.get('human_verified',False) is True
            e.evidence.append(Evidence(type='voice_transcript',content=result['transcript'],source=ev.source,connector='gnani',
                                      verification_status='ASSERTED' if supported else 'AMBIGUOUS',confidence=confidence,
                                      derived_from=[ev.id],origin=ev.origin,raw_reference=ev.raw_reference))
        return result
    if d.tool == 'close_episode':
        can_close(e)
        e.status = Status.RECONCILED
        e.status = Status.CLOSED
        e.messages.append({'speaker':'Owedience','message':'All sorted. Every share is resolved.','timestamp':e.clock.isoformat()})
        return {'status':'CLOSED','monitoring_stopped':True}
    raise PolicyError('Unknown tool')
