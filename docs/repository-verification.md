# Complete application publishing verification

The GitHub snapshot retains the existing React UI and complete FastAPI agent application. Publishing changes are limited to credential exclusion, Git configuration files, and local setup documentation. The existing MIT license and its original commit are preserved.

Before publication, the full regression suite passed: **111 tests**. The production frontend build passed. A separate Python virtual environment installed every package from `requirements.lock.txt`; `pip check` and the same 111 tests passed there too.

The real Gemini smoke test passed with model `gemini-3.5-flash-lite`, response ID `cF3Bau2oMYrGg8UP9NnEmQo`, and 7,651 total tokens. Its structured decision and provider receipt are in `gemini-smoke.json`. Real reasoning and settlement recordings, including unsuccessful attempts, remain in this directory for review.

The tracked snapshot includes the provider abstraction and optional OpenAI adapter, Gemini API implementation, shared system prompt/rules, decision/tool schemas, policy validation, deterministic financial engine, persistence, bounded follow-up policies, settlement and payment matching, clock monitoring, external connector abstractions, Wizard event adapters, simulation console, complete frontend, fixtures, tests and live verification scripts.

The staged source/evidence and existing history were checked against configured secret values and common credential patterns. `.env.example` has blank credential fields. `.env`, virtual environments, node modules, generated builds, local database/attachments, temporary tools, and verification checkouts are excluded. Screenshots use fictional demonstrations and mask the local console token. The local SQLite database is preserved and is not published.

Fresh installations create their own database. Wizard represents unavailable external rails; Gemini continues to perform the reasoning. Simulated delivery does not claim real WhatsApp delivery or real money movement.

Follow the root README's Windows PowerShell commands to clone, install pinned dependencies, configure local credentials, run a real smoke test, and start both servers. A separate clone from GitHub is checked after publication; its temporary credentials are supplied through process environment variables, never by committing or copying the local `.env`.
