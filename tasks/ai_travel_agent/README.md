# AI Smart Travel Planner & Booking Assistant

A tour-booking app for a fictional travel agency, *Praveen's Travels*. It has a
REST API, a web UI, and one chatbot that:

- answers general travel questions (itineraries, packing, best season),
- answers policy questions from indexed documents, with page citations (RAG),
- reads live packages and your bookings, and proposes bookings and
  cancellations through a single tool-calling agent. **Nothing is booked or
  cancelled until you press Confirm.**

Conversations, tool calls, proposals, bookings and documents are all stored in
MySQL, so everything survives a restart.

## Demo accounts (fictional, local only)

| Username | Password  | Role      |
|----------|-----------|-----------|
| admin    | Admin@123 | Admin     |
| alice    | Alice@123 | Traveller |
| bob      | Bob@123   | Traveller |

## Setup

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), MySQL 8, and an API
key for a language model (or a local Ollama).

The project uses **uv** instead of `pip` and `requirements.txt`. Dependencies
are declared in `pyproject.toml` and pinned in the workspace's `uv.lock`
(`genai-b3/uv.lock`).

```bash
# From the workspace root (genai-b3/). Always use --all-packages: a plain
# `uv sync` (or `uv add`) inside a member folder empties the shared virtual
# environment of the other projects' packages.
uv sync --all-packages

cd tasks/ai_travel_agent
cp .env.example .env          # then set DATABASE_URL and OPENAI_API_KEY

mysql -u root -p -e "CREATE DATABASE travel_ai CHARACTER SET utf8mb4;"
uv run alembic upgrade head   # create the tables
uv run travel-seed            # demo packages, accounts and bookings (idempotent)
uv run travel-index           # index knowledge_base/ for policy answers (idempotent)
uv run travel-api             # http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000> for the web UI and <http://127.0.0.1:8000/docs>
for Swagger. Browsing and booking work without an AI key; only the chatbot and
document upload then return `503 AI_NOT_CONFIGURED`.

Package departure dates are set relative to the day you run `travel-seed`, so a
fresh database always has bookable trips covering every refund tier.

To add a dependency: `uv add <package>` in this folder, then
`uv sync --all-packages` from the workspace root.

## Choosing the model

The chatbot talks to any **OpenAI-compatible Chat Completions API that supports
tool calling**. Set it in `.env`:

| Provider | Settings |
|----------|----------|
| OpenAI (default) | `OPENAI_API_KEY=sk-...`, `OPENAI_MODEL=gpt-4o-mini` (or `gpt-4.1-mini`) |
| Ollama (local, free) | `OPENAI_BASE_URL=http://localhost:11434/v1`, `OPENAI_MODEL=llama3.2` |
| Groq, OpenRouter, ... | `OPENAI_BASE_URL=<provider>/v1`, `OPENAI_API_KEY=<key>`, `OPENAI_MODEL=<model>` |

Embeddings use the same provider unless you set `EMBEDDING_API_KEY` /
`EMBEDDING_BASE_URL`, which keeps OpenAI embeddings while chatting with another
model. If you change the embedding model, delete `data/chroma` and run
`travel-index` again, because vectors from different models can't be compared.

Small local models follow tool instructions less reliably. The safeguards below
hold no matter which model is used.

## How the chatbot stays safe

- **Human in the loop.** `create_booking` and `cancel_booking` never write. They
  read live prices and seats and store a *pending action* with the exact terms
  (package, departure, travellers, unit price and total, or the refund amount
  and percentage), plus a 15-minute expiry. The UI shows it as a card with
  Confirm and Reject buttons.
- **Exactly once.** Confirming locks the action (`SELECT ... FOR UPDATE`), moves
  it to PROCESSING, rechecks the price (or the refund quote), then books through
  the REST API with the action's own idempotency key. If the terms changed in
  the meantime the action fails with `TERMS_CHANGED` and nothing is written.
  Confirming twice, or retrying after a timeout, returns the same booking.
- **No guessed packages.** Each booking line must name the package, and the tool
  rejects a `package_id` that belongs to a different package.
- **One refund rule.** The cancellation tiers live in one pure module
  (`services/cancellation_policy.py`): 100% at least 7 days before departure,
  50% from 2 to 6 days, nothing within 2 days, and no cancellation on or after
  departure. An operator cancellation refunds 100%. The API returns the quote
  with every cancellable booking, so the agent, the UI and the policy PDF never
  disagree.
- **The same rules as the UI.** The agent's tools call this app's own REST API as
  the signed-in user, using a random per-process token. The model never sees
  credentials, URLs or other users' data; another user's booking or proposal
  returns 404.
- **Grounded policies.** Only ACTIVE document versions are searched, and only
  chunks above `MIN_RELEVANCE`. With no evidence, the assistant says it can't
  verify the policy. Citations list only the document pages the answer actually
  cites (e.g. `[Cancellation_Policy.pdf p.1]`). Document text is passed to the
  model as data, never as instructions.
- **Failures are recorded.** Your message is saved before the model is called.
  If the model or the vector store fails, the reply is stored as a failed
  message and the API returns 503. Chat is limited to
  `CHAT_RATE_LIMIT_PER_MINUTE` messages per user (429 above that).

Each reply has a mode: `GENERAL` (no tools), `RAG` (policy only), `AGENT`
(business tools only) or `MIXED` (both).

## API overview

