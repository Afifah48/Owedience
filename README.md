# OWEDIENCE

Shared expenses, understood. **Never guess. Never overstep.**

A runnable local competition prototype: React + TypeScript, FastAPI + Pydantic, SQLite, and a real LLM-driven bounded-autonomy loop. The consumer experience and `/simulation` use the same state and engine.

# Run Owedience Locally

Prerequisites: Git, Python 3.12 or newer, and Node.js 22 LTS with npm. The exact backend packages in `requirements.lock.txt` and frontend packages in `frontend/package-lock.json` record the verified working versions. No local SQLite database, build bundle, uploads, `.env`, or machine-specific tool directory is required by the clone.

Open Windows PowerShell:

```powershell
git clone https://github.com/Afifah48/Owedience.git
cd Owedience
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
cd frontend
npm.cmd ci
cd ..
Copy-Item .env.example .env
```

Manually edit `.env` on your own computer: populate **GEMINI_API_KEY** with your key from [Google AI Studio](https://aistudio.google.com/apikey), and **SIMULATION_TOKEN** with your own nonempty local console access token. Keep both values private. Preserve `LLM_PROVIDER=gemini` and `LLM_MODEL=gemini-3.5-flash-lite`, the model verified by actual provider responses in this project. No OpenAI key or billing is required. `LLM_API_KEY` remains blank for the optional OpenAI provider. `.env` is Git-ignored; the template contains no credentials. `MONITOR_ENABLED=false` makes demonstration timing explicit; set it to `true` to enable the background real-clock checks.

`requirements.txt` also lists the supported direct dependencies; `requirements.lock.txt` pins the complete tested environment for reproducibility. If PowerShell blocks activation, run commands using `.\.venv\Scripts\python.exe` directly instead of changing machine-wide execution policy.

Run one **real Gemini** connection test, then start the backend:

```powershell
python -m scripts.gemini_smoke
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

A successful smoke report has `status: PASS`, the actual model, and provider response metadata. HTTP 429 or provider/service unavailability is reported separately from code failures; no mock or offline reasoning agent is substituted. The script writes its safe diagnostic report locally under `docs/`.

In a second PowerShell terminal, enter the clone's frontend directory:

```powershell
cd <path-to-your-clone>\Owedience\frontend
npm.cmd run dev
```

Open **http://127.0.0.1:5173**. Vite forwards `/api` to the backend on port 8000. API documentation is at **http://127.0.0.1:8000/docs**. Choose **Simulation console** in the sidebar and enter the same local `SIMULATION_TOKEN` you configured. Its consumer and simulation views share the same backend, persistent state and real Gemini agent.

`CONNECTOR_MODE=wizard` simulates external-world integrations and delivery observations for the competition. **SIMULATED_DELIVERY is not real WhatsApp delivery**, and simulated payment/link observations do not perform real money movements. Wizard never replaces Gemini's reasoning. The optional real connector adapters fail safely until real integrations are configured.

The database is **not committed**. On startup, the repository creates the parent directory and required SQLite tables at `DATABASE_PATH=data/owedience.sqlite3`. An empty clone starts with no episodes. Agent prompts, rules, schemas and settlement policies are source files; their behavior does not depend on pre-existing demo rows. Existing JSON snapshots use model defaults for newer fields. This project does not implement general relational schema migration beyond its stable episode table.

To serve the built frontend through FastAPI instead of Vite, build before starting the backend:

```powershell
cd frontend
npm.cmd run build
cd ..
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Then use **http://127.0.0.1:8000**. `start-backend.ps1` and `start-frontend.ps1` are convenience scripts. For dependency and functional verification:

```powershell
python -m pytest -q
cd frontend
npm.cmd run build
```

`gemini` uses Google's official Gemini Developer `generateContent` API. `openai` remains optional via Responses, and `compatible` supports a configurable Chat Completions endpoint. All providers share `Provider.decide(state, observation)`, the system prompt and canonical decision schemas. Keys stay backend-only. See [the real Gemini audit](docs/agent-audit.md) and [the complete settlement recording guide](docs/settlement-follow-up.md).

## Use the product

1. Create an expense with a category, total, participant names, payer and natural context. Receipt/audio uploads, bill items, item assignments and rough split proposals are optional. Receipts are preserved without claiming automatic parsing. A bill mismatch is passed to the agent for clarification. Every submission runs the agent. Amounts entered in the UI use ordinary currency units; API amounts are integer minor units (100 = one INR/USD/EUR/GBP unit).
2. Owedience independently reconstructs the evidence, preserves known facts, and asks a targeted question when a material fact is missing.
3. Answer in the expense screen. Inspect the per-person component breakdowns.
4. Change **Viewing as** to the relevant participant (the mobile header also has this control). This is a local prototype identity selector, not authenticated multi-user accounts.
5. Each debtor selects **Looks right** for their shares. **Something’s wrong** lets them pause selected component shares. Only the payer sees **I’ll cover this**.
6. The agent creates settlement requests only for confirmed undisputed shares. A link/request does not count as payment. Wizard payment requests are labelled Practice mode and have no payable URL.
7. Open `/simulation` to inject verified payment facts using the issued payment reference. Smaller payments leave the remainder outstanding. Ambiguous money movement does not change balances.
8. To resolve a dispute at the existing amount, both debtor and payer must select **I agree with this amount**. To change an established financial allocation, the agent returns control to humans; it cannot silently rewrite the ledger.
9. The agent can close only after all shares are paid or explicitly waived and questions, disputes and payment ambiguities are resolved.

The creditor explicitly authorizes each obligation before follow-up. Each share has its own interval, maximum, approval mode and quiet hours. Defaults are one reminder, a 24-hour interval, ask each time, and quiet hours 22:00–08:00 at UTC+05:30. No disputed, unconfirmed or unauthorized money is pursued. Contact history belongs to the obligation, so creating another payment request cannot reset its limit.

## Competition console and external rails

`/simulation` displays the external evidence stream, current episode/ledger, and expandable agent decisions. The trace records source, origin, verification state, concise reason, numbered rules, exact message, recipient, tool request/response and before/after state. It never collects private chain-of-thought. Export JSON or CSV with the console controls; CSV cells are protected against formula injection.

Load **dinner facts** or **cab facts** to create independent episodes. These are fixtures, not precomputed solutions. The initial dinner fixture supplies reviewed human text from the example context, with asserted provenance. It intentionally omits drinks consumption. No source code contains the expected allocations or clarification question.

The Wizard can inject only external events:

- Human message: choose speaker and supply their words. Confirmation/dispute/waiver interpretation is the LLM’s job; there are no Wizard confirmation buttons.
- Audio: upload/record a real voice note from the consumer expense screen, or provide an external voice-note reference. Audio stays local and is excluded from LLM context and audits.
- Connector response: match a previously requested operation and request key. Supply a documented external response, never an agent decision.
- Payment event: stable payment ID, debtor, creditor, currency, minor-unit amount, issued reference and verification attestation. The UI labels verification as simulated.
- Verified payment reference: provide new reliable reference evidence for a previously ambiguous payment. Identity and amount cannot be changed.
- Shipment event: supply delivery facts; these cannot prove consumption or financial responsibility.
- Clock event: supply an ISO timestamp with time zone; time cannot move backwards.
- Connector failure/recovery: exercise rail failure without corrupting the ledger.

For audio, wait for the agent to request `gnani / transcribe_audio`. Copy its evidence ID/request key from the trace. Inject a connector response:

```json
{"transcript":"The person’s actual words","human_verified":true,"confidence":0.98}
```

Only mark `human_verified` after reviewing the actual recording. Unreviewed transcripts remain ambiguous even with high confidence. A person can also restate the content as a human message. The model decides whether that context is sufficient.

Gnani owns voice, Pine Labs owns money movement, Delhivery owns logistics, and messaging owns delivery of authorised messages. **All external rails currently use Wizard adapters.** These adapters simulate requests and delivery, expose pending external observations for other operations, and label all results. `CONNECTOR_MODE=real` fails safely with an unconfigured-adapter message; no Gnani, Pine Labs or Delhivery endpoint was invented. Real adapters need verified vendor documentation, credentials, webhook signature validation, authenticated actors and idempotent external delivery before replacement.

`SIMULATION_TOKEN` optionally gates simulation routes. Enter it in the console’s access field; it is kept in session storage. The intended deployment is localhost. Consumer identity selection and Wizard attestation are prototype trust boundaries, not production authentication or bank verification.

With a configured provider, the backend checks the real clock every 30 seconds. It wakes episodes with individually authorized eligible shares, at most once per hour per episode, by inserting a clock event; the LLM still chooses WAIT, creditor approval request, reminder or escalation. Closure stops checks. Scoped escalation stops only that share; global handoff pauses all previously authorized follow-ups until a human resumes each selected share. Wizard clock events exercise the same engine immediately. Set `MONITOR_ENABLED=false` for manual competition runs. This process must remain running; it is not an OS-level scheduled job.

## Architecture

See [docs/architecture.md](docs/architecture.md) for domain models, policy boundaries and transitions.

```text
External event → typed normalization → evidence + provenance → persist
  → LLM chooses one function tool
  → typed decision + policy validation
  → execute against a copy → deterministic financial validation
  → audit + commit → updated state and tool observation → LLM re-evaluates
  → wait / future event / escalation / close
```

A loop has a 16-iteration budget, repeated-action protection and a 3-rejection limit. Invalid provider output, timeouts, malformed connector responses or missing credentials preserve the last accepted state and produce an audited wait. Per-episode locks serialize events within the single backend process. SQLite atomically stores the episode and audit together. Run one backend worker for this prototype.

Components keep exact citations to evidence. Payer-supplied amounts and human consumption claims remain **ASSERTED**, never silently promoted to verified facts. Such claims may support proposed shares; explicit debtor confirmation is required before active settlement. Payment evidence must be verified and matched by stable reference and identities. Arithmetic handles exact fixed shares and supported relative weights/percentages with largest-remainder rounding and stable lexical ties; all component and episode totals must conserve money.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm.cmd run build
```

The test suite covers all 20 requested scenario classes plus citation rejection, actor authority, atomic rejection, duplicate payments, overpayment, wrong payment identity, payment recovery, conflict agreement, cross-request reminder limits, API privacy, simulation access, audit exports, provider request contracts and agent re-evaluation. Test-only providers supply decisions to test contracts; they are never available to the running application.

For **live semantic evaluation** with your configured key:

```powershell
.\.venv\Scripts\python.exe -m scripts.live_eval
```

This uses an independent temporary database and checks A–E outcomes rather than prescribing model actions. It calls the configured real provider and prints pass/fail with concise traces. For Gemini, first run `python -m scripts.gemini_smoke`, then `python -m scripts.live_eval --bc --persist` for the requested same-bill B/C comparison and visible audit episodes. Usage counts against the selected provider’s quota. Use it to validate model choice and competition readiness; mocked contract tests cannot prove semantic quality.

Official function-calling contract: https://developers.openai.com/api/docs/guides/function-calling

## Current scope

- Real LLM provider, live function tools, persistent ledger, consumer UI, Wizard console and local clock monitoring are implemented.
- External money movement, messages and Gnani processing are simulated pending verified vendor integrations. Voice capture/upload is real; transcription requires a reviewed Wizard response.
- Established allocations are not automatically changed after dispute; unchanged amounts require bilateral consent and changed amounts require human escalation.
- Live semantic outcomes require an API key. If none is supplied, no financial interpretation is fabricated.

## Creditor-authorized settlement follow-up

After confirmation, use each obligation's **Yes, handle the follow-up** controls to authorize that share with its own interval, finite maximum, approval mode and quiet hours. Gemini drafts contextual reminders; independent policy gates preserve the human's boundaries. No, Not now, Pause, Resume and Let it go retain their distinct meanings. Partial payments change the reminder to the remaining amount; verified full payment stops future contact. Wizard simulates only external delivery and labels it truthfully.

See [the exact UI recording guide, architecture and live audit](docs/settlement-follow-up.md). The approved stylesheet and expense input flow remain intact.
