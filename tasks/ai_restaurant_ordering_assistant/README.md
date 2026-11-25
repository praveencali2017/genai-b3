# AI Smart Food Ordering & Restaurant Assistant

A food-delivery app for a fictional restaurant, *Praveen's Kitchen*. It has a
REST API, a web UI, and one chatbot that:

- answers general food questions,
- answers policy questions from indexed documents, with citations (RAG),
- reads the live menu and your orders, and proposes orders and cancellations
  through a single tool-calling agent. **Nothing is ordered or cancelled until
  you press Confirm.**

Conversations, tool calls, proposals, orders and documents are all stored in
MySQL, so everything survives a restart.

## Demo accounts (fictional, local only)

| Username | Password  | Role     |
|----------|-----------|----------|
| admin    | Admin@123 | Admin    |
| alice    | Alice@123 | Customer |
| bob      | Bob@123   | Customer |

## Setup

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), MySQL 8, and an API
key for a language model (or a local Ollama).

```bash
# From the workspace root (genai-b3/). Always use --all-packages: a plain
# `uv sync` inside a member folder empties the shared virtual environment.
uv sync --all-packages

cd tasks/ai_restaurant_ordering_assistant
cp .env.example .env          # then set DATABASE_URL and OPENAI_API_KEY

mysql -u root -p -e "CREATE DATABASE food_ai CHARACTER SET utf8mb4;"
uv run alembic upgrade head   # create the tables
uv run restaurant-seed        # demo menu, accounts and orders (idempotent)
uv run restaurant-index       # index knowledge_base/ for policy answers (idempotent)
uv run restaurant-api         # http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000> for the web UI and <http://127.0.0.1:8000/docs>
for Swagger. The shop works without an AI key; only the chatbot and document
upload then return `503 AI_NOT_CONFIGURED`.

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
`restaurant-index` again, because vectors from different models can't be compared.

Small local models follow tool instructions less reliably. The safeguards below
hold no matter which model is used.

## How the chatbot stays safe

- **Human in the loop.** `create_order` and `cancel_order` never write. They read
  live prices and stock and store a *pending action* with the exact lines, unit
  prices and total (or the cancellation impact), plus a 15-minute expiry. The UI
  shows it as a card with Confirm and Reject buttons.
- **Exactly once.** Confirming locks the action (`SELECT ... FOR UPDATE`), moves
  it to PROCESSING, rechecks the prices, then places the order through the REST
  API with the action's own idempotency key. Confirming twice, or retrying after
  a timeout, returns the same order.
- **No guessed dishes.** Each order line must name the dish, and the tool rejects
  an `item_id` that belongs to a different dish.
- **The same rules as the UI.** The agent's tools call this app's own REST API as
  the signed-in user, using a random per-process token. The model never sees
  credentials, URLs or other users' data; another user's order or proposal returns 404.
- **Grounded policies.** Only ACTIVE document versions are searched, and only
  chunks above `MIN_RELEVANCE`. With no evidence, the assistant says it can't
  verify the policy. Citations list only the sources the answer actually cites.
  Document text is passed to the model as data, never as instructions.
- **Failures are recorded.** Your message is saved before the model is called.
  If the model fails, the reply is stored as a failed message and the API
  returns 503. Chat is limited to `CHAT_RATE_LIMIT_PER_MINUTE` messages per user
  (429 above that).

Each reply has a mode: `GENERAL` (no tools), `RAG` (policy only), `AGENT`
(business tools only) or `MIXED` (both).

## API overview

| Method | Path | Who |
|--------|------|-----|
| GET | `/health` | anyone |
| GET | `/api/auth/me` | signed in |
| GET | `/api/menu_items`, `/api/menu_items/{id}` | anyone |
| POST, PUT, DELETE | `/api/menu_items[/{id}]` | admin |
| POST | `/api/orders` (`Idempotency-Key` header) | customer |
| GET | `/api/orders`, `/api/orders/{id}` | owner |
| POST | `/api/orders/{id}/cancel` | owner |
| GET, PATCH | `/api/admin/orders`, `/api/admin/orders/{id}/status` | admin |
| POST, GET | `/api/documents` | admin |
| POST, GET | `/api/chat/sessions` | signed in |
| POST, GET | `/api/chat/sessions/{id}/messages` | session owner |
| POST | `/api/chat/actions/{id}/decision` `{"decision": "CONFIRM" \| "REJECT"}` | action owner |

All requests use HTTP Basic auth. Every error has the body
`{"code", "message", "request_id"}`, and the `X-Request-ID` header matches it.
Codes: 401, 403, 404 (also used for "not yours"), 409 (stock, status, duplicate),
413, 415 and 422 for uploads, 429 for the rate limit, and 503 when an AI
dependency is down.

## Demo script

1. Browse the menu signed out; filter with **Veg only** and the price slider.
2. Sign in as `alice`, add dishes to the cart and place an order. Watch the
   stock counts drop, then see the order under **My orders**.
3. Open **Ask the assistant** and try:
   - "Suggest a light vegetarian breakfast" → `GENERAL`
   - "Can I get a refund after my order is being prepared?" → `RAG` with a source chip
   - "Show vegetarian dishes under ₹150" → `AGENT`, live menu data
   - "Does Paneer Biryani contain nuts? If not, order one" → `MIXED`; it declines, citing the allergen document
   - "Order 2 Masala Dosa" → a proposal card. No order exists yet. Press Confirm and order #N appears.
   - "Cancel my most recent order" → a cancellation card → Confirm
4. Sign in as `admin`: change a price, move an order to PREPARING, and upload a
   new version of a policy file under **Policy documents**.
5. Restart `restaurant-api`. Chat history, proposals and orders are all still there.

## Configuration

See `.env.example`; every key is documented there. Only `DATABASE_URL` is
required. Notable keys: `TOP_K`, `MIN_RELEVANCE`, `CHUNK_SIZE` and
`CHUNK_OVERLAP` (RAG); `AGENT_MAX_ITERATIONS`, `PENDING_ACTION_TTL_MINUTES` and
`CHAT_CONTEXT_TURNS` (agent). Leave `INTERNAL_API_BASE_URL` empty; it then
follows `API_PORT` and `--port`.

## Project layout

```
src/ai_restaurant_ordering_assistant/
  main.py, container.py, cli.py     app factory, composition root, console commands
  config/        settings (.env), database engine and sessions
  models/        SQLAlchemy tables      migrations/ Alembic revisions
  schemas/       request/response models
  repositories/  queries                services/  business rules and transactions
  controllers/   FastAPI routers        security/  Basic auth, Argon2, agent identity
  llm/           OpenAI-compatible client and error mapping
  rag/           loader (PDF/DOCX/TXT), chunker, embeddings, Chroma store, retriever
  agents/        prompt, tools, tool loop, internal API client
  frontend/      index.html, style.css, script.js (no build step)
knowledge_base/  starter policy documents (fictional)
```

Automated tests are not written yet. So far the app has been checked by hand with
a REST smoke script, live chat scenarios and a browser walkthrough.
