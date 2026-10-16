# Hotel Room Booking System (FastAPI)

A REST API and web UI for hotel room bookings, backed by MySQL.

Each booking has an **id**, **customer name**, **room number**, **room type**, **amount** and a **status**. You can create a booking, list all bookings, get one by id, change its room, check the guest in, check them out, cancel the booking, or delete all bookings.

## Running

Complete the setup in the [main README](../../../README.md#getting-started) first: run `uv sync`, then copy `.env.example` to `.env` and fill in your MySQL settings. Then start the server:

```bash
cd tasks/fastapi_crud_pgms
uv run fastapi dev hotel_room_bs/main.py
```

| URL | What's there |
|-----|--------------|
| http://127.0.0.1:8000/ | Web UI covering every operation |
| http://127.0.0.1:8000/docs | Interactive API docs (Swagger) |

The app needs only an empty MySQL database. On startup it runs `CREATE TABLE IF NOT EXISTS hotel_booking`, so the table is created on the first run and left alone after that. It doesn't depend on any other program in this repo. If `.env` is missing a setting, or MySQL can't be reached, the server fails at startup and the error says what's wrong.

`.env` is found by searching this folder and then each parent folder, so the server finds it wherever you start it from. Variables already set in the environment take precedence.

## Booking lifecycle

```
Booked ──check in──▶ Checked In ──check out──▶ Checked Out
   │
   └────cancel─────▶ Cancelled
```

- Every new booking starts as **Booked**. Clients can't set the status directly; it changes only through check in, check out and cancel.
- **Cancel** works only before check-in. The booking is kept with status Cancelled, not deleted.
- **Change room** works while the booking is Booked or Checked In. Checked Out and Cancelled bookings are kept unchanged as history.
- A change the current status doesn't allow returns `409 Conflict` with the reason, for example `"Cannot check out: the guest has not checked in yet."`.

Each change checks the current status inside the same `UPDATE` statement (`... WHERE id = %s AND status = %s`). If two requests try to check in the same booking at the same time, only one succeeds.

## API

| Method | Path | Success | Errors |
|--------|------|---------|--------|
| `POST` | `/bookings` | `201` with the new booking | `422` invalid fields |
| `GET` | `/bookings?limit=10&offset=0` | `200` with one page of bookings | `422` bad `limit`/`offset` |
| `GET` | `/bookings/{id}` | `200` with the booking | `404` |
| `PATCH` | `/bookings/{id}/room` | `200` with the updated booking | `404`, `409`, `422` |
| `POST` | `/bookings/{id}/check-in` | `200` with the updated booking | `404`, `409` |
| `POST` | `/bookings/{id}/check-out` | `200` with the updated booking | `404`, `409` |
| `POST` | `/bookings/{id}/cancel` | `200` with the updated booking | `404`, `409` |
| `DELETE` | `/bookings?confirm=true` | `200` with `{"deleted": n}` | `400` without `confirm=true` |

There's no endpoint for deleting one booking. Cancel it instead, which keeps the record.

### Validation

| Field | Rule |
|-------|------|
| `customer_name` | Required, 1–100 characters; surrounding whitespace is trimmed |
| `room_number` | Whole number, 1 or more |
| `room_type` | One of `Single`, `Double`, `Deluxe`, `Suite` |
| `amount` | Greater than 0, at most 99,999,999.99 (the column is `DECIMAL(10, 2)`) |

`PATCH /bookings/{id}/room` accepts `room_number`, `room_type`, or both. A request with neither returns `422`. Sending the room the booking already has returns `200` and changes nothing.

The table enforces the same rules (`ENUM` columns and `CHECK` constraints), so the database rejects bad rows even if a request bypasses the API's validation.

### Pagination

`GET /bookings` returns 10 bookings per page by default, sorted by id:

```json
{
  "items": [
    {"id": 1, "customer_name": "Asha Rao", "room_number": 101,
     "room_type": "Deluxe", "amount": 4500.0, "status": "Booked"}
  ],
  "total": 34,
  "limit": 10,
  "offset": 0
}
```

`limit` can be from 1 to 100, and `offset` must be 0 or more. To get the next page, add `limit` to `offset`. If `offset + limit` is equal to or greater than `total`, this is the last page.

### Examples

```bash
curl -X POST localhost:8000/bookings -H 'content-type: application/json' \
     -d '{"customer_name": "Asha Rao", "room_number": 101, "room_type": "Deluxe", "amount": 4500}'
curl 'localhost:8000/bookings?limit=5&offset=5'
curl localhost:8000/bookings/1
curl -X PATCH localhost:8000/bookings/1/room -H 'content-type: application/json' \
     -d '{"room_number": 204, "room_type": "Suite"}'
curl -X POST localhost:8000/bookings/1/check-in
curl -X POST localhost:8000/bookings/1/check-out
curl -X POST localhost:8000/bookings/2/cancel
curl -X DELETE 'localhost:8000/bookings?confirm=true'
```

## Web UI

`static/index.html` is a single file with no build step and no outside dependencies. It calls the API from the same origin, so no CORS setup is needed.

- New booking form with inline error messages, including errors returned by the server
- Find a booking by id
- Paginated table with a color-coded status badge
- Each row shows only the actions its status allows: check in, check out, change room, cancel
- Change room dialog; cancel asks for confirmation; delete all requires typing `DELETE`
- If a booking changed since the page loaded, the `409` reason is shown and the row refreshes
- Loading, empty and error screens, with a Retry button on the error screen
- Light and dark themes that follow your system setting; the layout works on phones

## Code layout

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app, startup (creates the table), routes, serving the web UI |
| `schemas.py` | Pydantic models, `RoomType` and `BookingStatus`, validation rules |
| `lifecycle.py` | Which status changes are allowed, and the message when one isn't. No I/O |
| `repository.py` | `BookingRepository`: all SQL, including `CREATE TABLE`; `get_repository()` opens one connection per request |
| `db_config.py` | Loads `DB_HOST`, `DB_USER`, `DB_PASSWORD` and `DB_NAME` from the nearest `.env` |
| `static/index.html` | Web UI |
| `tests/test_api.py` | API tests |

The table's `ENUM` values are generated from `RoomType` and `BookingStatus`, so the database and the API always accept the same values.

Each request gets its own MySQL connection, because mysql-connector connections aren't thread-safe and FastAPI runs these routes in a thread pool.

## Tests

The tests replace MySQL with an in-memory repository through FastAPI dependency overrides, so they need no database. Run them from the repo root:

```bash
uv run pytest
```

They cover creating and reading bookings, validation errors, pagination limits, changing rooms (including on finished bookings), every allowed and disallowed status change, 404s, the delete-all confirmation, and serving the web UI.
