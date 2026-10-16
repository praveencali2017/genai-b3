# Generative AI Course – Batch 3

My day-to-day notes, code, and exercises from the **Generative AI** course (Batch 3) offered by **MicroDegree**.

## About

This repository tracks my progress through the course. Class-day material lives in `dayN/` folders, and assignments live under `tasks/`: beginner Python exercises, a series of menu-driven MySQL management systems, and the same systems rebuilt as FastAPI REST APIs.

## Repository Structure

```
genai-b3/
├── README.md
├── pyproject.toml             # Project metadata and dependencies (managed with uv)
├── uv.lock
├── .env.example               # Template for local database settings
├── .github/workflows/
│   └── lint.yml               # Runs ruff on PRs that change tasks/
├── day1/
│   └── demo.py                # Day 1 session demo
├── src/genai_b3/              # Package entry point
└── tasks/
    ├── 25_basic_pgms/         # 25 beginner Python exercises (1_print_msg.py … 25_triangle_type.py)
    │   └── docs/              # Exercise sheet (PDF)
    ├── database_pgms/         # MySQL programs
    │   ├── db_config.py       # Loads DB settings from .env
    │   ├── basic-1.py         # Insert one employee record
    │   ├── basic2-crud.py     # Employee CRUD menu
    │   ├── student_management.py
    │   ├── library_management.py
    │   ├── product_management.py
    │   ├── hospital_management.py
    │   ├── hotel_booking.py
    │   ├── restaurant_orders.py
    │   ├── atm_account.py
    │   └── docs/              # Task sheet (DOCX)
    └── fastapi_crud_pgms/     # FastAPI + MySQL REST APIs
        ├── docs/              # Task sheet (DOCX)
        └── student_ms/        # Student Management API (see its README)
            ├── README.md      # App docs: API, validation, UI, tests
            ├── main.py        # App and routes
            ├── schemas.py     # Pydantic models
            ├── repository.py  # MySQL queries
            ├── db_config.py   # Loads DB settings from .env
            ├── static/index.html  # Web UI served at /
            └── tests/         # pytest API tests (no MySQL needed)
```

## Progress Log

| # | Task | Location |
|---|------|----------|
| 1 | Day 1 demo | [day1](day1/) |
| 2 | 25 basic Python programs | [tasks/25_basic_pgms](tasks/25_basic_pgms/) |
| 3 | MySQL basics: insert and employee CRUD | [basic-1.py](tasks/database_pgms/basic-1.py), [basic2-crud.py](tasks/database_pgms/basic2-crud.py) |
| 4 | Student Management System | [student_management.py](tasks/database_pgms/student_management.py) |
| 5 | Library Management System: add, issue/return, update, delete books | [library_management.py](tasks/database_pgms/library_management.py) |
| 6 | Product Inventory Management: add, re-price, restock, sell, delete products | [product_management.py](tasks/database_pgms/product_management.py) |
| 7 | Hospital Patient Management: register, search, update diagnosis/doctor, discharge, delete patients | [hospital_management.py](tasks/database_pgms/hospital_management.py) |
| 8 | Hotel Room Booking System: book, search, change room, check in/out, cancel, delete all bookings | [hotel_booking.py](tasks/database_pgms/hotel_booking.py) |
| 9 | Restaurant Order Management: create, search, update quantity/bill, cancel, delete all orders | [restaurant_orders.py](tasks/database_pgms/restaurant_orders.py) |
| 10 | ATM Account Management: create, check balance, deposit, withdraw, change PIN, search, close accounts | [atm_account.py](tasks/database_pgms/atm_account.py) |
| 11 | FastAPI Student Management API + web UI: add, list (paginated), get, update marks/course, delete, delete all | [student_ms](tasks/fastapi_crud_pgms/student_ms/) ([README](tasks/fastapi_crud_pgms/student_ms/README.md)) |

_Add a row after each task._

## Getting Started

Requires Python 3.12+ and a running MySQL server.

1. Clone the repository:
   ```bash
   git clone <repo-url>
   cd genai-b3
   ```
2. Install dependencies (`mysql-connector-python`, `python-dotenv`, `fastapi[standard]`, plus `ruff` and `pytest` for development):
   ```bash
   uv sync
   ```
   Or without uv:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install mysql-connector-python python-dotenv "fastapi[standard]" pytest
   ```
3. Set up database settings:
   ```bash
   cp .env.example .env   # then fill in DB_HOST, DB_USER, DB_PASSWORD, DB_NAME
   ```
   The database named in `DB_NAME` must already exist. Keep `.env` local and never commit it.

## Running the Programs

Basic exercises run directly:

```bash
python tasks/25_basic_pgms/7_even_or_odd.py
```

Database programs import `db_config`, so run them from their own folder:

```bash
cd tasks/database_pgms
python hospital_management.py
```

Each management system creates its table (`student`, `book`, `product`, `patient`, `booking`, `restaurant_order`, `account`) on first run, then shows a numbered menu.

FastAPI apps run from `tasks/fastapi_crud_pgms` with the dev server:

```bash
cd tasks/fastapi_crud_pgms
uv run fastapi dev student_ms/main.py
```

Then open http://127.0.0.1:8000/ for the web UI or http://127.0.0.1:8000/docs for the API. The [Student Management README](tasks/fastapi_crud_pgms/student_ms/README.md) covers the endpoints, validation rules, pagination, the web UI and the code layout.

## Testing

The FastAPI tests swap MySQL for an in-memory repository, so they run without a database:

```bash
uv run pytest
```

## Linting

`ruff` is pinned as a dev dependency, and every pull request that changes anything under `tasks/` runs it in GitHub Actions. Run the same check locally before pushing:

```bash
uv run ruff check tasks/
```

Add `--fix` to apply automatic fixes (for example, import ordering).

## Acknowledgements

Course content and instruction by **MicroDegree**. This repository holds my personal learning notes and practice work.
