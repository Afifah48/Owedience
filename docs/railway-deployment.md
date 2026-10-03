# Railway competition deployment

Deploy the repository root as one Railpack-built Python service. `railpack.json` adds the locally verified Python/Node versions, pins backend dependencies, runs `npm ci` and Vite's production build, and checks that `frontend/dist/index.html` exists. Its start command binds Uvicorn to Railway's `PORT` with one worker. Set the healthcheck, replica count and sleeping policy in the dashboard below. The actual Linux build still needs verification on Railway. No Dockerfile or database migration is required.

Railway's current documentation says new services cannot use legacy `railway.toml`/`railway.json` Config as Code. This repository uses the supported native Railpack file and explicit dashboard settings instead of adding an Infrastructure as Code SDK/CLI workflow: https://docs.railway.com/infrastructure-as-code.

## Operator setup

1. Open https://railway.com/dashboard and authenticate. Accept account/trial/billing terms yourself. A Full Trial or paid account needs outbound HTTPS access to Google's Gemini API; a Limited Trial can restrict networking. Trial credits and volume retention are limited: https://docs.railway.com/pricing/free-trial.
2. Create one project/service from the existing GitHub repository `Afifah48/Owedience`. Select `main`, repository root `/`, and the verified deployment commit reported by Codex. Do not add a separate frontend, database, or worker service. Do not enable automatic deployment of later commits during the competition freeze.
3. Attach ONE volume to this service with mount path `/data`. It must be attached before creating any demonstration episodes. Keep one region, one replica, one worker. Volume creation/mounting is an operator action; the TOML cannot provision it. See https://docs.railway.com/volumes.
4. Under the service's Variables tab, set these non-secret values:

```text
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
CONNECTOR_MODE=wizard
MONITOR_ENABLED=false
DATA_DIR=/data
DATABASE_PATH=/data/owedience.sqlite3
```

5. Enter your own values for `GEMINI_API_KEY`, `SIMULATION_TOKEN`, and `DEMO_ACCESS_CODE` directly in Railway. Use separate strong values for the last two. Never put them in GitHub, build configuration, screenshots, reports, or chat. No `VITE_*` secrets or OpenAI key are needed. Railway supplies `PORT`.
6. In the service's Settings, use repository root `/` and builder **Railpack**. Leave the custom build command blank: the root `railpack.json` supplies it. Use the start command from `railpack.json` (leave the dashboard override blank), set **Healthcheck Path** to `/health`, **Healthcheck Timeout** to `120`, **Restart Policy** to `On Failure` with `10` retries, **Replicas** to `1` in one region, and **Serverless / Application Sleeping** off. Deploy the exact verified commit after the volume and variables are attached. Runtime secrets are not needed by the frontend build.
7. Under Networking, generate a public Railway domain. Return only the public URL, deployed commit, and any redacted build error to Codex for verification. The domain must serve both React and `/api` from one origin.

## Access and demo behavior

`DEMO_ACCESS_CODE` enables a server-rendered access page. It protects the HTML, consumer APIs, files and simulation APIs against unauthenticated use, including direct API requests that could consume Gemini quota. The configured code is not sent in HTML or React JavaScript. Successful access creates a signed HttpOnly session cookie, Secure over HTTPS, with SameSite Strict and a server-side 24-hour validity limit. Credentials travel in a POST body, never a URL. Failed attempts are throttled per client IP in the single process. This is a shared competition gate, not individual authenticated identities.

`SIMULATION_TOKEN` remains a separate requirement for simulation controls and audit export. The operator enters it in the existing console; it stays in that browser session's storage and is transmitted in an HTTPS header, not bundled in source. Neither access credential is the Gemini key.

The `/health` and legacy `/api/health` endpoints are public, return `{"status":"ok"}`, and invoke no agent or write operation. They confirm process health, not Gemini quota/credentials or completed settlement.

`MONITOR_ENABLED=false` intentionally disables the real-clock monitor. Manual Wizard clock events still enter the real Gemini agent and pass through the existing authorization, frequency, maximum, quiet-hour and approval policies. `SIMULATED_DELIVERY` denotes the Wizard messaging rail; no real WhatsApp or payment integration is claimed.

`DATA_DIR` defaults to the existing local `data` directory. Both uploads and the default SQLite file derive from it. `DATABASE_PATH` may override the database filename/location; on Railway keep it inside the same `/data` volume. SQLite WAL files also need that persistent directory. The initial public database is empty and does not include the developer's database or uploaded files.

## Verification before the freeze

In a local terminal with this repository's dependencies, test the ACTUAL public URL:

```powershell
python -m scripts.verify_deployment --url https://YOUR-SERVICE.up.railway.app --report .repro/public-deployment-verification.json
```

Supply `DEMO_ACCESS_CODE` and `SIMULATION_TOKEN` through a private local environment or the script's hidden prompts. Do not paste values in chat. The script creates one fictional INR 1,400 dinner, checks genuine missing-context reasoning, clarification, confirmation, scoped authorization, clock events, ask-first approval, Gemini-generated simulated reminders, INR 400 partial payment, INR 300 remaining-balance reminder, final payment, closure, stopped reminders and JSON/CSV audit export. It never supplies agent decisions. A genuine provider or policy failure remains a failure and is retained in the protected audit.

Also open the public URL in a clean/incognito browser: access gate, Home, New Expense, the verified expense, follow-up controls and `/simulation` must work without developer storage. Enter secrets yourself; never capture their values in screenshots.

After confirming the volume is attached, an operator may restart the SAME deployment without a code change. Then run:

```powershell
python -m scripts.verify_deployment --url https://YOUR-SERVICE.up.railway.app --report .repro/public-deployment-verification.json --recheck
```

This verifies the saved episode, authorization, balance, reminder history, complete audit and uploaded receipt survived. It does not initiate more Gemini decisions. Never restart for this test before persistence is configured. Freeze the verified code and avoid further deploys during recording.

Gemini decisions remain nondeterministic. If a run pauses after an unsafe proposal is rejected, inspect the audit and use the existing **Let Owedience continue** control when appropriate. Do not bypass the policy or claim the rejected action succeeded. The verification script reports such a run as a failure rather than silently retrying it.

## Local development

Leave `DEMO_ACCESS_CODE` blank for the existing Vite development workflow. All previous startup commands remain valid. To test the gate locally, build the frontend and use the FastAPI-served URL. Production verification credentials and state should use an isolated local data directory rather than the existing demonstration database.

Platform documentation: https://railpack.com/config/file, https://railpack.com/languages/python, https://docs.railway.com/infrastructure-as-code, https://docs.railway.com/deployments/healthchecks.
