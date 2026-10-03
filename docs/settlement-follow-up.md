# Human-authorized settlement follow-ups

You choose who Owedience may follow up with. Gemini handles reminders within the limits you explicitly set. The existing expense creation, evidence reconstruction, financial engine, Gemini provider, and visual design remain in place.

## Record the complete flow from the current UI

1. Open http://127.0.0.1:8000 and choose **New expense**. Enter title **Dinner follow-up demonstration**, category **Food & Dining**, payer **Afifah**, participants **Afifah** and **Arjun**, total **1400** INR. Leave optional itemization empty. Context: **The dinner bill is 1400. I have not provided who consumed it or how it was shared.** Click **Let Owedience sort it**. These names and amounts are demonstration facts, not production logic.
2. Gemini should ask a material sharing question. Answer in **Expense context or answer**: **Arjun and I shared the entire dinner equally.** Click **Send**. Expand **Why this amount?** to show the evidence-based explanation. The deterministic ledger proposes a ₹700 share for each person.
3. Change **Viewing as** to **Arjun** and click **Looks right**. Change back to **Afifah**. His confirmed balance does not authorize follow-up, and preparing a payment reference sends no message.
4. On Arjun's **Whole bill** share, choose **Yes, handle the follow-up**. Set **Every 3 days**, maximum **3**, **Ask me first · ask each time**, quiet hours **22:00** to **08:00**, UTC **+05:30**. Click **Save and authorize this follow-up**. This authorizes only the selected obligation. With itemized bills, each component has its own controls; authorize each component you intend to pursue.
5. Open **Simulation console**, select this expense, and enter the local `SIMULATION_TOKEN` if configured. **Episode & ledger** shows the author, scoped obligation, remaining balance, frequency, maximum, approval mode, quiet hours, reminder count, and an exact ISO **Next** timestamp.
6. Choose **Advance clock**. Paste that exact next-check ISO timestamp into **External time** and click **Inject event**. The console supplies time, not an instruction to remind. If an eligible time falls in quiet hours, the displayed next check is deferred to the next permitted time.
7. Click **Consumer view**. Gemini's question to Afifah appears on Arjun's share. No debtor reminder has been sent yet. Click **Send it**. Gemini independently generates the wording; policy checks the current balance and permission again. Activity labels the resulting message **Simulated delivery**.
8. Under **Should I handle future reminders automatically within these limits?**, choose **Yes**. This is an explicit human opt-in to `ASK_FIRST_THEN_AUTO`. Choosing **Ask me each time** keeps per-reminder approval.
9. Return to the simulation console. Copy Arjun's issued **Payment reference** from **Episode & ledger**. Choose **Inject partial / full payment**. Set **Who paid? Arjun**, a new unique payment ID such as **demo-partial**, amount **400**, and that exact reference. Keep the verification checkbox selected and click **Inject event**. Wizard attests a fictional external payment; Gemini requests matching and the deterministic engine applies it. Consumer view shows **₹400 paid · ₹300 left**.
10. Return to simulation, copy the new exact next-check ISO timestamp, and inject an **Advance clock** event for that time. Gemini may send the next eligible reminder under the explicit future-auto permission. Its message refers only to **₹300**. No payment link is invented when the Wizard payment rail provides no URL.
11. Inject another verified payment using a different ID such as **demo-final**, amount **300**, Arjun as payer, and the same issued reference. Once matched, the share is **All settled ✓**, remaining **₹0**, and no future reminder check exists. With no other unresolved facts, Gemini closes the episode.
12. Expand **Agent activity → REMIND → Input / tool request / observation** to show Gemini's model, real response ID, usage and input/prompt hashes, generated wording, scope, policy result and `SIMULATED_DELIVERY`. **HUMAN_AUTHORIZE**, **HUMAN_APPROVE** and **HUMAN_EDIT** show the creditor's separate actions. Export **Audit JSON** for the full record.

A model may choose WAIT when evidence or policy requires it. Do not use the console to dictate agent decisions. If Gemini returns HTTP 429, preserve the evidence, wait for project quota capacity, then click **Try again** or **Let Owedience continue**. Pending payment evidence blocks contact until it is safely matched/reviewed. No scripted reasoning fallback is used.

## Other controls

- **No, I'll handle it myself** persists DECLINED. Only a new creditor authorization can enable it.
- **Not now** persists an undecided, deferred choice. **Decide on follow-up** brings the question back; no contact occurs meanwhile.
- **Pause reminders** keeps the balance outstanding. **Resume follow-up** is a human action and starts a new waiting interval. Neither Gemini nor dispute resolution resumes pursuit.
- **Let it go** opens a confirmation. **Yes, let it go** marks WAIVED, retains history, records no payment, and stops reminders. Cancel changes nothing.
- **Send reminder now** requests an agent assessment; it does not bypass interval, quiet hours, maximum, authorization, payment or approval checks.
- **Settings** supports custom whole-number hours/days, a bounded maximum of 1–10 reminders, quiet hours including ranges crossing midnight, tone, and optional explicit relationship context.
- **Inject dispute** is an external human fact: select its speaker, exact affected components and statement. A typed explicit dispute installs a safety hold before the LLM call. Other independently authorized components may continue. Both involved humans must resolve the dispute; the creditor then explicitly resumes if desired.
- Global **Reminder preferences** can disable all checks, but cannot authorize anyone or override a scoped policy.

