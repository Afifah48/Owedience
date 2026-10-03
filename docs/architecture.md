# Architecture and implementation boundaries

## Agent loop

`backend/agent/engine.py` normalizes each external event, commits provenance, calls a provider with structured episode state and the previous observation, validates its typed function call, executes on an isolated copy, appends the audit and commits. It repeats until external input/time is required, the episode closes, an action repeats, three actions are rejected, or the turn budget is exhausted. No category, participant, expense description or demo step chooses the next semantic action.

`provider.py` implements an async provider protocol. The Gemini Developer REST, OpenAI Responses and compatible Chat Completions implementations are swappable through environment configuration. The application supplies tools to the LLM and requires exactly one function call each iteration. Raw model text/reasoning is never retained.

## Domain and provenance

The primary reasoning unit is `Episode`: payer, participants, currency/total, components, evidence, allocations, obligations, payments, settlement requests, clarifications, disputes, communication policy and audit events.

`Evidence` holds type, content, source, connector, timestamp, origin (consumer/Wizard/connector/system), verification state, raw reference, confidence, payload and `derived_from` references. `Citation` binds derived context to exact source quotes. Assertions remain assertions. Confidence alone never certifies voice evidence.

Each component has payer-supplied amount/description, consumers, supported relative weights or exact fixed shares, citations and context/dispute status. Payer-quoted bill corrections/itemization are allowed before allocation; existing financial ledgers cannot be silently replaced. Each allocation carries component/person/amount and provenance. Each obligation is a component-level debtor-creditor line, preserving original amount, paid amount, explicit authorisation evidence and status. The consumer UI groups those lines by debtor, while the ledger freezes individual lines.

`SettlementRequest` retains stable payment reference, exact obligation IDs, amount, mode, creation time and reminder history. `Payment` records immutable amount/identities, source evidence, match state and applied minor units per obligation. Waived is a separate status from paid; prior payments remain visible.

## State constraints

Conceptual legal progression:

- DETECTED → UNDERSTANDING: external context received.
- UNDERSTANDING ↔ NEEDS_CLARIFICATION: supported facts are preserved while material questions remain.
- UNDERSTANDING → READY_TO_RECONCILE: all components have supported context.
- READY_TO_RECONCILE → AWAITING_CONFIRMATION: deterministic allocation conserves money.
- Inconsistent totals → INCONSISTENT, no active obligations created.
- UNCONFIRMED obligation → CONFIRMED only by explicit debtor agreement.
- CONFIRMED → ACTIVE only when an authorised settlement request succeeds.
- ACTIVE → PARTIALLY_PAID → PAID only from verified reliably matched money movement.
- Unresolved obligation → DISPUTED only from involved-person evidence; other lines continue.
- DISPUTED → CONFIRMED/PARTIALLY_PAID only through bilateral unchanged-amount agreement.
- Any unpaid balance → WAIVED only through explicit payer authority.
- Conflict/dispute/failure may request clarification, wait or escalate from multiple stages.
- All obligations resolved + conservation + no questions/disputes/unmatched payments → RECONCILED → CLOSED.

`tools.py` enforces action-specific transitions and permissions. `services/finance.py` derives the aggregate episode status without dictating the next LLM decision. A scoped follow-up escalation returns only that obligation to its creditor. A global ESCALATED handoff pauses all authorized follow-ups; only a human authorizes/resumes a selected share, leaving others paused. Verified payments and human responses can still be recorded. CLOSED rejects further financial events and monitoring.

## Tool schemas

Pydantic discriminated models in `decision_schema.py` produce provider function schemas and validate returned arguments. All decisions require a short factual reason and real numbered rule IDs. Tools include reconstruction, conflict marking/resolution, targeted clarification, allocation, human response, settlement request, payment match/recovery, reminder, escalation, rail operation, wait and closure.