| Method | Path | Who |
|--------|------|-----|
| GET | `/health` | anyone |
| GET | `/api/auth/me` | signed in |
| GET | `/api/packages` (`destination`, `max_price`, `duration_days`, `start_date`, `keyword`), `/api/packages/{id}` | anyone |
| POST, PUT, DELETE | `/api/packages[/{id}]` (DELETE disables) | admin |
| POST | `/api/bookings` (`Idempotency-Key` header) | traveller |
| GET | `/api/bookings`, `/api/bookings/{id}` | owner (admin may read) |
| POST | `/api/bookings/{id}/cancel` | owner |
| GET, PATCH | `/api/admin/bookings`, `/api/admin/bookings/{id}/status` | admin |
| POST, GET | `/api/documents` | admin |
| POST, GET | `/api/chat/sessions` | signed in |
| POST, GET | `/api/chat/sessions/{id}/messages` | session owner |
| POST | `/api/chat/actions/{id}/confirm` `{"decision": "CONFIRM" \| "REJECT"}` | action owner |

Booking lines accept the case study's field name too:
`{"lines": [{"item_id": 1, "quantity": 2}]}` is the same as `package_id`.
Quantity is the number of travellers (1–10). Repeating a booking request with the
same `Idempotency-Key` returns the original booking with `200` and
`Idempotent-Replayed: true`; the same key with a different body is `409`.

All requests use HTTP Basic auth. Every error has the body
`{"code", "message", "request_id"}`, and the `X-Request-ID` header matches it.
Codes: 401, 403, 404 (also used for "not yours"), 409 (seats, departed trip,
status change, key reuse), 413, 415 and 422 for uploads and validation, 429 for
the rate limit, and 503 when an AI dependency is down.

## Demo script

1. Browse packages signed out; filter by destination chip, days and budget.
2. Sign in as `alice`, book **Kashmir Explorer** for 2 travellers. Watch the
   seats drop, then see the booking and its refund quote under **My bookings**.
3. Open **Ask the assistant** and try the case study's prompts:
   - "Suggest a five-day Kashmir itinerary." → `GENERAL`
   - "What is the cancellation policy?" → `RAG` with page-cited source chips
   - "Find Kashmir packages under INR 25000." → `AGENT`, live package data
   - "Find a five-day Kashmir package below INR 25000, explain cancellation and
     book it." → `MIXED`; a proposal card appears and no booking exists yet.
     Press Confirm and booking #N appears under **My bookings**.
   - "Cancel booking N" → a card showing the refund → Confirm
4. Sign in as `admin`: change a price, mark a booking COMPLETED, and upload a new
   version of a policy file under **Policy documents**.
5. Restart `travel-api`. Chat history, proposals and bookings are all still there.

## Configuration

See `.env.example`; every key is documented there. Only `DATABASE_URL` is
required. Notable keys: `TOP_K`, `MIN_RELEVANCE`, `CHUNK_SIZE` and
`CHUNK_OVERLAP` (RAG); `AGENT_MAX_ITERATIONS`, `PENDING_ACTION_TTL_MINUTES` and
`CHAT_CONTEXT_TURNS` (agent). Leave `INTERNAL_API_BASE_URL` empty; it then
follows `API_PORT`.

The starter documents in `knowledge_base/` are fictional and generated by
`uv run python scripts/make_knowledge_base.py` (deterministic output).

## Project layout

```
src/ai_travel_agent/
  main.py, container.py, cli.py     app factory, composition root, console commands
  seed.py                           demo packages, accounts and bookings
  config/        settings (.env), database engine and sessions, logging
  models/        SQLAlchemy tables      migrations/ Alembic revisions
  schemas/       request/response models
  repositories/  queries                services/  business rules and transactions
  controllers/   FastAPI routers        security/  Basic auth, Argon2, agent identity
  llm/           OpenAI-compatible client and error mapping
  rag/           loader (PDF/DOCX/TXT), chunker, embeddings, Chroma store, retriever
  agents/        prompt, tools, tool loop, internal API client
  frontend/      index.html, style.css, script.js (no build step)
knowledge_base/  starter policy documents (fictional)
scripts/         knowledge-base generator
tests/           mirrors src/; api/ holds the MySQL integration tests
```

## Testing

```bash
uv run pytest           # unit tests always; MySQL tests when TEST_DATABASE_URL is set
uv run ruff check . && uv run ruff format --check .
```

- **Unit tests** need no services: cancellation tiers, schema limits, loader and
  chunker, citations, tools and the agent loop with a scripted fake model
  (general, mixed, prompt injection, round cap, outage).
- **Integration tests** (`tests/api`, marker `mysql`) run the whole app with
  FastAPI's `TestClient` against a real MySQL database. Only the language model
  and embeddings are faked; the agent's tools call the real REST endpoints.
  They cover the case study's test table: CRUD, validation with no database
  change, 401/403/404, insufficient seats with no partial write, idempotent
  replay, refund tiers, two threads racing for the last seat, confirm twice,
  reject and expiry, price change before confirm, AI and vector-store failures,
  restart continuity, document versioning, and that the migration matches the
  models.

The integration tests need a **separate, empty** database, because they drop and
recreate its tables:

```bash
mysql -u root -p -e "CREATE DATABASE travel_ai_test CHARACTER SET utf8mb4;"
# then in .env:
# TEST_DATABASE_URL=mysql+pymysql://<user>:<password>@127.0.0.1:3306/travel_ai_test
```

Without `TEST_DATABASE_URL` they are skipped with a clear reason.
