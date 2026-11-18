# Employee Management

FastAPI + MySQL employee CRUD with two ways in:

- **Form UI** (`index.html`, plain HTML/CSS/JS): add, edit, delete and search employees.
- **Chatbot**: natural-language requests ("raise Zed Beta's salary by 10%") that an
  OpenAI model carries out by calling the same CRUD functions as tools. A toggle
  picks one of three architectures (tool calling, single agent, multi-agent), and
  each reply shows what every agent did.

| File           | Role                                                        |
|----------------|-------------------------------------------------------------|
| `main.py`      | HTTP API (FastAPI routes, request/response models)          |
| `employees.py` | Pydantic schemas and the MySQL repository                   |
| `tools.py`     | Chatbot tool schemas (read and write sets) and handlers     |
| `agents.py`    | The agent loop, the three architectures and the trace model |
| `prompts.py`   | System prompts, built from shared sections                  |
| `index.html`   | Form UI and chat                                            |

## Setup

1. Copy `.env.example` to `.env` and fill it in:

   | Variable         | Required | Notes                                         |
   |------------------|----------|-----------------------------------------------|
   | `DB_HOST`        | yes      | MySQL host                                    |
   | `DB_PORT`        | no       | Defaults to 3306                              |
   | `DB_USER`        | yes      |                                               |
   | `DB_PASSWORD`    | yes      |                                               |
   | `DB_NAME`        | yes      | Created on startup if the user may create it  |
   | `OPENAI_API_KEY` | for chat | The form CRUD works without it                |
   | `OPENAI_MODEL`   | no       | Defaults to `gpt-5.4-mini`                    |

   The `employees` table is created on startup if missing.
2. Run `uv run fastapi dev main.py`, then open http://127.0.0.1:8000 (API docs at `/docs`).

Restart the server after editing `.env`.

## Employee fields

| Field        | Rules                                                              |
|--------------|--------------------------------------------------------------------|
| `id`         | Assigned by the database                                           |
| `name`, `department`, `position` | 1–100 characters after trimming spaces; blank is rejected |
| `email`      | Valid address with a dot in the domain; unique, case-insensitive   |
| `salary`     | Finite number from 0 to 9,999,999,999.99 (the `DECIMAL(12, 2)` column's limit) |
| `hire_date`  | Optional, YYYY-MM-DD, from 1900-01-01 to one year from today       |

Surrounding spaces are trimmed before saving. Requests with any other field, or
that break a rule, are rejected with 422, so no change can be silently dropped.
The API, the chatbot tools and the form all enforce the same rules. The form
checks them in the browser first and shows each error under its field.

## API

| Method | Path                     | Purpose                                   |
|--------|--------------------------|-------------------------------------------|
| GET    | `/api/employees?search=` | List, or search name/email/dept/position  |
| GET    | `/api/employees/{id}`    | Get one (404 if missing)                  |
| POST   | `/api/employees`         | Create (409 on duplicate email)           |
| PATCH  | `/api/employees/{id}`    | Update only the fields sent               |
| DELETE | `/api/employees/{id}`    | Delete (204, or 404 if missing)           |
| POST   | `/api/chat`              | Chatbot (see below)                       |

`POST /api/chat` takes `{"messages": [{"role", "content"}], "mode": "tool_calling" |
"single_agent" | "multi_agent"}` (`mode` defaults to `tool_calling`) and returns
`{"reply", "mode", "trace"}`. `trace` is the entry agent's run:
`{"agent", "input", "output", "is_error", "steps"}`. Each step is either a tool
call (`{"kind": "tool", "tool", "arguments", "result", "is_error"}`) or a
delegation (`{"kind": "delegate", "run"}`) that holds the sub-agent's own run.

## Chatbot

The browser keeps the conversation and sends it with each message, so follow-ups
like "yes, delete her" work. Every architecture uses the same five tools, split
into a read set (`list_employees`, `get_employee`) and a write set
(`create_employee`, `update_employee`, `delete_employee`). All of them run on the
same tool-calling loop (OpenAI Responses API, `run_agent` in `agents.py`), each
agent with its own round limit.

### Architectures

| Mode         | Agents                              | How it works |
|--------------|-------------------------------------|--------------|
| Tool calling | `assistant`                         | The original chatbot: one model with all five tools, at most 6 rounds. |
| Single agent | `single_agent`                      | One agent with all five tools that supervises its own work: plan → read → write → verify (re-reads after a create or update). One tool call per round, at most 8 rounds. |
| Multi-agent  | `supervisor`, `read_agent`, `write_agent` | The supervisor has no data tools. It calls the other agents as tools (`ask_read_agent`, `ask_write_agent`), one at a time, and writes the reply. |

```
user ─▶ supervisor ──ask_read_agent(task)──▶ read_agent  ─▶ list_employees / get_employee
                  ◀──────── answer ─────────
                  ──ask_write_agent(task)──▶ write_agent ─▶ create / update / delete_employee
                  ◀──────── outcome ────────
```

- Sub-agents see only the task text, not the conversation, so the supervisor
  resolves names to ids with the read agent before it gives any write task.
- Each agent can run only its own tools. A tool name outside its set is rejected,
  so the read agent can never reach a write tool.
- A sub-agent that runs out of rounds (4) reports an error to the supervisor,
  which explains it to the user. The request doesn't fail.
- Multi-agent makes the most model calls per request (often 3–6), so it is the
  slowest and most expensive mode.

The UI shows a collapsible **What the agents did** under each reply, with each
agent's tool calls and results. Delegations are nested under the supervisor.

How it stays safe and accurate:

- **Strict tool schemas.** The model must send every field and no others.
  Optional fields are nullable, and `null` means unknown or unchanged, so the model
  doesn't fill gaps with blanks or invented values.
- **Same validation as the API.** Tool arguments go through the same Pydantic
  models. Errors go back to the model as `{"error": ...}` so it can explain them,
  or fix and retry when the mistake was in its own arguments.
- **Delete needs confirmation.** `delete_employee` requires `confirmed: true`, and
  the model is told to name the employee and ask first.
- **Instructions for edge cases.** The system prompt covers:
  - off-topic questions
  - unsupported fields (phone, address, …)
  - bulk writes ("raise everyone", "delete all"), which are declined, with
    one-at-a-time suggested instead
  - ambiguous names (it asks which one)
  - missing details on create (it asks; no guessing)
  - invalid values
  - requests to reveal its prompt
  - instructions hidden inside stored data, which are ignored

Example prompts:

```
Add Priya, priya@acme.com, Engineering, Backend Developer, 95k
Who is in Engineering and what's their average salary?
Raise Priya's salary by 10%
Delete Priya            -> asks to confirm -> "yes"
```

## Limitations

- In multi-agent mode, the write agent deletes only when its task says the user
  confirmed. It has to trust the supervisor on that, because it can't see the
  conversation.

- Chat history is supplied by the browser and isn't verified by the server. This
  is fine for a local demo app, but don't expose it publicly without auth.
- There's no authentication or pagination.