## Architecture and validation

Per-obligation authorization and policy persist in the existing SQLite episode snapshot. Old episodes default to NOT_DECIDED; an old global reminder preference is not converted into consent. The creditor-control endpoint is separate from the LLM tool schema; the agent has no tool to grant/resume permission. The local prototype retains the existing Viewing-as identity control and actor validation, not production account authentication.

Gemini decides WAIT, approval request, REMIND or scoped ESCALATE_TO_PAYER. Code validates confirmed and undisputed outstanding status, creditor authorization, interval, quiet hours, contact count, unresolved payment evidence, channel and the approval/autonomy mode. Approval is consumed only on acknowledged delivery and is invalidated by a payment or dispute. Contact history/count belongs to the obligation, so a new payment request cannot reset its limit. Scoped escalation leaves other obligations eligible.

Gemini writes the sentence. It uses a literal `{{remaining}}` token; code inserts the verified amount. Optional debtor/creditor/expense/component tokens preserve exact supplied names and descriptions. Numeric amounts, dates, invented links, unrelated participant names and obvious aggressive wording are rejected in the draft. No hard-coded reminder sentence replaces Gemini. The shared prompt also prohibits unsupported claims, guilt, threats and unnecessary private context; concise audit text and actual generated messages remain inspectable.

`MessagingConnector.send_message(episode, recipient, message, obligation_id)` separates external delivery from reasoning. Wizard records `SIMULATED_DELIVERY` or `MESSAGE_READY`, never real WhatsApp delivery. A queued MESSAGE_READY consumes neither approval nor reminder count. Failed sends preserve approval and verified balances. A future real connector can implement the same interface.

The real clock monitor supplies time only. It wakes individually authorized due obligations, including confirmed shares that have no payment reference yet; it avoids waking for an unapproved pending question. Gemini supplies the semantic decision. Verified full settlement disables reminder eligibility immediately when the financial engine applies it, independent of whether Gemini subsequently chooses to close the episode.

Run regression/policy tests:

```powershell
cd C:\Users\Intern\Documents\Ken
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm.cmd run build
```

Run a fresh real Gemini fixture through the same APIs as the UI (requires configured backend Gemini credentials and available quota):

```powershell
cd C:\Users\Intern\Documents\Ken
.\.venv\Scripts\python.exe -m scripts.live_settlement --persist
```

If the final payment is already received but waiting on the provider, resume only that saved test:

```powershell
.\.venv\Scripts\python.exe -m scripts.live_settlement --finish
```

Start the backend with the built frontend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

For frontend development, in a second terminal:

```powershell
cd C:\Users\Intern\Documents\Ken\frontend
npm.cmd run dev
```

The successful real demonstration is saved as **episode_24cb7e6b39aa**, titled **Dinner follow-up demonstration**. Open http://127.0.0.1:8000/expense/episode_24cb7e6b39aa or its simulation view to inspect the closed ledger and full audit. `docs/gemini-settlement.json` retains all staged states, real Gemini response metadata, a temporary HTTP 429, the rejected reference-resolution call, and the subsequent successful matching/closure. Earlier failed fixture attempts remain in `gemini-settlement-attempt1.json` and `gemini-settlement-attempt2.json`; they are not presented as successful runs.

## Verified result

Final regression run: **111 tests passed**. TypeScript/Vite production build passed. Desktop and 390px mobile controls were inspected in the running UI. The original `frontend/src/styles.css` hash remains `797F2EA7668EFA428E08EFF3205F5A03CDDE4A830BB18C049C2951DFA4CFAFC2`.

The real Gemini fixture completed after preserving and safely retrying rejected calls and a temporary quota failure. This was not an uninterrupted first-attempt success. The audit shows why rejected actions changed no balances or contact counts.

| Accepted action | Gemini model | Real response ID | Actual result |
|---|---|---|---|
| First reminder | gemini-3.5-flash-lite | p1DBapeVL5Wzg8UPltbm0Ak | INR 700.00, after Afifah approved; SIMULATED_DELIVERY |
| Remaining-balance reminder | gemini-3.5-flash-lite | r1DBao-_JKbRg8UPlfaGsQo | INR 300.00, after verified INR 400.00 payment; SIMULATED_DELIVERY |
| Final payment match | gemini-3.5-flash-lite | f1HBauzUMeO-g8UPlrS4kQ8 | INR 300.00 applied; remaining zero |
| Episode closure | gemini-3.5-flash-lite | gVHBaou2Jsyrg8UPt_jRkQU | CLOSED; monitoring stopped |

The [focused settlement audit](gemini-settlement-audit.json) includes generated tool arguments, rendered messages, human permissions, real provider metadata, financial observations and the rejected calls. The [full staged report](gemini-settlement.json) preserves the complete run. Screenshots: [settings](follow-up-settings.png), [mobile settings](follow-up-mobile.png), [waiver confirmation](follow-up-waiver.png), [settled share](follow-up-settled.png).
