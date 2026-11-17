# Employee Management

FastAPI + MySQL employee CRUD with two ways in:

- **Form UI** (`index.html`, plain HTML/CSS/JS): add, edit, delete and search employees.
- **Chatbot**: natural-language requests ("raise Zed Beta's salary by 10%") that an
  OpenAI model carries out by calling the same CRUD functions as tools.

Everything lives in `main.py` (schemas, MySQL repository, chatbot tools, HTTP API)
and `index.html`.

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

`id`, `name`, `email` (unique, case-insensitive), `department`, `position`,
`salary` (non-negative number) and an optional `hire_date` (YYYY-MM-DD).
Requests with any other field are rejected with 422, so no change can be
silently dropped.

## API

| Method | Path                     | Purpose                                   |
|--------|--------------------------|-------------------------------------------|
| GET    | `/api/employees?search=` | List, or search name/email/dept/position  |
| GET    | `/api/employees/{id}`    | Get one (404 if missing)                  |
| POST   | `/api/employees`         | Create (409 on duplicate email)           |
| PATCH  | `/api/employees/{id}`    | Update only the fields sent               |
| DELETE | `/api/employees/{id}`    | Delete (204, or 404 if missing)           |
| POST   | `/api/chat`              | Chatbot: `{"messages": [{"role", "content"}]}` |

## Chatbot

The browser keeps the conversation and sends it with each message, so follow-ups
like "yes, delete her" work. The server runs a tool-calling loop (OpenAI Responses
API, at most 6 rounds) over five tools: `list_employees`, `get_employee`,
`create_employee`, `update_employee` and `delete_employee`.

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

- Chat history is supplied by the browser and isn't verified by the server. This
  is fine for a local demo app, but don't expose it publicly without auth.
- There's no authentication or pagination.
