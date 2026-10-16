# Student Management System (FastAPI)

A REST API and web UI for managing student records, backed by MySQL. It rebuilds the menu-driven [student_management.py](../../database_pgms/student_management.py) as a web app.

Each student has an **id**, **name**, **age**, **course** and **marks**. You can add a student, list all students, get one by id, update their marks or course, delete one, or delete all.

## Running

Complete the setup in the [main README](../../../README.md#getting-started) first: copy `.env.example` to `.env` and fill in your MySQL settings. This app is its own uv project, with its own `pyproject.toml`, `uv.lock` and `.venv`. Install its dependencies and start the server from this folder:

```bash
cd tasks/fastapi_crud_pgms/student_ms
uv sync
uv run fastapi dev main.py
```

| URL | What's there |
|-----|--------------|
| http://127.0.0.1:8000/ | Web UI covering every operation |
| http://127.0.0.1:8000/docs | Interactive API docs (Swagger) |

On startup the app creates the `student` table if it doesn't exist. The CLI version uses the same table, so both programs see the same rows. If `.env` is missing a setting, or MySQL can't be reached, the server fails at startup and the error says what's wrong.

## API

| Method | Path | Success | Errors |
|--------|------|---------|--------|
| `POST` | `/students` | `201` with the new student | `422` invalid fields |
| `GET` | `/students?limit=10&offset=0` | `200` with one page of students | `422` bad `limit`/`offset` |
| `GET` | `/students/{id}` | `200` with the student | `404` |
| `PATCH` | `/students/{id}` | `200` with the updated student | `404`, `422` |
| `DELETE` | `/students/{id}` | `204` | `404` |
| `DELETE` | `/students?confirm=true` | `200` with `{"deleted": n}` | `400` without `confirm=true` |

### Validation

| Field | Rule |
|-------|------|
| `name`, `course` | Required, 1–100 characters; surrounding whitespace is trimmed |
| `age` | Whole number, 1 or more |
| `marks` | Number from 0 to 100 |

`PATCH` accepts `course`, `marks`, or both. A request with neither returns `422`. Name and age can't be changed after creation, as the task specifies.

### Pagination

`GET /students` returns 10 students per page by default:

```json
{
  "items": [{"id": 4, "name": "Asha", "age": 20, "course": "Physics", "marks": 88.5}],
  "total": 34,
  "limit": 10,
  "offset": 0
}
```

`limit` can be from 1 to 100, and `offset` must be 0 or more. To get the next page, add `limit` to `offset`. If `offset + limit` is equal to or greater than `total`, this is the last page.

### Examples

```bash
curl -X POST localhost:8000/students -H 'content-type: application/json' \
     -d '{"name": "Asha", "age": 20, "course": "Physics", "marks": 88.5}'
curl 'localhost:8000/students?limit=5&offset=5'
curl -X PATCH localhost:8000/students/1 -H 'content-type: application/json' -d '{"marks": 92}'
curl -X DELETE localhost:8000/students/1
curl -X DELETE 'localhost:8000/students?confirm=true'
```

## Deploying on Vercel

This folder is its own uv project, so Vercel can build it without the rest of the repo. Vercel installs the dependencies from `pyproject.toml` and finds the `app` in `main.py` by itself, so no extra Vercel config is needed.

1. In the Vercel project settings, set **Root Directory** to `tasks/fastapi_crud_pgms/student_ms`.
2. Use a MySQL database that Vercel can reach over the internet. `localhost` on Vercel is the serverless machine, not your computer.
3. Add `DB_HOST`, `DB_USER`, `DB_PASSWORD` and `DB_NAME` under **Settings → Environment Variables**, plus `DB_PORT` if the database isn't on MySQL's default port 3306. Hosted MySQL often isn't (Aiven, for example, shows its port under Connection information). `.env` is never deployed; the app reads these environment variables instead.

## Web UI

`static/index.html` is a single file with no build step and no outside dependencies. It calls the API from the same origin, so no CORS setup is needed.

- Add form with inline error messages, including errors returned by the server
- Find a student by id
- Paginated table with color-coded marks (green ≥ 75, amber ≥ 50, red below)
- Edit and delete dialogs; delete all requires typing `DELETE`
- Loading, empty and error screens, with a Retry button on the error screen
- Light and dark themes that follow your system setting; the layout works on phones

## Code layout

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app, startup (creates the table), routes, serving the web UI |
| `schemas.py` | Pydantic request and response models and their validation rules |
| `repository.py` | `StudentRepository`: all SQL; `get_repository()` opens one connection per request |
| `db_config.py` | Loads `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` and the optional `DB_PORT` (default 3306) from the nearest `.env` (this folder or any parent), or from environment variables |
| `static/index.html` | Web UI |
| `tests/test_api.py` | API tests |
| `tests/test_db_config.py` | Connection-settings tests, including `DB_PORT` |
| `pyproject.toml`, `uv.lock` | This app's dependencies and pytest config |

The modules import each other by plain name (`from schemas import ...`), because this folder is the project root, locally and on Vercel.

Each request gets its own MySQL connection, because mysql-connector connections aren't thread-safe and FastAPI runs these routes in a thread pool.

## Tests

The tests replace MySQL with an in-memory repository through FastAPI dependency overrides, so they need no database. Run them from this folder:

```bash
cd tasks/fastapi_crud_pgms/student_ms
uv run pytest
```

They cover creating, reading, updating and deleting students, pagination limits, validation errors, 404s, the delete-all confirmation, and serving the web UI.