The LLM selects consumers and relative weights from supported context; code never supplies the demo solution. Quotes must exist and entities must already exist. A claim must come from a human/context source, never from delivery evidence. Policy cannot mechanically prove whether a quote semantically entails a claim; that remains LLM judgement bounded by provenance, participant inspection and explicit authorisation.

## Financial services and policy

Money uses integer minor units. Largest-remainder allocation distributes rounding units deterministically rather than inventing adjustments. Validation conserves each component and the overall total and proves that obligation lines equal non-payer allocations. Existing allocations cannot be silently replaced.

Confirmations require the debtor; waivers require the payer; disputes require an involved actor. Structured consumer actions bind exact intent and obligation IDs. Natural-language semantics are selected by the LLM and constrained to cited speaker evidence. Silence cannot authorise anything.

Payments match issued references plus debtor, creditor and currency. Duplicate payment IDs are idempotent; conflicting duplicate facts are rejected. Overpayment, wrong identity, ambiguous or unverified evidence cannot apply funds. New verified reference evidence can resolve an ambiguous match without changing amount/identity. A mixed disputed request applies only money that fits its eligible undisputed lines; it never settles disputed balances.

Reminder authorization, policy, approval, count and history belong to each obligation. Only the creditor-control API grants/resumes authorization or selects automatic modes; no such LLM tool exists. Defaults ask for each reminder, permit one reminder, wait 24 hours, and defer during quiet hours 22:00–08:00. Gemini generates the sentence with a remaining-balance token; code inserts the verified amount and validates eligibility/wording. Pending payments, disputes, timing, channels, counts and missing human approval block contact. New payment requests cannot reset history. Limits lead to scoped agent-selected escalation. Closure separately checks conservation, all obligation statuses, disputes, questions, context and payment matches.

## Rails and Wizard boundary

`connectors/base.py` defines a uniform async operation contract. Each rail owns a narrow capability set; no external connector decides responsibility. Wizard adapters are the only configured external rails. Undocumented real adapters fail safely.

`wizard/events.py` defines typed external events: human words, audio reference/data, payment facts, verified reference evidence, clock, connector response, shipment and failure/recovery. Connector responses must match a prior request. Wizard controls supply data and cannot set allocation, agent status, confirmation state, reminder decisions or closure.

`services/monitor.py` generates real clock events when policy says an episode is due for semantic review. It does not choose a reminder, confirmation, allocation or closure action.

## Persistence and audit

SQLite stores one validated episode snapshot and all audit events in the same transaction. Async locks serialize one episode in one backend worker. Every received event and meaningful decision records source, origin, connector, timestamps, before/after status, provider response metadata and input/prompt hashes when returned, accepted/rejected outcome, short reason, policy IDs, exact wording, recipient, tool arguments and observation. Provider failure bodies/headers/keys are excluded. Audio blobs are omitted from model input, audit and simulation responses.

The future real-connector boundary will need a durable outbox/idempotency store because copying local state cannot roll back external side effects. Current Wizard adapters have no real financial or messaging side effects.

## Information architecture

Consumer routes: overview (needs-you and balances by currency), expense (context, participants, breakdown, clarification/voice, response, payments and follow-ups), and activity. Technical details appear only in `/simulation`. All visible data comes from the backend. Mobile identity switching enables prototype role testing at 390px.

Simulation presents external facts, the current ledger and expandable autonomous decisions, followed by generic Wizard event controls and audit exports. Provider setup and policy rules are disclosed there. The two views observe the same episode and engine.

## Anti-hardcoding proof

Restaurant names/components/participants occur only in `fixtures/restaurant.json`; the cab fixture contains independent facts. Business logic operates on arbitrary IDs, evidence, weights, statuses and currency amounts. Production has no fake provider. Test doubles exist only inside `tests/` and prescribe actions solely to test execution contracts. `scripts/live_eval.py` uses a real provider to evaluate semantic outcomes without prescribing an action sequence.

See [the settlement recording guide and live evidence](settlement-follow-up.md) for the per-obligation control flow, MessagingConnector contract and current tests.
