# Actual Gemini verification

**69 local tests passed; production frontend build passed; 2/2 live B/C scenarios passed.** The approved base stylesheet is unchanged.

The initial minimal Gemini smoke test passed using Gemini 3.8 Flash (`docs/gemini-smoke.json`). Repeated service-unavailable responses during subsequent testing led to selecting stable, free-tier **gemini-3.5-flash-lite** for this application. Both final reasoning scenarios below used that selected model.

The final B/C inputs have identical title, bill, payer, participants, currency, category and preference. Only `initial_context` differs. Neither input includes scenario labels or an expected next action. Random database IDs differ normally. Both use the same provider-independent prompt and canonical Pydantic decisions. Production reasoning contains no drinks/test-participant conditionals.

| Case | Actual accepted function | Actual returned model | Google response ID |
| --- | --- | --- | --- |
| B | `reconstruct_context` | `gemini-3.5-flash-lite` | `wTfBaq7lEIijqfkPpIuf6QQ` |
| B | `request_clarification` | `gemini-3.5-flash-lite` | `xDfBaqDpAqurg8UPxJ_wsAo` |
| C | `reconstruct_context` | `gemini-3.5-flash-lite` | `xjfBauOZBKzSqfkPpuz2kAQ` |
| C | `propose_allocation` | `gemini-3.5-flash-lite` | `yDfBav3vEsHbg8UPpvqm6Ak` |
| C | `wait` | `gemini-3.5-flash-lite` | `yjfBapPwEMO0g8UPgdCdkAo` |

B evidence: “Riya and I had veg. Arjun and Kabir had chicken. Kabir had dessert.”

B result: **Who had the drinks?**. Known veg/chicken/dessert context was preserved. Drinks consumers remain unset. No allocation/obligation was created from missing information.

C evidence: “Riya and I had veg. Arjun and Kabir had chicken. Kabir had dessert. Arjun and Kabir shared the drinks.”

C result: no clarification. Deterministic code computed Afifah ₹300, Riya ₹300, Arjun ₹700 and Kabir ₹1,100, conserving ₹2,400. Every obligation is UNCONFIRMED; no settlement request exists. Gemini chose WAIT for confirmations after proposing the allocation.

B console: http://127.0.0.1:8000/simulation?episode=episode_e5e7c5b72716

C console: http://127.0.0.1:8000/simulation?episode=episode_a5e3c5fe41bc

Both runs used system-prompt SHA-256 `f7b18fbe73186ee10fe55c062c9bf3d5980962ad522325df6f064896302ca655`. Their input hashes differ, and the full JSON report contains exact evidence, structured tool requests/observations, API response IDs, function-call IDs and token metadata. No private thought text or credentials are stored.

All decisions in these final runs were accepted on their first attempt. Earlier development attempts remain in separate attempt reports and persisted audit episodes; policy rejected invalid argument maps/amounts without committing them. The final Gemini transport adapter removes schema defaults, declares participant-name map keys, and describes known consumers, relative weights and exact currency-unit agreements. It never supplies consumers, weights or monetary values. The shared prompt, decision validator, tools and financial calculations remain provider-independent.

Free-tier service availability and model outputs can vary; the agent preserves its accepted ledger on API/validation failures. These tests establish the observed outcomes rather than guaranteeing every future model response.

Run the live comparison again with `python -m scripts.live_eval --bc --persist`. To retry only failed cases in the last report, add `--resume`. [Exact startup, environment and browser steps](agent-audit.md).
