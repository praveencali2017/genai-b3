# Student Management System (FastAPI)

A REST API and web UI for managing student records, backed by MySQL. It rebuilds the menu-driven [student_management.py](../../database_pgms/student_management.py) as a web app.

Each student has an **id**, **name**, **age**, **course** and **marks**. You can add a student, list all students, get one by id, update their marks or course, delete one, or delete all.

## Running

Complete the setup in the [main README](../../../README.md#getting-started) first: run `uv sync`, then copy `.env.example` to `.env` and fill in your MySQL settings. Then start the server:

```bash
cd tasks/fastapi_crud_pgms
uv run fastapi dev student_ms/main.py
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
| `db_config.py` | Loads `DB_HOST`, `DB_USER`, `DB_PASSWORD` and `DB_NAME` from the repo-root `.env` |
| `static/index.html` | Web UI |
| `tests/test_api.py` | API tests |

Each request gets its own MySQL connection, because mysql-connector connections aren't thread-safe and FastAPI runs these routes in a thread pool.

## Tests

The tests replace MySQL with an in-memory repository through FastAPI dependency overrides, so they need no database. Run them from the repo root:

```bash
uv run pytest
```

They cover creating, reading, updating and deleting students, pagination limits, validation errors, 404s, the delete-all confirmation, and serving the web UI.
