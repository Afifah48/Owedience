# Owedience agent audit and Gemini setup

## Audit finding

**YES: a real LLM was already integrated before this change.** The existing provider protocol was `decide(state, observation)`, with OpenAI Responses and compatible Chat Completions implementations. No replacement of the loop was necessary. The earlier OpenAI live attempts returned HTTP 429; those attempts did not establish semantic correctness.

Gemini was added at the provider/composition boundary. `GeminiProvider` calls Google's official `generateContent` REST API. The factory selects the adapter; the loop, tools, policy, financial engine, repository and consumer form do not branch on provider identity. There is no LLM fallback to Wizard, rules or test doubles.

| Responsibility | Exact file or endpoint |
| --- | --- |
| Real provider calls and selection | `backend/agent/provider.py` — `GeminiProvider.decide`, `HTTPProvider.decide`, `create_provider` |
| Provider-independent prompt and rules | `backend/agent/system_prompt.py` |
| Shared structured decisions and function schemas | `backend/agent/decision_schema.py` |
| Agent re-evaluation loop | `backend/agent/engine.py` |
| Policy validation and tool execution | `backend/agent/tools.py` |
| Deterministic minor-unit financial calculations | `backend/services/finance.py` |
| UI submission | `frontend/src/components/NewExpense.tsx` — `save` |
| Frontend API callback | `frontend/src/main.tsx` — `post('/episodes', body)` |
| Expense creation endpoint | `POST /api/episodes` — `backend/main.py` |
| Natural clarification answer endpoint | `POST /api/episodes/{id}/messages` |
| Audit inspection | `GET /api/simulation/episodes/{id}` and `/audit?format=json` |

## Model and setup

The selected model is **`gemini-3.5-flash-lite`**. Google's current model documentation lists it as stable with function calling and structured outputs, optimized for low-latency agent tasks and extraction. The pricing page lists free input and output tokens for the standard developer tier. Quota/access are project-specific. The older 2.5 models are currently limited to users with previous usage, so they are not the default for a new project.

Verified official sources:

- https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
- https://ai.google.dev/gemini-api/docs/latest-model
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/api-key
- https://ai.google.dev/api/generate-content

Create a key in **https://aistudio.google.com/apikey**. Keep it only in local `.env`; `.env` is gitignored. Both API keys in `.env.example` are blank. No OpenAI billing or key is required when Gemini is selected.

Use this local `.env` (replace only the placeholder with your private key):

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=YOUR_LOCAL_GEMINI_KEY
LLM_MODEL=gemini-3.5-flash-lite
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
DATABASE_PATH=data/owedience.sqlite3
CONNECTOR_MODE=wizard
SIMULATION_TOKEN=<your-local-simulation-token>
MONITOR_ENABLED=false
```

`LLM_BASE_URL` is used only by the OpenAI/compatible adapter; Gemini always uses Google's documented endpoint. Wizard mode applies only to external-world rails. `MONITOR_ENABLED=false` makes manual testing predictable; enable it to resume the existing clock monitor. Restart the backend after editing credentials/model.

From `C:\Users\Intern\Documents\Ken`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell terminal:

```powershell
Set-Location C:\Users\Intern\Documents\Ken\frontend
npm.cmd run dev
```

Open http://127.0.0.1:5173 (development) or http://127.0.0.1:8000 (existing built frontend).

## Browser proof: the same bill, different evidence

1. Set **Viewing as** to Afifah. Click **New expense**.
2. Choose **Food & Dining**, total **2400**, four people **Afifah, Riya, Arjun, Kabir**, payer **Afifah**. Leave the preference at **Let Owedience figure it out from the context**.
3. Expand **Add bill items** and enter Veg **600**, Chicken **800**, Dessert **400**, Drinks **600**.
4. Test B context: **Riya and I had veg. Arjun and Kabir had chicken. Kabir had dessert.** Submit **Let Owedience sort it**. Inspect the actual model-selected clarification.
5. Create Test C with exactly the same bill/people. Add **Arjun and Kabir shared the drinks.** to the context. Submit and inspect whether the model proceeds without a drinks question.
6. In Test B, answer the question naturally. The message endpoint reruns the same loop, retaining prior supported context.
7. Open **Simulation console**, enter the `SIMULATION_TOKEN` from `.env`, and select each test expense. **External world** shows the differing asserted evidence. **Agent activity** shows each concise decision, policy IDs, accepted/rejected result, and exact tool request/observation.
8. Expand **Input / tool request / observation**. Its `provider` object records `provider`, requested/returned model, API `response_id` when supplied, token counts, and SHA-256 hashes of input and prompt. The common prompt hash demonstrates the same policy/prompt; inspect actual evidence to understand the input difference. Missing-key/provider-error entries are failures, not evidence of a successful model decision.
9. Export **Audit JSON** for full entries. Private model thoughts, API headers and credentials are not retained.

Expected outcomes are test assertions, **not production branches**: B preserves the known item facts and asks only about Drinks; C proposes supported consumption shares without asking who drank. The expected complete totals are Afifah ₹300, Riya ₹300, Arjun ₹700, Kabir ₹1,100. These are unconfirmed proposals; no settlement starts before debtor confirmation.

## Repeatable verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.gemini_smoke
.\.venv\Scripts\python.exe -m scripts.live_eval --bc --persist
```

Run B/C only after the smoke test succeeds. The smoke test makes one real provider call, receives an independently selected structured decision, and writes `docs/gemini-smoke.json`. B/C use the same expense-creation POST endpoint as the UI and write `docs/gemini-reasoning.json`. `--persist` keeps the episodes available in the app/console; omit it to use a temporary test database. Without a configured key these scripts report NOT RUN, never simulated success.

The broader A–E suite remains available with `python -m scripts.live_eval`; it also checks answering B, reviewing a conflicting rough equal proposal, and preserving a fixed cab agreement while asking about unknown remainder sharing. No scenario-supplied action sequence enters the production provider.

## Earlier requested input additions

The existing modal now accepts all eleven categories, optional receipt and audio files, optional bill items, participant count/names, payer, starting split preference, rough proposal, context, optional item assignments and extra context. All create requests invoke the agent, including no-text and voice-only submissions. Inputs become asserted evidence; no consumers/shares are populated by the form. Rough shares are `USER_PROPOSED_SPLIT` evidence and cannot prove consumption. Receipt bytes are preserved locally but not parsed; audio needs a reviewed external transcription before it supports allocation. Bill mismatches reach the agent for clarification rather than receiving invented adjustments.

Exact fixed shares and stated percentages are handled by generic tools; server code calculates the remaining amount and rounding. The existing per-person breakdown includes **Why this amount?**. Approved base CSS is unchanged: SHA-256 `797F2EA7668EFA428E08EFF3205F5A03CDDE4A830BB18C049C2951DFA4CFAFC2`.
